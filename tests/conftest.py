import secrets
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio

from atlas.auth import digest
from atlas.config import settings
from atlas.db import pool


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def database():
    await pool.open(wait=True)
    yield
    await pool.close()


class Secret(str):
    def __repr__(self):
        return "<redacted test credential>"


@pytest.fixture
def tenants():
    ids = [uuid4(), uuid4()]
    keys = [Secret("atl_" + secrets.token_urlsafe(32)) for _ in ids]
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
            conn.execute(
                "INSERT INTO atlas.documents(tenant_id,id,title,source_key,content_hash,content) VALUES(%s,%s,'private','private','hash','private')",
                (tenant, uuid4()),
            )
    yield ids, keys
    with psycopg.connect(settings.database_admin_url) as conn:
        for tenant in ids:
            for table in [
                "outbox",
                "ingestion_jobs",
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
                "queries",
                "collections",
                "usage_ledger",
                "documents",
            ]:
                conn.execute(f"DELETE FROM atlas.{table} WHERE tenant_id=%s", (tenant,))
            conn.execute("DELETE FROM atlas.api_keys WHERE tenant_id=%s", (tenant,))
            conn.execute("DELETE FROM atlas.tenants WHERE id=%s", (tenant,))
