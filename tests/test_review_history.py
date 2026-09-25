"""History and overview regressions found in production readiness review."""

from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from test_workflows import ready_document

from atlas.api import overview, queries
from atlas.conversations import safe_message
from atlas.db import transaction
from atlas.knowledge import PublicationBody, publish_version


@pytest.mark.integration
async def test_overview_counts_only_published_current_chunks(database, tenants):
    result = await overview(tenants.identities[tenants.ids[0]])
    assert result["chunks"] == 0
    assert result["documents"] >= 1


@pytest.mark.integration
async def test_old_answer_refreshes_source_publication_metadata(database, tenants):
    tenant = tenants.ids[0]
    identity = tenants.identities[tenant]
    document, version, _ = await ready_document(tenant, "Policy", "Approval is required.")
    message = {
        "role": "assistant",
        "status": "completed",
        "content": "Approval is required. [1]",
        "metadata": {},
        "sources": [
            {
                "id": str(uuid4()),
                "document_id": str(document),
                "version_id": str(version),
                "title": "Policy",
                "content": "Approval is required.",
                "publication_status": "published",
            }
        ],
    }
    assert not (await safe_message(identity, message))["sources"][0]["historical"]
    await publish_version(
        document, version, PublicationBody(publication_status="archived"), identity
    )
    restored = await safe_message(identity, message)
    assert restored["sources"][0]["historical"]
    assert restored["sources"][0]["publication_status"] == "archived"


@pytest.mark.integration
async def test_plain_query_history_withholds_preexisting_unsupported_answer(database, tenants):
    tenant = tenants.ids[0]
    identity = tenants.identities[tenant]
    document, version, _ = await ready_document(tenant, "Policy", "Approval is required.")
    evidence = [
        {
            "id": str(uuid4()),
            "document_id": str(document),
            "version_id": str(version),
            "title": "Policy",
            "content": "Approval is required.",
        }
    ]
    async with transaction(tenant) as conn:
        await conn.execute(
            "INSERT INTO atlas.queries(tenant_id,id,user_id,principal_id,principal_kind,question,mode,status,answer,sources) VALUES(%s,%s,%s,%s,'user','Approval?','hybrid','completed','Approval is optional. [1]',%s)",
            (tenant, uuid4(), identity.user_id, identity.principal_id, Jsonb(evidence)),
        )
    result = await queries(identity)
    assert result[0]["sources"]
    assert "Approval is optional" not in result[0]["answer"]
    assert "could not verify" in result[0]["answer"]
