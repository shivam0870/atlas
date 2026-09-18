import hashlib
import json
import math
import time
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from fastapi import HTTPException
from redis.asyncio import Redis

from atlas.config import settings
from atlas.db import access_context, transaction

redis = Redis.from_url(settings.redis_url, decode_responses=True)
cache_redis = Redis.from_url(settings.cache_redis_url, decode_responses=True)
RATE_SCRIPT = """
local t=redis.call('TIME')
local now=t[1]*1000+math.floor(t[2]/1000)
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now-60000)
local count=redis.call('ZCARD',KEYS[1])
if count>=tonumber(ARGV[1]) then
  local oldest=redis.call('ZRANGE',KEYS[1],0,0,'WITHSCORES')
  return {0,math.max(1,math.ceil((tonumber(oldest[2])+60000-now)/1000))}
end
redis.call('ZADD',KEYS[1],now,ARGV[2])
redis.call('PEXPIRE',KEYS[1],61000)
return {1,0}
"""


async def limits(tenant: UUID):
    async with transaction(tenant) as conn:
        row = await (
            await conn.execute(
                "INSERT INTO atlas.tenant_limits(tenant_id) VALUES(%s) ON CONFLICT(tenant_id) DO UPDATE SET tenant_id=excluded.tenant_id RETURNING *",
                (tenant,),
            )
        ).fetchone()
    return row


async def rate_limit(tenant: UUID, maximum: int):
    try:
        allowed, retry = await redis.eval(
            RATE_SCRIPT, 1, f"atlas:rate:{tenant}", maximum, str(uuid4())
        )
    except Exception as exc:
        raise HTTPException(503, "Rate-limit service is unavailable; please retry shortly") from exc
    if not allowed:
        raise HTTPException(
            429, "Workspace request limit reached", headers={"Retry-After": str(retry)}
        )


async def reserve(tenant: UUID, operation: UUID, tokens=8832):
    period = datetime.now(UTC).date().replace(day=1)
    actor = access_context.get()
    user_id = actor.user_id if actor and actor.tenant_id == tenant else None
    async with transaction(tenant) as conn:
        row = await (
            await conn.execute(
                "SELECT * FROM atlas.tenant_limits WHERE tenant_id=%s FOR UPDATE", (tenant,)
            )
        ).fetchone()
        await conn.execute(
            "INSERT INTO atlas.budget_periods(tenant_id,period) VALUES(%s,%s) ON CONFLICT DO NOTHING",
            (tenant, period),
        )
        usage = await (
            await conn.execute(
                "SELECT * FROM atlas.budget_periods WHERE tenant_id=%s AND period=%s FOR UPDATE",
                (tenant, period),
            )
        ).fetchone()
        assert row and usage
        if await (
            await conn.execute(
                "SELECT id FROM atlas.reservations WHERE tenant_id=%s AND id=%s",
                (tenant, operation),
            )
        ).fetchone():
            return
        if usage["used_tokens"] + usage["reserved_tokens"] + tokens > row["monthly_tokens"]:
            raise HTTPException(402, "Monthly workspace token budget reached")
        if usage["spent_usd"] > row["monthly_usd"]:
            raise HTTPException(402, "Monthly workspace cost cap reached")
        if user_id:
            member = await (
                await conn.execute(
                    "SELECT monthly_tokens FROM atlas.memberships WHERE tenant_id=%s AND user_id=%s AND status='active'",
                    (tenant, user_id),
                )
            ).fetchone()
            if not member:
                raise HTTPException(403, "Workspace membership is unavailable")
            member_usage = await (
                await conn.execute(
                    "SELECT coalesce(sum(CASE WHEN status='settled' THEN coalesce(used_tokens,0) ELSE tokens END),0) n FROM atlas.reservations WHERE tenant_id=%s AND user_id=%s AND period=%s",
                    (tenant, user_id, period),
                )
            ).fetchone()
            assert member_usage is not None
            if (
                member["monthly_tokens"] is not None
                and member_usage["n"] + tokens > member["monthly_tokens"]
            ):
                raise HTTPException(402, "Your monthly token budget has been reached")
        inserted = await (
            await conn.execute(
                "INSERT INTO atlas.reservations(tenant_id,id,period,tokens,user_id) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
                (tenant, operation, period, tokens, user_id),
            )
        ).fetchone()
        if inserted:
            await conn.execute(
                "UPDATE atlas.budget_periods SET reserved_tokens=reserved_tokens+%s WHERE tenant_id=%s AND period=%s",
                (tokens, tenant, period),
            )


async def settle(tenant: UUID, operation: UUID, used: int | None):
    async with transaction(tenant) as conn:
        row = await (
            await conn.execute(
                "SELECT * FROM atlas.reservations WHERE tenant_id=%s AND id=%s AND status='reserved' FOR UPDATE",
                (tenant, operation),
            )
        ).fetchone()
        if not row:
            return
        if used is None:
            await conn.execute(
                "UPDATE atlas.reservations SET status='uncertain' WHERE tenant_id=%s AND id=%s",
                (tenant, operation),
            )
        else:
            await conn.execute(
                "UPDATE atlas.budget_periods SET reserved_tokens=reserved_tokens-%s,used_tokens=used_tokens+%s WHERE tenant_id=%s AND period=%s",
                (row["tokens"], used, tenant, row["period"]),
            )
            await conn.execute(
                "UPDATE atlas.reservations SET status='settled',used_tokens=%s WHERE tenant_id=%s AND id=%s",
                (used, tenant, operation),
            )


def cache_namespace(tenant, collection, revision, mode, top_k, rerank, scopes):
    fingerprint = hashlib.sha256(
        json.dumps(
            [
                str(tenant),
                collection,
                revision,
                mode,
                top_k,
                rerank,
                sorted(scopes),
                settings.generation_model,
                "prompt-v2",
                settings.relevance_floor,
                settings.rrf_constant,
                settings.vector_weight,
                settings.lexical_weight,
            ]
        ).encode()
    ).hexdigest()
    return f"atlas:answer:{tenant}:" + fingerprint


def cosine(a: list[float], b: list[float]) -> float:
    denominator = math.sqrt(sum(x * x for x in a) * sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / denominator if denominator else 0


async def cache_lookup(namespace: str, question: str, vector: list[float] | None = None):
    key = hashlib.sha256(question.strip().encode()).hexdigest()
    try:
        exact = await cache_redis.get(namespace + ":" + key)
        if exact:
            return {**json.loads(exact), "match": "exact"}
        if vector is not None and settings.semantic_cache_threshold is not None:
            candidates = cast(list[str], await cache_redis.zrevrange(namespace + ":index", 0, 49))
            if candidates:
                values = await cache_redis.mget(
                    [namespace + ":" + candidate for candidate in candidates]
                )
                best, best_score = None, settings.semantic_cache_threshold
                for value in values:
                    if value:
                        record = json.loads(value)
                        score = cosine(vector, record["vector"])
                        if score >= best_score:
                            best, best_score = record, score
                if best:
                    return {**best, "match": "semantic", "similarity": best_score}
    except Exception:
        # Cache failure is optional-path failure. Quota/rate state is handled independently.
        return None
    return None


async def cache_store(
    namespace: str, question: str, vector: list[float], answer: str, sources: list[dict]
):
    key = hashlib.sha256(question.strip().encode()).hexdigest()
    value = json.dumps({"answer": answer, "sources": sources, "vector": vector})
    try:
        async with cache_redis.pipeline(transaction=True) as pipe:
            pipe.set(namespace + ":" + key, value, ex=3600)
            pipe.zadd(namespace + ":index", {key: time.time()})
            pipe.zremrangebyrank(namespace + ":index", 0, -51)
            pipe.expire(namespace + ":index", 3600)
            await pipe.execute()
    except Exception:
        return


async def enqueue_conn(conn, tenant: UUID, document_id: UUID, traceparent: str | None = None):
    from atlas.ingestion import pipeline_hash

    document = await (
        await conn.execute(
            "SELECT coalesce(pending_version_id,current_version_id)::text version FROM atlas.documents WHERE tenant_id=%s AND id=%s",
            (tenant, document_id),
        )
    ).fetchone()
    if not document:
        raise HTTPException(404, "Document unavailable")
    key = str(document_id) + ":" + (document["version"] or "legacy") + ":" + pipeline_hash()
    await conn.execute("SELECT id FROM atlas.tenants WHERE id=%s FOR UPDATE", (tenant,))
    existing = await (
        await conn.execute(
            "SELECT id FROM atlas.ingestion_jobs WHERE tenant_id=%s AND idempotency_key=%s",
            (tenant, key),
        )
    ).fetchone()
    if not existing:
        pending = await (
            await conn.execute(
                "SELECT count(*) n FROM atlas.ingestion_jobs WHERE tenant_id=%s AND status IN ('queued','running','retry')",
                (tenant,),
            )
        ).fetchone()
        if pending["n"] >= settings.max_pending_jobs:
            raise HTTPException(
                429,
                "Workspace indexing queue is full; try again after pending documents finish",
                headers={"Retry-After": "10"},
            )

    row = await (
        await conn.execute(
            "INSERT INTO atlas.ingestion_jobs(tenant_id,id,document_id,idempotency_key,traceparent) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,idempotency_key) DO UPDATE SET idempotency_key=excluded.idempotency_key RETURNING *",
            (tenant, uuid4(), document_id, key, traceparent),
        )
    ).fetchone()
    assert row
    doc = await (
        await conn.execute(
            "SELECT status,pending_version_id FROM atlas.documents WHERE tenant_id=%s AND id=%s",
            (tenant, document_id),
        )
    ).fetchone()
    if (
        row["status"] in {"completed", "dead"}
        and doc
        and (doc["status"] == "pending" or doc["pending_version_id"])
    ):
        await conn.execute(
            "UPDATE atlas.ingestion_jobs SET status='queued',attempts=0,error_code=NULL WHERE tenant_id=%s AND id=%s",
            (tenant, row["id"]),
        )
        row["status"], row["attempts"] = "queued", 0
        await conn.execute(
            "INSERT INTO atlas.outbox(tenant_id,id,job_id) VALUES(%s,%s,%s)",
            (tenant, uuid4(), row["id"]),
        )
    elif row["status"] == "queued" and row["attempts"] == 0:
        await conn.execute(
            "INSERT INTO atlas.outbox(tenant_id,id,job_id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
            (tenant, row["id"], row["id"]),
        )
    return {"job_id": str(row["id"]), "job_status": row["status"]}


async def enqueue(tenant: UUID, document_id: UUID, traceparent: str | None = None):
    async with transaction(tenant) as conn:
        return await enqueue_conn(conn, tenant, document_id, traceparent)
