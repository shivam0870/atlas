from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from atlas.config import settings

pool: AsyncConnectionPool[AsyncConnection[dict[str, Any]]] = AsyncConnectionPool(
    settings.database_url,
    open=False,
    min_size=1,
    max_size=8,
    kwargs={"row_factory": dict_row},
    timeout=10,
)

identity_pool: AsyncConnectionPool[AsyncConnection[dict[str, Any]]] = AsyncConnectionPool(
    settings.identity_database_url,
    open=False,
    min_size=1,
    max_size=4,
    kwargs={"row_factory": dict_row},
    timeout=10,
)
access_context: ContextVar[Any] = ContextVar("atlas_access_identity", default=None)
application_pool: AsyncConnectionPool[AsyncConnection[dict[str, Any]]] = AsyncConnectionPool(
    settings.database_url,
    open=False,
    min_size=1,
    max_size=4,
    kwargs={"row_factory": dict_row},
    timeout=10,
)
transaction_pool: ContextVar[Any] = ContextVar("atlas_transaction_pool", default=None)


@asynccontextmanager
async def application_transactions():
    """Worker evaluation uses application privileges, never worker access to all tenant evidence."""
    if application_pool.closed:
        await application_pool.open(wait=True)
    token = transaction_pool.set(application_pool)
    try:
        yield
    finally:
        transaction_pool.reset(token)


def bind_identity(identity):
    return access_context.set(identity)


@asynccontextmanager
async def identity_transaction():
    if identity_pool.closed:
        await identity_pool.open(wait=True)
    async with identity_pool.connection() as conn, conn.transaction():
        await conn.execute("SET LOCAL statement_timeout = '15s'")
        actor = access_context.get()
        await conn.execute(
            "SELECT set_config('app.principal_id',%s,true)",
            (str(actor.principal_id or "") if actor else "",),
        )
        await conn.execute(
            "SELECT set_config('app.principal_kind',%s,true)",
            (actor.principal_kind if actor else "",),
        )
        yield conn


@asynccontextmanager
async def transaction(
    tenant_id: UUID | str | None = None,
) -> AsyncIterator[AsyncConnection[dict[str, Any]]]:
    selected_pool = transaction_pool.get() or pool
    async with selected_pool.connection() as conn, conn.transaction():
        await conn.execute(
            "SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id) if tenant_id else "",)
        )
        identity = access_context.get()
        matches = identity is not None and str(identity.tenant_id) == str(tenant_id)
        values = {
            "app.principal_id": str(identity.principal_id or "") if matches else "",
            "app.principal_kind": identity.principal_kind if matches else "",
            "app.key_id": str(identity.key_id or "") if matches else "",
            "app.session_id": str(identity.session_id or "") if matches else "",
            "app.session_idle_seconds": str(settings.session_idle_seconds),
        }
        for name, value in values.items():
            await conn.execute("SELECT set_config(%s,%s,true)", (name, value))
        await conn.execute("SET LOCAL statement_timeout = '15s'")
        await conn.execute("SET LOCAL jit = off")
        yield conn
