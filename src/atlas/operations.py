"""Metadata-only tenant operations. Host recovery remains an operator-only CLI action."""

from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from atlas import db
from atlas.administration import audit, manager, system
from atlas.auth import Identity, authenticate
from atlas.config import settings
from atlas.db import transaction
from atlas.serving import enqueue_conn, redis

router = APIRouter(prefix="/api/operations", tags=["operations"])


@router.get("")
async def overview(identity: Identity = Depends(authenticate)):
    manager(identity)
    health = await system(identity)
    from atlas.upload_safety import scanner_status

    try:
        health["scanner"] = {"ready": True, **await scanner_status()}
    except Exception:
        health["scanner"] = {"ready": False}
    async with transaction(identity.tenant_id) as conn:
        queue = await (
            await conn.execute(
                """SELECT count(*) FILTER(WHERE status='queued') queued,
                count(*) FILTER(WHERE status='running') running,
                count(*) FILTER(WHERE status='retry') retry,
                count(*) FILTER(WHERE status='dead') dead
                FROM atlas.ingestion_jobs WHERE tenant_id=%s""",
                (identity.tenant_id,),
            )
        ).fetchone()
        readiness = {
            name: value
            for name, value in health.items()
            if name != "redis" and isinstance(value, dict) and "ready" in value
        }
        readiness["queue"] = health["redis"]
        result: dict[str, Any] = {"health": readiness, "queue": queue}
        for key, table in (
            ("alerts", "operational_alerts"),
            ("backups", "recovery_runs"),
            ("releases", "release_records"),
        ):
            result[key] = await (
                await conn.execute(
                    f"SELECT * FROM atlas.{table} WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 50",
                    (identity.tenant_id,),
                )
            ).fetchall()
    # Being a company owner never grants access to the host, all-tenant backups, or secrets.
    result["platform_operator"] = False
    return result


@router.get("/jobs")
async def jobs(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: Identity = Depends(authenticate),
):
    manager(identity)
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                """SELECT j.id,j.document_id,d.title,j.status,j.attempts,j.error_code,
                j.created_at,j.updated_at,j.lease_until,count(*) OVER() total
                FROM atlas.ingestion_jobs j JOIN atlas.documents d
                ON d.tenant_id=j.tenant_id AND d.id=j.document_id
                WHERE j.tenant_id=%s ORDER BY j.updated_at DESC,j.id LIMIT %s OFFSET %s""",
                (identity.tenant_id, limit, offset),
            )
        ).fetchall()
    return {"items": rows, "total": rows[0]["total"] if rows else 0}


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: UUID, identity: Identity = Depends(authenticate)):
    manager(identity)
    async with transaction(identity.tenant_id) as conn:
        job = await (
            await conn.execute(
                "SELECT * FROM atlas.ingestion_jobs WHERE tenant_id=%s AND id=%s FOR UPDATE",
                (identity.tenant_id, job_id),
            )
        ).fetchone()
        if not job:
            raise HTTPException(404, "Job is unavailable")
        if job["status"] not in {"dead", "cancelled"}:
            raise HTTPException(409, "Only failed or cancelled jobs can be retried")
        # enqueue_conn records the current actor and rechecks document authority and capacity.
        result = await enqueue_conn(conn, identity.tenant_id, job["document_id"])
        await audit(conn, identity, "ingestion.retry", "ingestion_job", job_id)
    return result


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: UUID, identity: Identity = Depends(authenticate)):
    manager(identity)
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                """UPDATE atlas.ingestion_jobs SET status='cancelled',lease_until=NULL,
                error_code='operator_cancelled',updated_at=now()
                WHERE tenant_id=%s AND id=%s AND status IN ('queued','running','retry') RETURNING id""",
                (identity.tenant_id, job_id),
            )
        ).fetchone()
        if not row:
            raise HTTPException(409, "Job is unavailable or has already finished")
        await audit(conn, identity, "ingestion.cancel", "ingestion_job", job_id)
    return {"cancelled": True}


@router.post("/alerts/{alert_id}/ack")
async def acknowledge_alert(alert_id: UUID, identity: Identity = Depends(authenticate)):
    manager(identity)
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                """UPDATE atlas.operational_alerts SET acknowledged_at=now(),acknowledged_by=%s
                WHERE tenant_id=%s AND id=%s RETURNING id""",
                (identity.user_id, identity.tenant_id, alert_id),
            )
        ).fetchone()
        if not row:
            raise HTTPException(404, "Alert is unavailable")
        await audit(conn, identity, "alert.acknowledged", "operational_alert", alert_id)
    return {"acknowledged": True}


async def monitor_runtime():
    """Called by the trusted worker; persist deduplicated alerts without source text."""
    try:
        queue_ready = bool(await redis.ping())
    except Exception:
        queue_ready = False
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            response = await client.get(settings.ollama_url.rstrip("/") + "/api/tags")
            response.raise_for_status()
            generation_ready = settings.generation_model in {
                m.get("name") for m in response.json().get("models", [])
            }
    except Exception:
        generation_ready = False
    from atlas.upload_safety import scanner_status

    try:
        await scanner_status()
        scanner_ready = settings.upload_scan_required
    except Exception:
        scanner_ready = False
    async with db.pool.connection() as conn:
        await conn.execute(
            "SELECT atlas.refresh_operational_alerts(%s,%s)", (queue_ready, generation_ready)
        )
        await conn.execute("SELECT atlas.record_scanner_health(%s)", (scanner_ready,))
