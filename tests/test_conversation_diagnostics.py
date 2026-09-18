"""Real persisted failure diagnostics with explicitly stubbed retrieval and generation."""

import json
from uuid import uuid4

import psycopg
import pytest
import test_resource_access
from fastapi import HTTPException
from test_conversations import consume, request, source_document

from atlas.api import Query, query
from atlas.config import settings
from atlas.conversations import (
    ConversationCreate,
    conversation_detail,
    create_conversation,
    failure_diagnostics,
)
from atlas.db import bind_identity

resource_workspace = test_resource_access.resource_workspace


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario", ["retrieving", "generating", "stopped", "success", "preflight"]
)
async def test_failure_metadata_is_structured_without_exception_details(
    database, resource_workspace, monkeypatch, scenario
):
    import atlas.api as api_module

    ws = resource_workspace
    principal = ws.identities[2]
    bind_identity(principal)
    source = source_document(ws.tenant)
    thread = (await create_conversation(ConversationCreate(), principal))["conversation"]
    secret = "DO-NOT-STORE-EXCEPTION-SECRET"  # pragma: allowlist secret -- synthetic leak sentinel

    async def retrieve(*args, **kwargs):
        if scenario == "retrieving":
            raise RuntimeError(secret)
        return [source], [1.0] + [0.0] * 383, str(uuid4()), 1

    async def generate(*args):
        yield {"type": "delta", "text": "A permitted answer [1]."}
        if scenario == "generating":
            raise RuntimeError(secret)
        if scenario == "stopped":
            with psycopg.connect(settings.database_admin_url) as conn:
                conn.execute(
                    "UPDATE atlas.messages SET metadata=metadata || '{\"cancel_requested\":true}'::jsonb WHERE tenant_id=%s AND conversation_id=%s AND role='assistant'",
                    (ws.tenant, thread["id"]),
                )
            yield {"type": "delta", "text": "MUST_NOT_EMIT_AFTER_STOP"}

    async def reject(*args):
        raise HTTPException(402, secret)

    monkeypatch.setattr(api_module, "retrieve", retrieve)
    monkeypatch.setattr(api_module, "generate", generate)
    body = Query(
        question="What is the evidence?",
        conversation_id=thread["id"],
        idempotency_key=str(uuid4()),
        use_cache=False,
    )
    if scenario == "preflight":
        monkeypatch.setattr(api_module.serving, "reserve", reject)
        with pytest.raises(HTTPException):
            await query(body, request(), principal)
        output = ""
    else:
        output = await consume(await query(body, request(), principal))
    assert secret not in output
    assert "MUST_NOT_EMIT_AFTER_STOP" not in output
    with psycopg.connect(settings.database_admin_url) as conn:
        metadata = conn.execute(
            "SELECT metadata FROM atlas.messages WHERE tenant_id=%s AND conversation_id=%s AND role='assistant'",
            (ws.tenant, thread["id"]),
        ).fetchone()[0]
    assert secret not in json.dumps(metadata)
    if scenario == "success":
        assert "failure_stage" not in metadata and "error_code" not in metadata
    else:
        assert metadata["failure_stage"] == ("generating" if scenario == "stopped" else scenario)
        assert metadata["error_code"] == {
            "stopped": "stopped",
            "preflight": "request_rejected",
        }.get(scenario, "internal_error")
        visible = await conversation_detail(thread["id"], principal)
        assistant = next(row for row in visible["messages"] if row["role"] == "assistant")
        assert assistant["metadata"]["failure_stage"] == metadata["failure_stage"]
        assert assistant["metadata"]["error_code"] == metadata["error_code"]


@pytest.mark.integration
async def test_interrupted_attempt_records_safe_labels_without_backfilling_history(
    database, resource_workspace
):
    ws = resource_workspace
    principal = ws.identities[2]
    bind_identity(principal)
    thread = (await create_conversation(ConversationCreate(), principal))["conversation"]
    with psycopg.connect(settings.database_admin_url) as conn:
        for status in ["running", "failed"]:
            conn.execute(
                "INSERT INTO atlas.messages(tenant_id,id,conversation_id,user_id,role,status,created_at) VALUES(%s,%s,%s,%s,'assistant',%s,now()-interval '1 day')",
                (ws.tenant, uuid4(), thread["id"], principal.user_id, status),
            )
    detail = await conversation_detail(thread["id"], principal)
    recovered = [
        message for message in detail["messages"] if message["metadata"].get("interrupted")
    ]
    assert len(recovered) == 1
    assert recovered[0]["metadata"]["failure_stage"] == "interrupted"
    assert recovered[0]["metadata"]["error_code"] == "worker_interrupted"
    old = next(
        message for message in detail["messages"] if not message["metadata"].get("interrupted")
    )
    assert old["metadata"] == {}
    assert failure_diagnostics("A secret exception stage", "A secret exception detail") == {
        "failure_stage": "not_recorded",
        "error_code": "internal_error",
    }
