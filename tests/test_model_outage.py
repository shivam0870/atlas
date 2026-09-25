"""Real retrieval and real failed network connection; no mocked model client or sources."""

import socket

import pytest
from test_conversations import consume, request

from atlas import api, ingestion
from atlas.config import settings
from atlas.db import application_transactions


@pytest.mark.integration
@pytest.mark.model
async def test_real_generation_outage_keeps_authorized_search_available(
    worker_database, tenants, monkeypatch
):
    tenant = tenants.ids[0]
    principal = tenants.identities[tenant]
    doc = await ingestion.create_document(
        tenant,
        "Zephyr outage handbook",
        "The Zephyr release approval code is QUARTZ-EMBER. The Platform team approves every Zephyr deployment.",
        "zephyr-outage",
    )
    await ingestion.index_document(tenant, doc["id"])
    other = tenants.ids[1]
    tenants.use(other)
    private = await ingestion.create_document(
        other,
        "Private Zephyr handbook",
        "The private Zephyr approval code is HIDDEN-SAPPHIRE.",
        "private-zephyr-outage",
    )
    await ingestion.index_document(other, private["id"])
    tenants.use(tenant)
    # Reserve a real local TCP port without listening, guaranteeing refusal and
    # leaving the shared running Ollama instance untouched.
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        monkeypatch.setattr(
            settings, "ollama_url", f"http://127.0.0.1:{unavailable.getsockname()[1]}"
        )
        monkeypatch.setattr(settings, "fallback_enabled", False)
        async with application_transactions():
            output = await consume(
                await api.query(
                    api.Query(
                        question="What is the Zephyr release approval code?", use_cache=False
                    ),
                    request(),
                    principal,
                )
            )
    assert '"search_fallback": true' in output
    assert '"status": "abstained"' in output
    assert "QUARTZ-EMBER" in output
    assert "HIDDEN-SAPPHIRE" not in output
