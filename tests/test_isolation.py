import secrets
from uuid import uuid4

import httpx
import psycopg
import pytest

from atlas.config import settings
from atlas.db import access_context, transaction
from atlas.main import app


@pytest.mark.integration
async def test_rls_without_application_filter(database, tenants):
    ids, _ = tenants
    for tenant in ids:
        tenants.use(tenant)
        async with transaction(tenant) as conn:
            rows = await (await conn.execute("SELECT tenant_id FROM atlas.documents")).fetchall()
            assert rows and all(r["tenant_id"] == tenant for r in rows)
            roles = await (
                await conn.execute(
                    "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
                )
            ).fetchone()
            assert not roles["rolsuper"] and not roles["rolbypassrls"]
    async with transaction() as conn:
        assert await (await conn.execute("SELECT * FROM atlas.documents")).fetchall() == []
    access_context.set(None)
    async with transaction(ids[0]) as conn:
        assert await (await conn.execute("SELECT * FROM atlas.documents")).fetchall() == []


@pytest.mark.integration
async def test_rls_blocks_cross_tenant_write(database, tenants):
    ids, _ = tenants
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with transaction(ids[0]) as conn:
            await conn.execute(
                "INSERT INTO atlas.documents(tenant_id,id,title,source_key,content_hash,content) VALUES(%s,%s,'bad','bad','bad','bad')",
                (ids[1], uuid4()),
            )


@pytest.mark.integration
async def test_authentication(database, tenants):
    ids, keys = tenants
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        for value in [None, "bad", "atl_" + secrets.token_urlsafe(32)]:
            response = await client.get(
                "/api/me", headers={"Authorization": f"Bearer {value}"} if value else {}
            )
            assert response.status_code == 401
        response = await client.get("/api/me", headers={"Authorization": f"Bearer {keys[0]}"})
        assert response.json()["tenant_id"] == str(ids[0])
        with psycopg.connect(settings.database_admin_url) as conn:
            conn.execute("UPDATE atlas.api_keys SET revoked_at=now() WHERE tenant_id=%s", (ids[0],))
        assert (
            await client.get("/api/me", headers={"Authorization": f"Bearer {keys[0]}"})
        ).status_code == 401
