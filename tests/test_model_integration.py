import pytest

from atlas import ingestion
from atlas.db import transaction
from atlas.retrieval import retrieve

pytestmark = [pytest.mark.integration, pytest.mark.model]


async def test_real_embedding_cache_idempotence_retrieval_and_offsets(
    worker_database, tenants, monkeypatch
):
    tenant = tenants[0][0]
    content = "The Zephyr release approval code is QUARTZ-EMBER. The Platform team approves every Zephyr deployment."
    doc = await ingestion.create_document(tenant, "Zephyr handbook", content, "zephyr")
    initial = await ingestion.index_document(tenant, doc["id"])
    repeated = await ingestion.index_document(tenant, doc["id"])
    assert repeated["reused"] and repeated["chunks"] == initial["chunks"]

    async def unexpected(*_):
        raise AssertionError("Cached passages must not be embedded again")

    monkeypatch.setattr(ingestion, "embed", unexpected)
    copied = await ingestion.create_document(tenant, "Zephyr copy", content, "zephyr-copy")
    reused = await ingestion.index_document(tenant, copied["id"])
    assert reused["embedding_cache_hits"] == initial["chunks"]
    sources, *_ = await retrieve(tenant, "What is the Zephyr release approval code?", top_k=3)
    assert sources and "QUARTZ-EMBER" in sources[0]["content"]
    for source in sources:
        assert 1 <= source["vector_rank"] <= 20
        assert 1 <= source["lexical_rank"] <= 20
        async with transaction(tenant) as conn:
            original = await (
                await conn.execute(
                    "SELECT content FROM atlas.documents WHERE tenant_id=%s AND id=%s",
                    (tenant, source["document_id"]),
                )
            ).fetchone()
        assert (
            original["content"][source["start_offset"] : source["end_offset"]] == source["content"]
        )
    vector_only, *_ = await retrieve(tenant, "Zephyr approval", mode="vector", top_k=3)
    assert [source["vector_rank"] for source in vector_only] == list(range(1, len(vector_only) + 1))
    assert all(source["lexical_rank"] is None for source in vector_only)
