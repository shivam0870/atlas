"""Postgres outbox -> Redis Streams, with leases and idempotent publication."""

import asyncio
import logging
import signal
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from opentelemetry import propagate
from psycopg.types.json import Jsonb
from redis.exceptions import ResponseError

from atlas import db
from atlas.config import settings
from atlas.db import transaction
from atlas.evaluation import process_evaluation_job
from atlas.ingestion import index_document
from atlas.job_authority import JobCancelled, ingestion_job
from atlas.serving import cache_redis, redis
from atlas.telemetry import indexed, queue_depth, setup, tracer

STREAM = "atlas:ingestion"
GROUP = "indexers"
log = logging.getLogger("atlas.worker")
BOUNDED_DISPATCH = """
if redis.call('XLEN',KEYS[1])>=tonumber(ARGV[1]) then return false end
return redis.call('XADD',KEYS[1],'*','tenant_id',ARGV[2],'job_id',ARGV[3])
"""


async def dispatch():
    if await redis.xlen(STREAM) >= settings.ingestion_stream_size:
        return 0
    async with db.pool.connection() as conn, conn.transaction():
        rows = await (await conn.execute("SELECT * FROM atlas.dispatch_outbox()")).fetchall()
        for row in rows:
            published = await redis.eval(
                BOUNDED_DISPATCH,
                1,
                STREAM,
                settings.ingestion_stream_size,
                str(row["tenant_id"]),
                str(row["job_id"]),
            )
            if not published:
                continue
            await conn.execute(
                "SELECT set_config('app.tenant_id',%s,true)", (str(row["tenant_id"]),)
            )
            await conn.execute(
                "UPDATE atlas.outbox SET published_at=now() WHERE tenant_id=%s AND id=%s",
                (row["tenant_id"], row["id"]),
            )
    return len(rows)


async def dispatch_evaluation(owner):
    async with db.pool.connection() as conn:
        jobs = await (
            await conn.execute("SELECT * FROM atlas.pending_evaluation_jobs()")
        ).fetchall()
    for job in jobs:
        await process_evaluation_job(job["tenant_id"], job["id"], owner)


async def maintenance():
    from atlas.operations import monitor_runtime
    from atlas.workflows import workflow_tick

    await monitor_runtime()
    await workflow_tick()
    await cleanup_permission_caches()
    async with db.pool.connection() as conn, conn.transaction():
        await conn.execute("SELECT atlas.deliver_maintenance_notifications()")
        job = await (await conn.execute("SELECT * FROM atlas.process_retention()")).fetchone()
    if job:
        for key in job["storage_keys"]:
            await asyncio.to_thread(
                (Path(".local/uploads") / str(UUID(key))).unlink, missing_ok=True
            )
        if job["kind"] == "tenant":
            async for key in cache_redis.scan_iter(
                match=f"atlas:answer:{job['target_id']}:*", count=100
            ):
                await cache_redis.delete(key)
        async with db.pool.connection() as conn:
            await conn.execute("SELECT atlas.finish_retention(%s)", (job["job_id"],))


async def cleanup_permission_caches():
    # New revisions immediately prevent cache reuse in the request path. This
    # durable outbox additionally erases old payloads and survives Redis downtime.
    async with db.pool.connection() as conn:
        pending = await (
            await conn.execute("SELECT * FROM atlas.pending_permission_cache_invalidations()")
        ).fetchall()
    for row in pending:
        async for key in cache_redis.scan_iter(
            match=f"atlas:answer:{row['tenant_id']}:*", count=100
        ):
            await cache_redis.delete(key)
        async with db.pool.connection() as conn:
            await conn.execute(
                "SELECT atlas.ack_permission_cache_invalidation(%s,%s)",
                (row["tenant_id"], row["revision"]),
            )


async def heartbeat(tenant, job, owner):
    while True:
        await asyncio.sleep(10)
        async with transaction(tenant) as conn:
            await conn.execute(
                "UPDATE atlas.ingestion_jobs SET lease_until=now()+interval '45 seconds' WHERE tenant_id=%s AND id=%s AND owner=%s AND status='running'",
                (tenant, job, owner),
            )


async def process(message_id, payload, owner):
    tenant, job = UUID(payload["tenant_id"]), UUID(payload["job_id"])
    async with transaction(tenant) as conn:
        row = await (
            await conn.execute(
                "SELECT * FROM atlas.ingestion_jobs WHERE tenant_id=%s AND id=%s FOR UPDATE",
                (tenant, job),
            )
        ).fetchone()
        if not row or row["status"] in {"completed", "dead", "cancelled"}:
            await redis.xack(STREAM, GROUP, message_id)
            await redis.xdel(STREAM, message_id)
            return
        claimed = await (
            await conn.execute(
                "UPDATE atlas.ingestion_jobs SET status='running',owner=%s,lease_until=now()+interval '45 seconds',attempts=attempts+1,updated_at=now() WHERE tenant_id=%s AND id=%s AND (status IN ('queued','retry') OR lease_until<now()) RETURNING *",
                (owner, tenant, job),
            )
        ).fetchone()
    if not claimed:
        return
    beat = asyncio.create_task(heartbeat(tenant, job, owner))
    job_token = ingestion_job.set(claimed)
    try:
        context = propagate.extract(
            {"traceparent": claimed["traceparent"]} if claimed["traceparent"] else {}
        )
        with tracer.start_as_current_span("ingestion.job", context=context) as span:
            span.set_attribute("tenant.id", str(tenant))
            span.set_attribute("job.id", str(job))
            async with asyncio.timeout(300):
                result = await index_document(tenant, claimed["document_id"])
                indexed.add(1)
        async with transaction(tenant) as conn:
            await conn.execute(
                "UPDATE atlas.ingestion_jobs SET status='completed',result=%s,lease_until=NULL,updated_at=now() WHERE tenant_id=%s AND id=%s AND owner=%s AND status='running'",
                (Jsonb(result), tenant, job, owner),
            )
    except Exception as exc:
        dead = claimed["attempts"] >= 3 or isinstance(exc, ValueError)
        async with transaction(tenant) as conn:
            await conn.execute(
                "UPDATE atlas.ingestion_jobs SET status=%s,error_code=%s,lease_until=NULL,updated_at=now() WHERE tenant_id=%s AND id=%s AND owner=%s AND status='running'",
                (
                    "cancelled" if isinstance(exc, JobCancelled) else "dead" if dead else "retry",
                    type(exc).__name__,
                    tenant,
                    job,
                    owner,
                ),
            )
            if not dead:
                await conn.execute(
                    "INSERT INTO atlas.outbox(tenant_id,id,job_id,available_at) VALUES(%s,%s,%s,now()+(%s * interval '1 second'))",
                    (tenant, uuid4(), job, 2 ** claimed["attempts"]),
                )
        if dead:
            await redis.xadd(
                "atlas:ingestion:dead",
                {"tenant_id": str(tenant), "job_id": str(job), "error_code": type(exc).__name__},
                maxlen=1000,
            )
        log.warning(
            "ingestion_attempt_failed",
            extra={"fields": {"job_id": str(job), "error_code": type(exc).__name__}},
        )
    finally:
        ingestion_job.reset(job_token)
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)
    await redis.xack(STREAM, GROUP, message_id)
    await redis.xdel(STREAM, message_id)


async def main():
    if not settings.worker_database_url:
        raise RuntimeError("WORKER_DATABASE_URL is required")
    db.pool.conninfo = settings.worker_database_url
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    settings.service_name = "atlas-worker"
    setup()
    await db.pool.open(wait=True)
    owner = str(uuid4())
    maintained_at = 0.0
    try:
        await redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise
    try:
        while not stop.is_set():
            try:
                await dispatch()
                queue_depth.set(await redis.xlen(STREAM))
                reclaimed = await redis.xautoclaim(
                    STREAM, GROUP, owner, min_idle_time=60000, start_id="0-0", count=5
                )
                messages = reclaimed[1]
                if not messages:
                    batches: Any = await redis.xreadgroup(
                        GROUP, owner, {STREAM: ">"}, count=1, block=1000
                    )
                    if isinstance(batches, dict):
                        messages = next(iter(batches.values()))[0] if batches else []
                    else:
                        messages = batches[0][1] if batches else []
                for message_id, payload in messages:
                    await process(message_id, payload, owner)
                await dispatch_evaluation(owner)
                if time.monotonic() - maintained_at > 30:
                    await maintenance()
                    maintained_at = time.monotonic()
            except Exception as exc:
                log.warning("worker_retry", extra={"fields": {"error_code": type(exc).__name__}})
                await asyncio.sleep(2)
    finally:
        await db.pool.close()
        await db.application_pool.close()
        await db.identity_pool.close()
        await redis.aclose()
        await cache_redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
