import secrets
from dataclasses import dataclass
from uuid import UUID, uuid4

import psycopg
import pytest_asyncio
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from atlas.auth import Identity, digest
from atlas.config import settings
from atlas.db import access_context, bind_identity, pool, transaction_pool


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def database():
    await pool.open(wait=True)
    yield
    await pool.close()


class Secret(str):
    def __repr__(self):
        return "<redacted test credential>"


@dataclass
class TenantFixtures:
    ids: list[UUID]
    keys: list[Secret]
    identities: dict[UUID, Identity]

    def __iter__(self):
        return iter((self.ids, self.keys))

    def __getitem__(self, index):
        return (self.ids, self.keys)[index]

    def use(self, tenant=None):
        """Explicit real user/session principal for direct application-role SQL."""
        return bind_identity(self.identities[tenant or self.ids[0]])


@pytest_asyncio.fixture
async def tenants():
    ids = [uuid4(), uuid4()]
    keys = [Secret("atl_" + secrets.token_urlsafe(32)) for _ in ids]
    identities = {}
    people = []
    with psycopg.connect(settings.database_admin_url) as conn:
        for tenant, key in zip(ids, keys, strict=True):
            conn.execute(
                "INSERT INTO atlas.tenants(id,slug,name) VALUES(%s,%s,%s)",
                (tenant, str(tenant), "Isolation test"),
            )
            conn.execute(
                "INSERT INTO atlas.api_keys(id,tenant_id,digest,prefix,scopes) VALUES(%s,%s,%s,%s,%s)",
                (uuid4(), tenant, digest(key), key[:10], ["read"]),
            )
            user, session = uuid4(), uuid4()
            people.append(user)
            conn.execute(
                "INSERT INTO atlas.users(id,email,name,password_hash,email_verified_at) VALUES(%s,%s,'Regression owner','unused-fixture-password',now())",
                (user, f"{user}@example.test"),
            )
            conn.execute(
                "INSERT INTO atlas.user_sessions(id,user_id,digest,expires_at) VALUES(%s,%s,%s,now()+interval '1 hour')",
                (session, user, digest(secrets.token_urlsafe(32))),
            )
            conn.execute(
                "INSERT INTO atlas.memberships(tenant_id,user_id,role) VALUES(%s,%s,'owner')",
                (tenant, user),
            )
            identities[tenant] = Identity(
                tenant,
                session,
                "Isolation test",
                ["read", "write", "query", "admin"],
                user_id=user,
                role="owner",
                principal_kind="user",
                principal_id=user,
                session_id=session,
            )
            conn.execute(
                "INSERT INTO atlas.documents(tenant_id,id,title,source_key,content_hash,content) VALUES(%s,%s,'private','private','hash','private')",
                (tenant, uuid4()),
            )
    fixture = TenantFixtures(ids, keys, identities)
    fixture.use()
    yield fixture
    access_context.set(None)
    with psycopg.connect(settings.database_admin_url) as conn:
        for tenant in ids:
            conn.execute(
                "UPDATE atlas.documents SET current_version_id=NULL,pending_version_id=NULL WHERE tenant_id=%s",
                (tenant,),
            )
            for table in [
                "bookmarks",
                "feedback",
                "messages",
                "conversations",
                "queries",
                "outbox",
                "ingestion_jobs",
                "evaluation_jobs",
                "eval_runs",
                "eval_labels",
                "reservations",
                "budget_periods",
                "tenant_limits",
                "entities",
                "chunk_terms",
                "embedding_cache",
                "embeddings",
                "chunks",
                "document_versions",
                "resource_grants",
                "access_requests",
                "notifications",
                "documents",
                "collections",
                "usage_ledger",
                "team_members",
                "teams",
                "invitations",
                "memberships",
                "api_keys",
                "service_accounts",
                "spaces",
            ]:
                conn.execute(f"DELETE FROM atlas.{table} WHERE tenant_id=%s", (tenant,))
            # Mutation triggers emit audit rows during cleanup; remove them last.
            conn.execute("DELETE FROM atlas.audit_events WHERE tenant_id=%s", (tenant,))
            conn.execute("DELETE FROM atlas.tenants WHERE id=%s", (tenant,))
        conn.execute("DELETE FROM atlas.users WHERE id=ANY(%s)", (people,))


@pytest_asyncio.fixture
async def worker_database(database):
    """Real worker credentials for tests that execute indexing/queue maintenance directly."""
    worker_pool = AsyncConnectionPool(
        settings.worker_database_url,
        open=False,
        min_size=1,
        max_size=4,
        kwargs={"row_factory": dict_row},
    )
    await worker_pool.open(wait=True)
    token = transaction_pool.set(worker_pool)
    try:
        yield
    finally:
        transaction_pool.reset(token)
        await worker_pool.close()
