"""Permission-filtered discovery, private saved items and intentional feedback sharing."""

from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from atlas.auth import Identity, authenticate, require
from atlas.conversations import human, safe_message
from atlas.db import transaction

router = APIRouter(prefix="/api", tags=["discovery"])


@router.get("/discovery")
async def discover(
    q: str = Query("", max_length=200),
    kind: Literal["all", "documents", "conversations"] = "all",
    space_id: UUID | None = None,
    identity: Identity = Depends(authenticate),
):
    require(identity, "read")
    documents, conversations = [], []
    async with transaction(identity.tenant_id) as conn:
        if kind != "conversations":
            documents = await (
                await conn.execute(
                    "SELECT id,title,space_id,media_type,current_version_id,updated_at,substring(content,1,240) excerpt FROM atlas.documents WHERE tenant_id=%s AND lifecycle='active' AND status='ready' AND (%s::uuid IS NULL OR space_id=%s) AND (%s='' OR title ILIKE %s OR to_tsvector('english',content) @@ plainto_tsquery('english',%s)) ORDER BY updated_at DESC LIMIT 30",
                    (identity.tenant_id, space_id, space_id, q, "%" + q + "%", q),
                )
            ).fetchall()
        if kind != "documents" and identity.user_id:
            conversations = await (
                await conn.execute(
                    "SELECT id,title,pinned,archived,updated_at FROM atlas.conversations WHERE tenant_id=%s AND user_id=%s AND title ILIKE %s ORDER BY updated_at DESC LIMIT 30",
                    (identity.tenant_id, identity.user_id, "%" + q + "%"),
                )
            ).fetchall()
    return {"documents": documents, "conversations": conversations}


class BookmarkBody(BaseModel):
    kind: Literal["document", "message"]
    resource_id: UUID
    title: str = Field(default="", max_length=160)


async def bookmark_resource(identity, kind, resource_id):
    async with transaction(identity.tenant_id) as conn:
        if kind == "document":
            row = await (
                await conn.execute(
                    "SELECT id,title,current_version_id FROM atlas.documents WHERE tenant_id=%s AND id=%s AND lifecycle='active' AND status='ready'",
                    (identity.tenant_id, resource_id),
                )
            ).fetchone()
            return (
                {"available": True, "title": row["title"], "document_id": row["id"]}
                if row
                else {"available": False}
            )
        row = await (
            await conn.execute(
                "SELECT * FROM atlas.messages WHERE tenant_id=%s AND id=%s AND user_id=%s AND role='assistant'",
                (identity.tenant_id, resource_id, human(identity)),
            )
        ).fetchone()
    if not row:
        return {"available": False}
    message = await safe_message(identity, row)
    if message["status"] == "unavailable":
        return {"available": False}
    return {
        "available": True,
        "conversation_id": message["conversation_id"],
        "content": message["content"],
        "sources": message["sources"],
        "title": message["metadata"].get("question", "Saved answer")[:160],
    }


@router.get("/bookmarks")
async def bookmarks(identity: Identity = Depends(authenticate)):
    user_id = human(identity)
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT * FROM atlas.bookmarks WHERE tenant_id=%s AND user_id=%s ORDER BY created_at DESC LIMIT 200",
                (identity.tenant_id, user_id),
            )
        ).fetchall()
    result = []
    for row in rows:
        resource = await bookmark_resource(identity, row["kind"], row["resource_id"])
        item = {**row, **resource}
        if not resource["available"]:
            item["title"] = "Unavailable saved item"
        result.append(item)
    return {"items": result}


@router.post("/bookmarks", status_code=201)
async def save_bookmark(body: BookmarkBody, identity: Identity = Depends(authenticate)):
    user_id = human(identity)
    require(identity, "read")
    resource = await bookmark_resource(identity, body.kind, body.resource_id)
    if not resource["available"]:
        raise HTTPException(404, "This item is unavailable")
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "INSERT INTO atlas.bookmarks(tenant_id,id,user_id,kind,resource_id,title) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,user_id,kind,resource_id) DO UPDATE SET title=excluded.title RETURNING *",
                (
                    identity.tenant_id,
                    uuid4(),
                    user_id,
                    body.kind,
                    body.resource_id,
                    body.title or resource["title"],
                ),
            )
        ).fetchone()
    assert row
    return {"bookmark": {**row, **resource}}


@router.delete("/bookmarks/{bookmark_id}")
async def delete_bookmark(bookmark_id: UUID, identity: Identity = Depends(authenticate)):
    user_id = human(identity)
    async with transaction(identity.tenant_id) as conn:
        await conn.execute(
            "DELETE FROM atlas.bookmarks WHERE tenant_id=%s AND id=%s AND user_id=%s",
            (identity.tenant_id, bookmark_id, user_id),
        )
    return {"deleted": True}


class FeedbackBody(BaseModel):
    message_id: UUID
    rating: Literal[-1, 1]
    reason: Literal[
        "", "no_source", "wrong_source", "unsupported", "stale", "permission", "helpful"
    ] = ""
    correction: str = Field(default="", max_length=2000)


@router.post("/feedback", status_code=201)
async def feedback(body: FeedbackBody, identity: Identity = Depends(authenticate)):
    user_id = human(identity)
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "SELECT * FROM atlas.messages WHERE tenant_id=%s AND id=%s AND user_id=%s AND role='assistant'",
                (identity.tenant_id, body.message_id, user_id),
            )
        ).fetchone()
    if not row:
        raise HTTPException(404, "Answer not found")
    message = await safe_message(identity, row)
    if message["status"] == "unavailable":
        raise HTTPException(409, "Evidence access has changed; this answer cannot be shared")
    async with transaction(identity.tenant_id) as conn:
        saved = await (
            await conn.execute(
                "INSERT INTO atlas.feedback(tenant_id,id,user_id,message_id,query_id,rating,reason,correction,question,sources) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (
                    identity.tenant_id,
                    uuid4(),
                    user_id,
                    body.message_id,
                    row["request_id"],
                    body.rating,
                    body.reason,
                    body.correction,
                    row["metadata"].get("question", ""),
                    Jsonb(message["sources"]),
                ),
            )
        ).fetchone()
    assert saved
    return {"id": saved["id"], "message": "Feedback shared with company administrators"}


@router.get("/notifications")
async def notifications(identity: Identity = Depends(authenticate)):
    user_id = human(identity)
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT id,kind,title,body,link,read_at,created_at FROM atlas.notifications WHERE tenant_id=%s AND user_id=%s ORDER BY created_at DESC LIMIT 100",
                (identity.tenant_id, user_id),
            )
        ).fetchall()
    return {"items": rows}


@router.post("/notifications/read-all")
async def read_all(identity: Identity = Depends(authenticate)):
    user_id = human(identity)
    async with transaction(identity.tenant_id) as conn:
        await conn.execute(
            "UPDATE atlas.notifications SET read_at=now() WHERE tenant_id=%s AND user_id=%s AND read_at IS NULL",
            (identity.tenant_id, user_id),
        )
    return {"read": True}


@router.post("/notifications/{notification_id}/read")
async def read_notification(notification_id: UUID, identity: Identity = Depends(authenticate)):
    user_id = human(identity)
    async with transaction(identity.tenant_id) as conn:
        await conn.execute(
            "UPDATE atlas.notifications SET read_at=now() WHERE tenant_id=%s AND id=%s AND user_id=%s",
            (identity.tenant_id, notification_id, user_id),
        )
    return {"read": True}
