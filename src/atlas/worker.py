"""Postgres outbox -> Redis Streams, with leases and idempotent publication."""

import asyncio
import logging
import signal
from typing import Any
from uuid import UUID, uuid4

from opentelemetry import propagate
from psycopg.types.json import Jsonb
from redis.exceptions import ResponseError

from atlas import db
from atlas.config import settings
from atlas.db import transaction
from atlas.ingestion import index_document
from atlas.serving import redis
from atlas.telemetry import indexed, queue_depth, setup, tracer

STREAM = "atlas:ingestion"
GROUP = "indexers"
log = logging.getLogger("atlas.worker")


async def dispatch():
    async with db.pool.connection() as conn, conn.transaction():
        rows = await (await conn.execute("SELECT * FROM atlas.dispatch_outbox()")).fetchall()
        for row in rows:
            await redis.xadd(
                STREAM, {"tenant_id": str(row["tenant_id"]), "job_id": str(row["job_id"])}
            )
            await conn.execute(
                "SELECT set_config('app.tenant_id',%s,true)", (str(row["tenant_id"]),)
            )
            await conn.execute(
                "UPDATE atlas.outbox SET published_at=now() WHERE tenant_id=%s AND id=%s",
                (row["tenant_id"], row["id"]),
            )
    return len(rows)


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
        if not row or row["status"] in {"completed", "dead"}:
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
                "UPDATE atlas.ingestion_jobs SET status='completed',result=%s,lease_until=NULL,updated_at=now() WHERE tenant_id=%s AND id=%s AND owner=%s",
                (Jsonb(result), tenant, job, owner),
            )
    except Exception as exc:
        dead = claimed["attempts"] >= 3 or isinstance(exc, ValueError)
        async with transaction(tenant) as conn:
            await conn.execute(
                "UPDATE atlas.ingestion_jobs SET status=%s,error_code=%s,lease_until=NULL,updated_at=now() WHERE tenant_id=%s AND id=%s AND owner=%s",
                ("dead" if dead else "retry", type(exc).__name__, tenant, job, owner),
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
            except Exception as exc:
                log.warning("worker_retry", extra={"fields": {"error_code": type(exc).__name__}})
                await asyncio.sleep(2)
    finally:
        await db.pool.close()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
