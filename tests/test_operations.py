"""Operational evidence is tenant/admin scoped; recovery integrity is mandatory."""

import json
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException
from psycopg_pool import AsyncConnectionPool

from atlas import db, operations
from atlas.config import settings
from atlas.db import transaction, verify_application_role


@pytest.mark.integration
async def test_application_role_guard_rejects_admin(database, monkeypatch):
    await verify_application_role()
    unsafe = AsyncConnectionPool(
        settings.database_admin_url, open=False, kwargs={"row_factory": psycopg.rows.dict_row}
    )
    await unsafe.open(wait=True)
    monkeypatch.setattr(db, "pool", unsafe)
    try:
        with pytest.raises(RuntimeError, match="unprivileged"):
            await verify_application_role()
    finally:
        await unsafe.close()


@pytest.mark.integration
async def test_application_role_guard_requires_forced_tenant_rls(database):
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute("ALTER TABLE atlas.tenants NO FORCE ROW LEVEL SECURITY")
    try:
        with pytest.raises(RuntimeError, match="forced RLS"):
            await verify_application_role()
    finally:
        with psycopg.connect(settings.database_admin_url) as conn:
            conn.execute("ALTER TABLE atlas.tenants FORCE ROW LEVEL SECURITY")
    await verify_application_role()


@pytest.mark.integration
async def test_operations_evidence_is_admin_and_tenant_scoped(database, tenants, monkeypatch):
    first, second = tenants.ids
    alert_id = uuid4()
    with psycopg.connect(settings.database_admin_url) as conn:
        for tenant in (first, second):
            conn.execute(
                "INSERT INTO atlas.operational_alerts(tenant_id,id,kind,severity,message) VALUES(%s,%s,'test','warning',%s)",
                (tenant, alert_id, "Own alert" if tenant == first else "Private other alert"),
            )
    identity = tenants.identities[first]

    async def healthy(*args):
        return {"database": {"ready": True}, "redis": {"ready": True}}

    async def scanner():
        return {"engine": "test"}

    from atlas import upload_safety

    monkeypatch.setattr(operations, "system", healthy)
    monkeypatch.setattr(upload_safety, "scanner_status", scanner)
    view = await operations.overview(identity)
    assert [item["message"] for item in view["alerts"]] == ["Own alert"]
    assert view["platform_operator"] is False
    assert (await operations.acknowledge_alert(alert_id, identity))["acknowledged"]
    with pytest.raises(HTTPException) as denied:
        await operations.overview(replace(identity, role="viewer"))
    assert denied.value.status_code == 403
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute("UPDATE atlas.memberships SET role='viewer' WHERE tenant_id=%s", (first,))
    # RLS also blocks reads when a caller still holds an old owner identity object.
    async with transaction(first) as conn:
        assert not await (await conn.execute("SELECT * FROM atlas.operational_alerts")).fetchall()


@pytest.mark.integration
async def test_operational_alerts_deduplicate_and_resolve(worker_database, tenants):
    async with transaction(tenants.ids[0]) as conn:
        await conn.execute("SELECT atlas.refresh_operational_alerts(false,false)")
        await conn.execute("SELECT atlas.refresh_operational_alerts(false,false)")
        rows = await (
            await conn.execute(
                "SELECT * FROM atlas.operational_alerts WHERE kind='queue_unavailable'"
            )
        ).fetchall()
        assert len(rows) == 1 and rows[0]["resolved_at"] is None
        await conn.execute("SELECT atlas.refresh_operational_alerts(true,true)")
        row = await (
            await conn.execute(
                "SELECT resolved_at FROM atlas.operational_alerts WHERE kind='queue_unavailable'"
            )
        ).fetchone()
        assert row["resolved_at"] is not None


@pytest.mark.integration
async def test_job_metadata_obeys_document_permissions(database, tenants):
    from atlas.serving import enqueue_conn

    tenant = tenants.ids[0]
    async with transaction(tenant) as conn:
        document = await (await conn.execute("SELECT id FROM atlas.documents LIMIT 1")).fetchone()
        job = await enqueue_conn(conn, tenant, document["id"])
        assert await (
            await conn.execute("SELECT id FROM atlas.outbox WHERE job_id=%s", (job["job_id"],))
        ).fetchone()
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute("UPDATE atlas.documents SET restricted=true WHERE tenant_id=%s", (tenant,))
        conn.execute("UPDATE atlas.memberships SET role='viewer' WHERE tenant_id=%s", (tenant,))
    async with transaction(tenant) as conn:
        assert not await (await conn.execute("SELECT id FROM atlas.ingestion_jobs")).fetchall()
        assert not await (await conn.execute("SELECT id FROM atlas.outbox")).fetchall()


@pytest.mark.integration
async def test_job_quota_counts_jobs_hidden_from_editor(database, tenants):
    from atlas.serving import enqueue_conn

    tenant = tenants.ids[0]
    async with transaction(tenant) as conn:
        private = await (await conn.execute("SELECT id FROM atlas.documents LIMIT 1")).fetchone()
        await enqueue_conn(conn, tenant, private["id"])
        visible = await (
            await conn.execute(
                "INSERT INTO atlas.documents(tenant_id,id,title,source_key,content_hash,content) VALUES(%s,%s,'Visible','visible','visible','visible') RETURNING id",
                (tenant, uuid4()),
            )
        ).fetchone()
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "INSERT INTO atlas.tenant_limits(tenant_id,max_pending_jobs) VALUES(%s,1) ON CONFLICT(tenant_id) DO UPDATE SET max_pending_jobs=1",
            (tenant,),
        )
        conn.execute(
            "UPDATE atlas.documents SET restricted=true WHERE tenant_id=%s AND id=%s",
            (tenant, private["id"]),
        )
        conn.execute("UPDATE atlas.memberships SET role='editor' WHERE tenant_id=%s", (tenant,))
    async with transaction(tenant) as conn:
        assert not await (await conn.execute("SELECT id FROM atlas.ingestion_jobs")).fetchall()
        with pytest.raises(HTTPException) as full:
            await enqueue_conn(conn, tenant, visible["id"])
        assert full.value.status_code == 429


def test_restore_requires_complete_integrity_evidence(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from restore_backup import verify_bundle

    (tmp_path / "manifest.json").write_text(json.dumps({"uploads": 0, "upload_sha256": {}}))
    with pytest.raises(ValueError, match="integrity hashes"):
        verify_bundle(tmp_path)


def test_release_selection_requires_passed_checks_and_pinned_image(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    # Avoid importing another installed package named release.
    sys.modules.pop("release", None)
    from release import REQUIRED_CHECKS, deployment_command

    with pytest.raises(ValueError, match="checks passed"):
        deployment_command({"status": "blocked"}, Path("private.env"))
    with pytest.raises(ValueError, match="pinned"):
        deployment_command(
            {
                "status": "accepted",
                "checks": dict.fromkeys(REQUIRED_CHECKS, True),
                "image": "atlas:latest",
            },
            Path("private.env"),
        )


def test_release_fingerprint_tracks_build_and_security_inputs(tmp_path, monkeypatch):
    import subprocess

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    import release

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(release, "ROOT", tmp_path)
    paths = ("scripts/release.py", "tests/permission.py", "deploy/compose.yaml", "pyproject.toml")
    for name in (*paths, ".local/private.json", ".env"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("initial")
    files, original = release.source_manifest()
    assert set(files) == set(paths)
    for name in paths:
        path = tmp_path / name
        path.write_text("changed")
        assert release.source_manifest()[1] != original
        path.write_text("initial")
    (tmp_path / ".env").write_text("private-change")
    assert release.source_manifest()[1] == original
