"""Real PostgreSQL history pagination and complete, permission-filtered Markdown export."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
import test_resource_access
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from test_conversations import source_document

from atlas.config import settings
from atlas.conversations import (
    ConversationCreate,
    conversation_detail,
    create_conversation,
    export_conversation,
)
from atlas.db import bind_identity

resource_workspace = test_resource_access.resource_workspace


@pytest.mark.integration
async def test_newest_history_cursor_full_export_and_revoked_old_evidence(
    database, resource_workspace
):
    ws = resource_workspace
    principal = ws.identities[2]
    bind_identity(principal)
    thread = (await create_conversation(ConversationCreate(title="Long history"), principal))[
        "conversation"
    ]
    other = (await create_conversation(ConversationCreate(), principal))["conversation"]
    hidden = source_document(ws.tenant, "Older sensitive evidence")
    ordered = []
    with psycopg.connect(settings.database_admin_url) as conn:
        started = datetime.now(UTC) - timedelta(days=1)
        for index in range(503):
            message_id = uuid4()
            ordered.append(message_id)
            conn.execute(
                "INSERT INTO atlas.messages(tenant_id,id,conversation_id,user_id,role,content,sources,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    ws.tenant,
                    message_id,
                    thread["id"],
                    principal.user_id,
                    "assistant" if index == 0 else "user",
                    "Private older answer" if index == 0 else f"Message {index:04d}",
                    Jsonb([hidden] if index == 0 else []),
                    started + timedelta(seconds=index),
                ),
            )
        foreign_cursor = uuid4()
        conn.execute(
            "INSERT INTO atlas.messages(tenant_id,id,conversation_id,user_id,role,content) VALUES(%s,%s,%s,%s,'user','Other conversation')",
            (ws.tenant, foreign_cursor, other["id"], principal.user_id),
        )
    newest = await conversation_detail(thread["id"], principal)
    assert [row["id"] for row in newest["messages"]] == ordered[-100:]
    collected = list(row["id"] for row in newest["messages"])
    page = newest
    while page["pagination"]["has_older"]:
        page = await conversation_detail(
            thread["id"], principal, before=page["pagination"]["before"]
        )
        collected = [row["id"] for row in page["messages"]] + collected
    assert collected == ordered
    assert page["pagination"]["before"] is None
    exported = (await export_conversation(thread["id"], principal)).body.decode()
    assert "Private older answer" in exported
    assert "Message 0502" in exported
    assert exported.count("## User") == 502
    for cursor in [foreign_cursor, uuid4()]:
        with pytest.raises(HTTPException) as error:
            await conversation_detail(thread["id"], principal, before=cursor)
        assert error.value.status_code == 404
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "UPDATE atlas.documents SET restricted=true WHERE tenant_id=%s AND id=%s",
            (ws.tenant, hidden["document_id"]),
        )
    older = await conversation_detail(thread["id"], principal, before=ordered[1])
    assert older["messages"][0]["status"] == "unavailable"
    exported = (await export_conversation(thread["id"], principal)).body.decode()
    assert "Private older answer" not in exported and "Message 0502" in exported
    bind_identity(ws.identities[0])
    with pytest.raises(HTTPException) as error:
        await conversation_detail(thread["id"], ws.identities[0], before=ordered[1])
    assert error.value.status_code == 404
