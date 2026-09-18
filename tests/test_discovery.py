"""Real PostgreSQL saved-item, discovery and notification isolation checks."""

from uuid import UUID, uuid4

import psycopg
import pytest
import test_resource_access
from psycopg.types.json import Jsonb
from test_conversations import source_document

from atlas.config import settings
from atlas.conversations import ConversationCreate, create_conversation
from atlas.db import bind_identity
from atlas.discovery import (
    BookmarkBody,
    bookmarks,
    discover,
    notifications,
    read_notification,
    save_bookmark,
)

resource_workspace = test_resource_access.resource_workspace


@pytest.mark.integration
async def test_saved_answer_is_redacted_after_dependency_access_loss(database, resource_workspace):
    ws = resource_workspace
    principal = ws.identities[2]
    bind_identity(principal)
    hidden = source_document(ws.tenant, "Sensitive source phrase.")
    visible = source_document(ws.tenant, "Unrestricted current evidence.")
    conversation = (
        await create_conversation(ConversationCreate(title="Private question"), principal)
    )["conversation"]
    message_id = uuid4()
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "INSERT INTO atlas.messages(tenant_id,id,conversation_id,user_id,role,content,sources,metadata) VALUES(%s,%s,%s,%s,'assistant','Sensitive answer [1].',%s,%s)",
            (
                ws.tenant,
                message_id,
                conversation["id"],
                principal.user_id,
                Jsonb([visible]),
                Jsonb({"question": "Private question", "dependency_sources": [hidden]}),
            ),
        )
    saved = await save_bookmark(BookmarkBody(kind="message", resource_id=message_id), principal)
    assert saved["bookmark"]["available"]
    assert (await bookmarks(principal))["items"][0]["content"] == "Sensitive answer [1]."
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "UPDATE atlas.documents SET restricted=true WHERE tenant_id=%s AND id=%s",
            (ws.tenant, hidden["document_id"]),
        )
    items = (await bookmarks(principal))["items"]
    assert len(items) == 1 and items[0]["available"] is False
    assert items[0]["title"] == "Unavailable saved item"
    assert "content" not in items[0] and "sources" not in items[0]
    bind_identity(ws.identities[0])
    assert (await bookmarks(ws.identities[0]))["items"] == []


@pytest.mark.integration
async def test_discovery_and_notification_reads_keep_user_boundaries(database, resource_workspace):
    ws = resource_workspace
    viewer, other = ws.identities[2:]
    bind_identity(viewer)
    document = source_document(ws.tenant, "A search phrase.")
    thread = (
        await create_conversation(ConversationCreate(title="Only my private thread"), viewer)
    )["conversation"]
    notice = uuid4()
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "INSERT INTO atlas.notifications(tenant_id,id,user_id,kind,title,dedupe_key) VALUES(%s,%s,%s,'test','Private notification',%s)",
            (ws.tenant, notice, viewer.user_id, str(notice)),
        )
    result = await discover(q="", kind="all", space_id=None, identity=viewer)
    assert str(result["documents"][0]["id"]) == document["document_id"]
    assert result["conversations"][0]["id"] == thread["id"]
    bind_identity(other)
    other_result = await discover(q="", kind="all", space_id=None, identity=other)
    assert other_result["conversations"] == []
    assert (await notifications(other))["items"] == []
    await read_notification(notice, other)
    with psycopg.connect(settings.database_admin_url) as conn:
        assert (
            conn.execute(
                "SELECT read_at FROM atlas.notifications WHERE id=%s", (notice,)
            ).fetchone()[0]
            is None
        )
        conn.execute(
            "UPDATE atlas.documents SET restricted=true WHERE tenant_id=%s AND id=%s",
            (ws.tenant, UUID(document["document_id"])),
        )
    assert (await discover(q="", kind="documents", space_id=None, identity=other))[
        "documents"
    ] == []
