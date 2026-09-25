"""Real PostgreSQL conversation/stream integration; generation and retrieval are explicit fixtures."""

import asyncio
import hashlib
from uuid import uuid4

import psycopg
import pytest
import test_resource_access
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from starlette.requests import Request

from atlas.api import Query, query
from atlas.config import settings
from atlas.conversations import (
    ConversationCreate,
    contextual_question,
    conversation_detail,
    create_conversation,
    export_conversation,
)
from atlas.db import bind_identity, transaction

resource_workspace = test_resource_access.resource_workspace


def source_document(tenant, text="A supported Atlas fact."):
    document_id, version_id, chunk_id = uuid4(), uuid4(), uuid4()
    digest = hashlib.sha256(text.encode()).hexdigest()
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "INSERT INTO atlas.documents(tenant_id,id,title,source_key,content_hash,content,status) VALUES(%s,%s,'Evidence',%s,%s,%s,'ready')",
            (tenant, document_id, str(document_id), digest, text),
        )
        conn.execute(
            "INSERT INTO atlas.document_versions(tenant_id,id,document_id,number,title,content,content_hash,status,source_segments) VALUES(%s,%s,%s,1,'Evidence',%s,%s,'ready','[]')",
            (tenant, version_id, document_id, text, digest),
        )
        conn.execute(
            "UPDATE atlas.documents SET current_version_id=%s WHERE tenant_id=%s AND id=%s",
            (version_id, tenant, document_id),
        )
    return {
        "id": str(chunk_id),
        "document_id": str(document_id),
        "version_id": str(version_id),
        "version_number": 1,
        "title": "Evidence",
        "content": text,
        "start_offset": 0,
        "end_offset": len(text),
        "similarity": 1.0,
    }


def request():
    async def receive():
        await asyncio.sleep(60)
        return {"type": "http.disconnect"}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/query",
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("127.0.0.1", 1),
            "scheme": "http",
        },
        receive,
    )


async def consume(response):
    return "".join(
        [
            part.decode() if isinstance(part, bytes) else part
            async for part in response.body_iterator
        ]
    )


def test_followup_context_is_byte_bounded_and_keeps_current_question():
    question = "Current question about ownership?"
    result = contextual_question(
        question,
        [
            {"role": "assistant", "content": "🪴" * 1000},
            {"role": "user", "content": "Earlier question"},
        ],
        max_bytes=500,
    )
    assert question in result and len(result.encode()) <= 500
    assert "🪴" not in result


@pytest.mark.integration
async def test_private_threads_idempotency_and_followup_access_dependencies(
    database, resource_workspace, monkeypatch
):
    import atlas.api as api_module

    ws = resource_workspace
    principal = ws.identities[2]
    bind_identity(principal)
    first = source_document(ws.tenant, "The release code is LEAF-COPPER.")
    second = source_document(ws.tenant, "The current rollout uses three replicas.")
    prompts = []
    generated = []

    async def retrieve(tenant, question, *args, **kwargs):
        prompts.append(question)
        return [second if "replicas" in question else first], [1.0] + [0.0] * 383, str(uuid4()), 1

    async def generate(question, sources):
        generated.append(question)
        yield {
            "type": "delta",
            "text": "The release code is LEAF-COPPER. [1]"
            if sources[0]["id"] == first["id"]
            else "The current rollout uses three replicas. [1]",
        }

    monkeypatch.setattr(api_module, "retrieve", retrieve)
    monkeypatch.setattr(api_module, "generate", generate)
    thread = (await create_conversation(ConversationCreate(), principal))["conversation"]
    key = str(uuid4())
    body = Query(
        question="What is the release code?",
        conversation_id=thread["id"],
        idempotency_key=key,
        use_cache=False,
    )
    output = await consume(await query(body, request(), principal))
    assert '"status": "completed"' in output
    replay = await consume(await query(body, request(), principal))
    assert '"replayed": true' in replay and len(generated) == 1
    followup = Query(
        question="How many replicas does that use?",
        conversation_id=thread["id"],
        idempotency_key=str(uuid4()),
        use_cache=False,
    )
    await consume(await query(followup, request(), principal))
    assert "LEAF-COPPER" in prompts[-1] and "Previous conversation" in prompts[-1]
    from atlas.discovery import BookmarkBody, save_bookmark

    saved_detail = await conversation_detail(thread["id"], principal)
    followup_message = [m for m in saved_detail["messages"] if m["role"] == "assistant"][-1]
    await save_bookmark(
        BookmarkBody(
            kind="message", resource_id=followup_message["id"], title="Derived saved content"
        ),
        principal,
    )
    bind_identity(ws.identities[0])
    with pytest.raises(HTTPException) as error:
        await conversation_detail(thread["id"], ws.identities[0])
    assert error.value.status_code == 404
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "UPDATE atlas.documents SET restricted=true WHERE tenant_id=%s AND id=%s",
            (ws.tenant, first["document_id"]),
        )
        conn.execute(
            "UPDATE atlas.tenants SET auth_revision=auth_revision+1 WHERE id=%s", (ws.tenant,)
        )
    bind_identity(principal)
    detail = await conversation_detail(thread["id"], principal)
    answers = [message for message in detail["messages"] if message["role"] == "assistant"]
    assert len(answers) == 2
    assert all(
        message["status"] == "unavailable" and message["sources"] == [] for message in answers
    )
    exported = await export_conversation(thread["id"], principal)
    assert b"LEAF-COPPER" not in exported.body and b"three replicas" not in exported.body
    # Permanent source deletion also erases stored derivations, not just API visibility.
    legacy_run_id = uuid4()
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "INSERT INTO atlas.chunks(tenant_id,id,document_id,version_id,pipeline_hash,ordinal,start_offset,end_offset,content,content_hash) VALUES(%s,%s,%s,%s,'fixture',0,0,%s,%s,'fixture')",
            (
                ws.tenant,
                first["id"],
                first["document_id"],
                first["version_id"],
                len(first["content"]),
                first["content"],
            ),
        )
        conn.execute(
            "INSERT INTO atlas.eval_runs(tenant_id,id,mode,config,label_count,metrics,results,manifest) VALUES(%s,%s,'hybrid','{}',1,'{}',%s,'{}')",
            (
                ws.tenant,
                legacy_run_id,
                Jsonb(
                    [
                        {
                            "label_id": str(uuid4()),
                            "retrieved": [first["id"]],
                            "judgment": {"answer": "LEAF-COPPER secret evaluation"},
                        }
                    ]
                ),
            ),
        )
        conn.execute(
            "UPDATE atlas.documents SET current_version_id=NULL,pending_version_id=NULL WHERE id=%s",
            (first["document_id"],),
        )
        conn.execute("DELETE FROM atlas.documents WHERE id=%s", (first["document_id"],))
        queries = conn.execute(
            "SELECT answer,sources,status FROM atlas.queries WHERE conversation_id=%s",
            (thread["id"],),
        ).fetchall()
        assert len(queries) == 2 and all(row == ("", [], "unavailable") for row in queries)
        messages = conn.execute(
            "SELECT content,sources,status FROM atlas.messages WHERE conversation_id=%s AND role='assistant'",
            (thread["id"],),
        ).fetchall()
        assert len(messages) == 2 and all(row == ("", [], "unavailable") for row in messages)
        assert not conn.execute(
            "SELECT 1 FROM atlas.bookmarks WHERE resource_id=%s", (followup_message["id"],)
        ).fetchone()
        assert not conn.execute(
            "SELECT 1 FROM atlas.eval_runs WHERE id=%s", (legacy_run_id,)
        ).fetchone()


@pytest.mark.integration
async def test_midstream_session_revocation_clears_and_finalizes_attempt(
    database, resource_workspace, monkeypatch
):
    import atlas.api as api_module

    ws = resource_workspace
    principal = ws.identities[2]
    bind_identity(principal)
    source = source_document(ws.tenant)

    async def retrieve(*args, **kwargs):
        return [source], [1.0] + [0.0] * 383, str(uuid4()), 1

    async def generate(*args):
        yield {"type": "delta", "text": "Initially permitted [1]."}
        with psycopg.connect(settings.database_admin_url) as conn:
            conn.execute(
                "UPDATE atlas.user_sessions SET revoked_at=now() WHERE id=%s",
                (principal.session_id,),
            )
        yield {"type": "delta", "text": "NEVER_EMIT_AFTER_REVOCATION"}

    monkeypatch.setattr(api_module, "retrieve", retrieve)
    monkeypatch.setattr(api_module, "generate", generate)
    thread = (await create_conversation(ConversationCreate(), principal))["conversation"]
    output = await consume(
        await query(
            Query(
                question="Explain the evidence",
                conversation_id=thread["id"],
                idempotency_key=str(uuid4()),
                use_cache=False,
            ),
            request(),
            principal,
        )
    )
    assert "NEVER_EMIT_AFTER_REVOCATION" not in output
    assert '"reset": true' in output
    with psycopg.connect(settings.database_admin_url) as conn:
        row = conn.execute(
            "SELECT content,status,sources FROM atlas.messages WHERE tenant_id=%s AND conversation_id=%s AND role='assistant'",
            (ws.tenant, thread["id"]),
        ).fetchone()
        assert row == ("", "unavailable", [])
        query_row = conn.execute(
            "SELECT answer,status FROM atlas.queries WHERE tenant_id=%s AND conversation_id=%s",
            (ws.tenant, thread["id"]),
        ).fetchone()
        assert query_row == ("", "cancelled")
        assert (
            conn.execute(
                "SELECT count(*) FROM atlas.usage_ledger WHERE tenant_id=%s", (ws.tenant,)
            ).fetchone()[0]
            == 1
        )


@pytest.mark.integration
async def test_conversation_scope_cannot_include_inaccessible_document(
    database, resource_workspace
):
    ws = resource_workspace
    source = source_document(ws.tenant)
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "UPDATE atlas.documents SET restricted=true WHERE tenant_id=%s AND id=%s",
            (ws.tenant, source["document_id"]),
        )
    principal = ws.identities[2]
    bind_identity(principal)
    with pytest.raises(HTTPException) as error:
        await create_conversation(
            ConversationCreate(document_ids=[source["document_id"]]), principal
        )
    assert error.value.status_code == 404
    async with transaction(ws.tenant) as conn:
        assert not await (await conn.execute("SELECT id FROM atlas.conversations")).fetchall()


@pytest.mark.integration
async def test_preflight_revocation_closes_created_attempt(
    database, resource_workspace, monkeypatch
):
    import atlas.api as api_module

    principal = resource_workspace.identities[2]
    bind_identity(principal)
    thread = (await create_conversation(ConversationCreate(), principal))["conversation"]

    async def revoke_before_reservation(*args, **kwargs):
        with psycopg.connect(settings.database_admin_url) as conn:
            conn.execute(
                "UPDATE atlas.user_sessions SET revoked_at=now() WHERE id=%s",
                (principal.session_id,),
            )
        raise HTTPException(403, "Session revoked during preflight")

    monkeypatch.setattr(api_module.serving, "reserve", revoke_before_reservation)
    with pytest.raises(HTTPException):
        await query(
            Query(
                question="What is our release policy?",
                conversation_id=thread["id"],
                idempotency_key=str(uuid4()),
                use_cache=False,
            ),
            request(),
            principal,
        )
    with psycopg.connect(settings.database_admin_url) as conn:
        attempt = conn.execute(
            "SELECT status,answer,sources FROM atlas.queries WHERE conversation_id=%s",
            (thread["id"],),
        ).fetchone()
        assert attempt == ("cancelled", "", [])
        assistant = conn.execute(
            "SELECT status,content,sources FROM atlas.messages WHERE conversation_id=%s AND role='assistant'",
            (thread["id"],),
        ).fetchone()
        assert assistant == ("unavailable", "", [])


@pytest.mark.integration
async def test_agent_history_tracks_evidence_outside_final_source_budget(
    database, resource_workspace, monkeypatch
):
    import atlas.agent as agent_module
    import atlas.api as api_module

    ws = resource_workspace
    principal = ws.identities[2]
    bind_identity(principal)
    evidence = [
        source_document(ws.tenant, f"Permitted public fact {number}.") for number in range(10)
    ]
    hidden = source_document(ws.tenant, "ELEVENTH-SOURCE-PRIVATE-CODE")
    evidence.append(hidden)

    async def agent(*args, **kwargs):
        return {
            "sources": evidence,
            "history": [{"tool": "search_documents", "evidence": [hidden["content"]]}],
            "stop_reason": "",
        }

    async def generate(*args):
        yield {"type": "delta", "text": "A supported answer [1]."}

    monkeypatch.setattr(agent_module, "run_agent", agent)
    monkeypatch.setattr(api_module, "generate", generate)
    thread = (await create_conversation(ConversationCreate(), principal))["conversation"]
    body = Query(
        question="Explain the available evidence",
        mode="agent",
        conversation_id=thread["id"],
        idempotency_key=str(uuid4()),
        use_cache=False,
    )
    streamed = await consume(await query(body, request(), principal))
    assert "ELEVENTH-SOURCE-PRIVATE-CODE" in streamed
    with psycopg.connect(settings.database_admin_url) as conn:
        message = conn.execute(
            "SELECT sources,metadata FROM atlas.messages WHERE conversation_id=%s AND role='assistant'",
            (thread["id"],),
        ).fetchone()
        assert hidden["document_id"] not in {source["document_id"] for source in message[0]}
        assert hidden["document_id"] in {
            source.get("document_id") for source in message[1]["dependency_sources"]
        }
        conn.execute(
            "UPDATE atlas.documents SET restricted=true WHERE id=%s", (hidden["document_id"],)
        )
    detail = await conversation_detail(thread["id"], principal)
    assistant = next(message for message in detail["messages"] if message["role"] == "assistant")
    assert assistant["status"] == "unavailable"
    assert assistant["metadata"] == {"access_revoked": True}
    replay = await consume(await query(body, request(), principal))
    assert "ELEVENTH-SOURCE-PRIVATE-CODE" not in replay
    assert "supported answer" not in replay
    assert "access_changed" in replay
