import asyncio
from uuid import uuid4

import pytest

from atlas import worker
from atlas.db import transaction
from atlas.ingestion import create_document

pytestmark = pytest.mark.integration


class QueueRecorder:
    def __init__(self):
        self.acks = []
        self.dead = []

    async def xack(self, *args):
        self.acks.append(args)

    async def xdel(self, *args):
        pass

    async def xadd(self, *args, **kwargs):
        self.dead.append(args)


async def make_job(tenant, attempts=0, expired=False):
    doc = await create_document(
        tenant, "lease fixture", "Synthetic worker recovery document.", "lease-" + str(uuid4())
    )
    job = uuid4()
    async with transaction(tenant) as conn:
        await conn.execute(
            "INSERT INTO atlas.ingestion_jobs(tenant_id,id,document_id,idempotency_key,attempts,status,owner,lease_until) VALUES(%s,%s,%s,%s,%s,%s,'crashed-worker',now()-interval '1 second')",
            (tenant, job, doc["id"], str(job), attempts, "running" if expired else "queued"),
        )
    return job, doc


async def test_expired_lease_is_recovered_and_duplicate_delivery_is_safe(
    worker_database, tenants, monkeypatch
):
    tenant = tenants[0][0]
    job, doc = await make_job(tenant, expired=True)
    calls = []

    async def index(t, d):
        calls.append((t, d))
        await asyncio.sleep(0.02)
        return {"chunks": 1, "reused": False}

    queue = QueueRecorder()
    monkeypatch.setattr(worker, "redis", queue)
    monkeypatch.setattr(worker, "index_document", index)
    payload = {"tenant_id": str(tenant), "job_id": str(job)}
    await asyncio.gather(
        worker.process("1-0", payload, "new-worker"), worker.process("2-0", payload, "other-worker")
    )
    await worker.process("3-0", payload, "third-worker")
    assert calls == [(tenant, doc["id"])]
    async with transaction(tenant) as conn:
        row = await (
            await conn.execute(
                "SELECT status,attempts FROM atlas.ingestion_jobs WHERE tenant_id=%s AND id=%s",
                (tenant, job),
            )
        ).fetchone()
    assert row == {"status": "completed", "attempts": 1}


async def test_retry_exhaustion_moves_job_to_dead_letter(worker_database, tenants, monkeypatch):
    tenant = tenants[0][0]
    job, _ = await make_job(tenant, attempts=2)

    async def broken(*_):
        raise TimeoutError("Synthetic model timeout")

    queue = QueueRecorder()
    monkeypatch.setattr(worker, "redis", queue)
    monkeypatch.setattr(worker, "index_document", broken)
    await worker.process("1-0", {"tenant_id": str(tenant), "job_id": str(job)}, "worker")
    async with transaction(tenant) as conn:
        row = await (
            await conn.execute(
                "SELECT status,error_code FROM atlas.ingestion_jobs WHERE tenant_id=%s AND id=%s",
                (tenant, job),
            )
        ).fetchone()
    assert row == {"status": "dead", "error_code": "TimeoutError"}
    assert queue.dead and queue.acks
