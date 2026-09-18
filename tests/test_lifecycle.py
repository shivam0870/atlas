"""Real database retention, notifications, principal accounting and request protection."""

from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi import HTTPException
from test_resource_access import resource_workspace as resource_workspace

from atlas.auth import bind_identity
from atlas.config import settings
from atlas.serving import limits, reserve, settle


@pytest.mark.integration
async def test_member_budget_reserves_and_settles_once(database, resource_workspace):
    owner = resource_workspace.identities[0]
    bind_identity(owner)
    await limits(owner.tenant_id)
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "UPDATE atlas.memberships SET monthly_tokens=100 WHERE tenant_id=%s AND user_id=%s",
            (owner.tenant_id, owner.user_id),
        )
    operation = uuid4()
    try:
        await reserve(owner.tenant_id, operation, 70)
        await reserve(owner.tenant_id, operation, 70)
        with pytest.raises(HTTPException) as rejected:
            await reserve(owner.tenant_id, uuid4(), 40)
        assert rejected.value.status_code == 402
        await settle(owner.tenant_id, operation, 20)
        await settle(owner.tenant_id, operation, 20)
        await reserve(owner.tenant_id, uuid4(), 80)
        with psycopg.connect(settings.database_admin_url) as conn:
            used, reserved = conn.execute(
                "SELECT used_tokens,reserved_tokens FROM atlas.budget_periods WHERE tenant_id=%s",
                (owner.tenant_id,),
            ).fetchone()
        assert (used, reserved) == (20, 80)
    finally:
        with psycopg.connect(settings.database_admin_url) as conn:
            for table in ["reservations", "budget_periods", "tenant_limits"]:
                conn.execute(f"DELETE FROM atlas.{table} WHERE tenant_id=%s", (owner.tenant_id,))


@pytest.mark.integration
async def test_retention_and_notification_delivery_are_durable_and_deduplicated(resource_workspace):
    ws = resource_workspace
    document, job = uuid4(), uuid4()
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "INSERT INTO atlas.documents(tenant_id,id,title,source_key,content_hash,content,owner_user_id,review_due_at) VALUES(%s,%s,'Private title','retention','hash','Private body',%s,now()-interval '1 day')",
            (ws.tenant, document, ws.identities[0].user_id),
        )
        conn.execute(
            "INSERT INTO atlas.ingestion_jobs(tenant_id,id,document_id,idempotency_key,status) VALUES(%s,%s,%s,%s,'completed')",
            (ws.tenant, job, document, str(job)),
        )
    with psycopg.connect(settings.worker_database_url) as conn:
        conn.execute("SELECT atlas.deliver_maintenance_notifications()")
        conn.execute("SELECT atlas.deliver_maintenance_notifications()")
    with psycopg.connect(settings.database_admin_url) as conn:
        rows = conn.execute(
            "SELECT title,body FROM atlas.notifications WHERE tenant_id=%s", (ws.tenant,)
        ).fetchall()
        assert len(rows) == 2
        assert all("Private" not in str(row) for row in rows)
        conn.execute("UPDATE atlas.tenants SET status='suspended' WHERE id=%s", (ws.tenant,))
        queued = conn.execute(
            "SELECT id,due_at>now()+interval '29 days' FROM atlas.purge_jobs WHERE target_id=%s",
            (ws.tenant,),
        ).fetchone()
        assert queued and queued[1]
        conn.execute(
            "UPDATE atlas.purge_jobs SET due_at=now()-interval '1 second' WHERE id=%s", (queued[0],)
        )
    with psycopg.connect(settings.worker_database_url) as conn:
        result = conn.execute("SELECT * FROM atlas.process_retention()").fetchone()
        assert result[0] == queued[0]
    with psycopg.connect(settings.database_admin_url) as conn:
        assert (
            conn.execute("SELECT count(*) FROM atlas.tenants WHERE id=%s", (ws.tenant,)).fetchone()[
                0
            ]
            == 0
        )
    with psycopg.connect(settings.worker_database_url) as conn:
        # The file-cleanup stage survives a worker restart and is retried without redoing SQL erasure.
        assert (
            conn.execute("SELECT job_id FROM atlas.process_retention()").fetchone()[0] == queued[0]
        )
        conn.execute("SELECT atlas.finish_retention(%s)", (queued[0],))
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute("DELETE FROM atlas.purge_jobs WHERE id=%s", (queued[0],))


async def test_actual_main_rejects_cross_origin_and_serves_deep_links():
    from atlas.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url=settings.app_url
    ) as client:
        rejected = await client.post(
            "/api/auth/login",
            headers={"Origin": "https://foreign.invalid", "X-Atlas-Client": "console"},
            json={},
        )
        assert rejected.status_code == 403
        assert (await client.post("/api/auth/login", json={})).status_code == 403
        assert (await client.get("/login")).status_code == 200
        assert (await client.get("/o/unknown/library")).status_code == 200
        assert (await client.get("/api/nonexistent")).status_code == 404


@pytest.mark.integration
async def test_document_file_erasure_remains_queued_after_database_commit(resource_workspace):
    ws = resource_workspace
    document, version, storage_key = uuid4(), uuid4(), uuid4()
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "INSERT INTO atlas.documents(tenant_id,id,title,source_key,content_hash,content) VALUES(%s,%s,'File cleanup','file-cleanup','hash','body')",
            (ws.tenant, document),
        )
        conn.execute(
            "INSERT INTO atlas.document_versions(tenant_id,id,document_id,number,title,content,content_hash,storage_key) VALUES(%s,%s,%s,1,'File cleanup','body','hash',%s)",
            (ws.tenant, version, document, str(storage_key)),
        )
        conn.execute(
            "DELETE FROM atlas.documents WHERE tenant_id=%s AND id=%s", (ws.tenant, document)
        )
    with psycopg.connect(settings.database_admin_url) as conn:
        row = conn.execute(
            "SELECT id,status,storage_keys FROM atlas.purge_jobs WHERE kind='document' AND target_id=%s",
            (document,),
        ).fetchone()
        assert row and row[1:] == ("files", [str(storage_key)])
        conn.execute("DELETE FROM atlas.purge_jobs WHERE id=%s", (row[0],))
