from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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


@asynccontextmanager
async def transaction(
    tenant_id: UUID | str | None = None,
) -> AsyncIterator[AsyncConnection[dict[str, Any]]]:
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            "SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id) if tenant_id else "",)
        )
        await conn.execute("SET LOCAL statement_timeout = '15s'")
        yield conn
