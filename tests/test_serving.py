import asyncio
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi import HTTPException

from atlas import serving
from atlas.config import settings
from atlas.db import transaction
from atlas.evaluation import EvalConfig, run_evaluation
from atlas.generation import UsageMeter, fit_sources, messages
from atlas.main import app

pytestmark = pytest.mark.integration


async def test_rate_limit_atomic_concurrency(database, tenants):
    tenant = tenants[0][0]

    async def attempt():
        try:
            await serving.rate_limit(tenant, 3)
            return 200
        except HTTPException as exc:
            assert exc.status_code == 429
            assert 1 <= int(exc.headers["Retry-After"]) <= 60
            return 429

    statuses = await asyncio.gather(*(attempt() for _ in range(20)))
    assert statuses.count(200) == 3 and statuses.count(429) == 17
    await serving.redis.delete(f"atlas:rate:{tenant}")


async def test_budget_atomic_reserve_idempotence_and_settlement(database, tenants):
    tenant = tenants[0][0]
    await serving.limits(tenant)
    async with transaction(tenant) as conn:
        await conn.execute(
            "UPDATE atlas.tenant_limits SET monthly_tokens=100 WHERE tenant_id=%s", (tenant,)
        )
    operation = uuid4()
    await asyncio.gather(*(serving.reserve(tenant, operation, 80) for _ in range(8)))
    with pytest.raises(HTTPException) as err:
        await serving.reserve(tenant, uuid4(), 21)
    assert err.value.status_code == 402
    await asyncio.gather(*(serving.settle(tenant, operation, 30) for _ in range(8)))
    async with transaction(tenant) as conn:
        row = await (
            await conn.execute("SELECT * FROM atlas.budget_periods WHERE tenant_id=%s", (tenant,))
        ).fetchone()
    assert row["reserved_tokens"] == 0 and row["used_tokens"] == 30
    unknown = uuid4()
    await serving.reserve(tenant, unknown, 60)
    await serving.settle(tenant, unknown, None)
    with pytest.raises(HTTPException):
        await serving.reserve(tenant, uuid4(), 11)


async def test_cache_scope_version_and_semantic_opt_in(tenants, monkeypatch):
    tenant, other = tenants[0]
    args = ["collection", 1, "hybrid", 5, False, ["query"]]
    namespace = serving.cache_namespace(tenant, *args)
    await serving.cache_store(
        namespace, "private question", [1.0, 0.0], "private answer", [{"id": "private"}]
    )
    assert (await serving.cache_lookup(namespace, "private question"))["match"] == "exact"
    assert (
        await serving.cache_lookup(serving.cache_namespace(other, *args), "private question")
        is None
    )
    args[1] = 2
    assert (
        await serving.cache_lookup(serving.cache_namespace(tenant, *args), "private question")
        is None
    )
    assert await serving.cache_lookup(namespace, "paraphrase", [1.0, 0.0]) is None
    monkeypatch.setattr(settings, "semantic_cache_threshold", 0.99)
    assert (await serving.cache_lookup(namespace, "paraphrase", [1.0, 0.0]))["match"] == "semantic"
    assert await serving.cache_lookup(namespace, "different", [0.0, 1.0]) is None
    async for key in serving.cache_redis.scan_iter(match=namespace + "*"):
        await serving.cache_redis.delete(key)


async def test_concurrent_http_scope_expiry_and_permissions(database, tenants):
    ids, keys = tenants
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:

        async def read(index):
            response = await client.get(
                "/api/documents", headers={"Authorization": "Bearer " + keys[index]}
            )
            assert response.status_code == 200
            return {row["id"] for row in response.json()}

        rows = await asyncio.gather(*(read(i % 2) for i in range(32)))
        assert all(row == rows[i % 2] for i, row in enumerate(rows))
        assert not rows[0] & rows[1]
        denied = await client.post(
            "/api/documents/text",
            json={"title": "x", "content": "x"},
            headers={"Authorization": "Bearer " + keys[0]},
        )
        assert denied.status_code == 403
        hidden = await client.get(
            "/api/documents/" + next(iter(rows[1])), headers={"Authorization": "Bearer " + keys[0]}
        )
        assert hidden.status_code == 404
        csrf = await client.post(
            "/api/local/connect/" + str(ids[0]), headers={"Origin": "https://attacker.example"}
        )
        assert csrf.status_code == 403
        with psycopg.connect(settings.database_admin_url) as conn:
            conn.execute(
                "UPDATE atlas.api_keys SET expires_at=now()-interval '1 second' WHERE tenant_id=%s",
                (ids[0],),
            )
        assert (
            await client.get("/api/me", headers={"Authorization": "Bearer " + keys[0]})
        ).status_code == 401


async def test_unreviewed_evaluation_is_blocked(database, tenants):
    with pytest.raises(HTTPException) as err:
        await run_evaluation(tenants[0][0], EvalConfig())
    assert err.value.status_code == 409


async def test_runtime_cannot_dispatch_other_tenant_jobs(database):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with transaction() as conn:
            await conn.execute("SELECT * FROM atlas.dispatch_outbox()")


def test_usage_and_context_bound():
    import json

    meter = UsageMeter()
    assert meter.total == 0
    meter.begin()
    assert meter.total is None
    meter.finish(100, 20)
    assert meter.total == 120
    fitted = fit_sources(
        "question", [{"id": "a", "title": "unicode", "content": "文" * 20000, "start_offset": 50}]
    )
    assert len(json.dumps(messages("question", fitted), ensure_ascii=False).encode()) <= 7000
    assert fitted[0]["end_offset"] == 50 + len(fitted[0]["content"])


async def test_no_evidence_abstains_without_generation(database, tenants, monkeypatch):
    from atlas import api

    ids, keys = tenants
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "UPDATE atlas.api_keys SET scopes=ARRAY['read','query'] WHERE tenant_id=%s", (ids[0],)
        )

    async def empty(*args, **kwargs):
        return [], [], str(uuid4()), 0

    async def forbidden(*args, **kwargs):
        raise AssertionError("Generation must not run without evidence")
        yield

    monkeypatch.setattr(api, "retrieve", empty)
    monkeypatch.setattr(api, "generate", forbidden)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/query",
            headers={"Authorization": "Bearer " + keys[0]},
            json={"question": "What is the invisible orbital code?"},
        )
        assert response.status_code == 200
        assert '"status": "abstained"' in response.text
        assert "find enough relevant information" in response.text
    async with transaction(ids[0]) as conn:
        row = await (
            await conn.execute(
                "SELECT reserved_tokens,used_tokens FROM atlas.budget_periods WHERE tenant_id=%s",
                (ids[0],),
            )
        ).fetchone()
    assert row == {"reserved_tokens": 0, "used_tokens": 0}


async def test_deletion_during_embedding_never_resurrects(database, tenants, monkeypatch):
    from types import SimpleNamespace

    from atlas import ingestion

    tenant = tenants[0][0]
    doc = await ingestion.create_document(
        tenant, "race", "Only synthetic content for the deletion race.", "race"
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(texts):
        entered.set()
        await release.wait()
        return [[1.0] + [0.0] * 383 for _ in texts]

    monkeypatch.setattr(
        ingestion,
        "encoder",
        lambda: SimpleNamespace(tokenizer=SimpleNamespace(encode=lambda text: text.split())),
    )
    monkeypatch.setattr(ingestion, "embed", delayed)
    task = asyncio.create_task(ingestion.index_document(tenant, doc["id"]))
    await asyncio.wait_for(entered.wait(), 10)
    async with transaction(tenant) as conn:
        await conn.execute(
            "UPDATE atlas.documents SET status='deleted' WHERE tenant_id=%s AND id=%s",
            (tenant, doc["id"]),
        )
    release.set()
    with pytest.raises(ValueError, match="removed"):
        await task
    async with transaction(tenant) as conn:
        row = await (
            await conn.execute(
                "SELECT status FROM atlas.documents WHERE tenant_id=%s AND id=%s",
                (tenant, doc["id"]),
            )
        ).fetchone()
        assert row["status"] == "deleted"
        assert not await (
            await conn.execute(
                "SELECT id FROM atlas.chunks WHERE tenant_id=%s AND document_id=%s",
                (tenant, doc["id"]),
            )
        ).fetchall()


async def test_outbox_document_is_atomic_and_idempotent(database, tenants):
    from atlas.ingestion import create_document

    tenant = tenants[0][0]
    docs = await asyncio.gather(
        *(
            create_document(tenant, "atomic", "A unique synthetic source.", "atomic", queued=True)
            for _ in range(5)
        )
    )
    assert len({d["id"] for d in docs}) == 1
    async with transaction(tenant) as conn:
        assert (
            len(
                await (
                    await conn.execute(
                        "SELECT * FROM atlas.ingestion_jobs WHERE tenant_id=%s", (tenant,)
                    )
                ).fetchall()
            )
            == 1
        )
        assert (
            len(
                await (
                    await conn.execute("SELECT * FROM atlas.outbox WHERE tenant_id=%s", (tenant,))
                ).fetchall()
            )
            == 1
        )


async def test_primary_failover_and_no_retry_after_partial_stream(monkeypatch):
    from atlas import generation

    calls = []

    async def allowed(_):
        return True

    async def result(*_):
        pass

    async def backend(name, prompt):
        calls.append(name)
        if name == "ollama":
            raise httpx.ConnectError("Forced primary outage")
        yield {"type": "delta", "text": "Recovered [1]"}
        yield {"type": "usage", "input_tokens": 12, "output_tokens": 4}

    monkeypatch.setattr(settings, "fallback_enabled", True)
    monkeypatch.setattr(generation, "circuit_allowed", allowed)
    monkeypatch.setattr(generation, "circuit_result", result)
    monkeypatch.setattr(generation, "backend_stream", backend)
    parts = [
        x
        async for x in generation.generate("question", [{"title": "source", "content": "evidence"}])
    ]
    assert calls == ["ollama", "ollama", "llama.cpp"] and parts[0]["text"] == "Recovered [1]"
    calls.clear()

    async def partial(name, prompt):
        calls.append(name)
        yield {"type": "delta", "text": "partial"}
        raise httpx.ReadError("Stream interrupted")

    monkeypatch.setattr(generation, "backend_stream", partial)
    with pytest.raises(httpx.ReadError):
        _ = [
            x
            async for x in generation.generate(
                "question", [{"title": "source", "content": "evidence"}]
            )
        ]
    assert calls == ["ollama"]


async def test_circuit_open_single_half_open_probe_and_recovery():
    from atlas.generation import circuit_allowed, circuit_result

    name = "test-" + str(uuid4())
    for _ in range(3):
        await circuit_result(name, False)
    assert not await circuit_allowed(name)
    await serving.redis.delete("atlas:cooldown:" + name)
    attempts = await asyncio.gather(*(circuit_allowed(name) for _ in range(10)))
    assert sum(attempts) == 1
    await circuit_result(name, True)
    assert await circuit_allowed(name)


async def test_http_budget_and_rate_statuses(database, tenants, monkeypatch):
    from atlas import api

    tenant, key = tenants[0][0], tenants[1][0]
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "UPDATE atlas.api_keys SET scopes=ARRAY['read','query'] WHERE tenant_id=%s", (tenant,)
        )
    await serving.limits(tenant)
    async with transaction(tenant) as conn:
        await conn.execute(
            "UPDATE atlas.tenant_limits SET monthly_tokens=0 WHERE tenant_id=%s", (tenant,)
        )

    async def empty(*args, **kwargs):
        return [], [], str(uuid4()), 0

    monkeypatch.setattr(api, "retrieve", empty)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://test",
        headers={"Authorization": "Bearer " + key},
    ) as client:
        assert (
            await client.post("/api/query", json={"question": "Budget test?"})
        ).status_code == 402
        await serving.redis.delete(f"atlas:rate:{tenant}")
        async with transaction(tenant) as conn:
            await conn.execute(
                "UPDATE atlas.tenant_limits SET monthly_tokens=100000,requests_per_minute=1 WHERE tenant_id=%s",
                (tenant,),
            )
        assert (
            await client.post("/api/query", json={"question": "First question?"})
        ).status_code == 200
        response = await client.post("/api/query", json={"question": "Second question?"})
        assert response.status_code == 429 and int(response.headers["Retry-After"]) >= 1


async def test_cache_outage_is_optional_but_rate_outage_fails_closed(monkeypatch):
    class Unavailable:
        async def get(self, *_):
            raise ConnectionError("Unavailable")

        async def eval(self, *_):
            raise ConnectionError("Unavailable")

    monkeypatch.setattr(serving, "cache_redis", Unavailable())
    monkeypatch.setattr(serving, "redis", Unavailable())
    assert await serving.cache_lookup("isolated", "question") is None
    with pytest.raises(HTTPException) as exc:
        await serving.rate_limit(uuid4(), 10)
    assert exc.value.status_code == 503


def test_retry_after_and_nontransient_errors():
    import ollama

    from atlas.generation import retry_after_seconds, retryable

    assert retry_after_seconds("3") == 3
    assert retry_after_seconds("invalid") == 0
    assert retryable(ollama.ResponseError("busy", status_code=429))
    assert retryable(ollama.ResponseError("outage", status_code=503))
    assert not retryable(ollama.ResponseError("invalid", status_code=400))


async def test_queue_backpressure_rolls_back_document_creation(database, tenants, monkeypatch):
    from atlas.ingestion import create_document

    tenant = tenants[0][0]
    doc = await create_document(
        tenant, "existing queue item", "Synthetic queue fixture.", "existing"
    )
    async with transaction(tenant) as conn:
        await conn.execute(
            "INSERT INTO atlas.ingestion_jobs(tenant_id,id,document_id,idempotency_key) VALUES(%s,%s,%s,%s)",
            (tenant, uuid4(), doc["id"], "existing"),
        )
    monkeypatch.setattr(settings, "max_pending_jobs", 1)
    with pytest.raises(HTTPException) as exc:
        await create_document(
            tenant, "must roll back", "Synthetic rejected upload.", "rejected", queued=True
        )
    assert exc.value.status_code == 429
    async with transaction(tenant) as conn:
        assert not await (
            await conn.execute(
                "SELECT id FROM atlas.documents WHERE tenant_id=%s AND source_key='rejected'",
                (tenant,),
            )
        ).fetchone()
