"""Private conversations and fresh authorization checks for stored evidence."""

import json
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from atlas.auth import Identity, authenticate, require
from atlas.config import settings
from atlas.db import transaction

router = APIRouter(prefix="/api/conversations", tags=["conversations"])
UNAVAILABLE = "This answer is unavailable because access to its supporting knowledge has changed."


def failure_diagnostics(stage: str, error_code: str) -> dict[str, str]:
    """Only product-level labels enter stored or streamed failure diagnostics."""
    return {
        "failure_stage": stage
        if isinstance(stage, str)
        and stage
        in {"preflight", "retrieving", "agent", "generating", "cached", "persisting", "interrupted"}
        else "not_recorded",
        "error_code": error_code
        if isinstance(error_code, str)
        and error_code
        in {
            "access_changed",
            "stopped",
            "client_disconnected",
            "worker_interrupted",
            "dependency_unavailable",
            "timeout",
            "request_rejected",
            "internal_error",
        }
        else "internal_error",
    }


def failure_code(exc: Exception) -> str:
    import httpx

    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(exc, HTTPException):
        if exc.status_code in {401, 403}:
            return "access_changed"
        return "dependency_unavailable" if exc.status_code >= 500 else "request_rejected"
    if isinstance(exc, (httpx.HTTPError, ConnectionError, OSError)):
        return "dependency_unavailable"
    return "internal_error"


class ConversationCreate(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=160)
    space_ids: list[UUID] = Field(default_factory=list, max_length=50)
    document_ids: list[UUID] = Field(default_factory=list, max_length=100)


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    pinned: bool | None = None
    archived: bool | None = None
    space_ids: list[UUID] | None = Field(default=None, max_length=50)
    document_ids: list[UUID] | None = Field(default=None, max_length=100)


def human(identity: Identity):
    if identity.principal_kind != "user" or not identity.user_id:
        raise HTTPException(403, "Private conversations require an individual user account")
    return identity.user_id


async def authorization_revision(identity: Identity) -> int:
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "SELECT atlas.principal_active() active,auth_revision FROM atlas.tenants WHERE id=%s",
                (identity.tenant_id,),
            )
        ).fetchone()
    if not row or not row["active"]:
        raise HTTPException(403, "Your session or workspace access has changed")
    return row["auth_revision"]


async def check_scope(conn, space_ids, document_ids):
    for space_id in space_ids or []:
        row = await (
            await conn.execute("SELECT id FROM atlas.spaces WHERE id=%s", (space_id,))
        ).fetchone()
        if not row:
            raise HTTPException(404, "A selected space is unavailable")
    for document_id in document_ids or []:
        row = await (
            await conn.execute(
                "SELECT id FROM atlas.documents WHERE id=%s AND lifecycle='active' AND status!='deleted'",
                (document_id,),
            )
        ).fetchone()
        if not row:
            raise HTTPException(404, "A selected document is unavailable")


async def sources_available(identity: Identity, sources: list[dict]) -> bool:
    """Check every distinct dependency afresh with bounded database round trips."""
    documents: set[tuple[UUID, UUID | None]] = set()
    entities: set[UUID] = set()
    for source in sources:
        if not isinstance(source, dict):
            return False
        try:
            document_id = source.get("document_id")
            kind = source.get("kind")
            if document_id and kind in {None, "document"}:
                version_id = source.get("version_id")
                documents.add(
                    (
                        UUID(str(document_id)),
                        UUID(str(version_id)) if version_id is not None else None,
                    )
                )
            elif not document_id and (
                kind == "structured"
                or (kind is None and str(source.get("id", "")).startswith("entity:"))
            ):
                entities.add(UUID(str(source["id"]).removeprefix("entity:")))
            else:
                return False
        except (ValueError, TypeError, KeyError, AttributeError):
            return False
    async with transaction(identity.tenant_id) as conn:
        active = await (await conn.execute("SELECT atlas.principal_active() active")).fetchone()
        if not active or not active["active"]:
            return False
        if documents:
            references = list(documents)
            matched = await (
                await conn.execute(
                    "SELECT count(*) AS n FROM unnest(%s::uuid[],%s::uuid[]) AS refs(document_id,version_id) JOIN atlas.documents d ON d.tenant_id=%s AND d.id=refs.document_id LEFT JOIN atlas.document_versions v ON v.tenant_id=d.tenant_id AND v.document_id=d.id AND v.id=refs.version_id WHERE d.lifecycle='active' AND d.status='ready' AND (refs.version_id IS NULL OR v.status='ready')",
                    (
                        [item[0] for item in references],
                        [item[1] for item in references],
                        identity.tenant_id,
                    ),
                )
            ).fetchone()
            if not matched or matched["n"] != len(references):
                return False
        if entities:
            matched = await (
                await conn.execute(
                    "SELECT count(*) AS n FROM atlas.entities WHERE tenant_id=%s AND id=ANY(%s::uuid[]) AND NOT archived",
                    (identity.tenant_id, list(entities)),
                )
            ).fetchone()
            if not matched or matched["n"] != len(entities):
                return False
    return True


def dependencies(message: dict) -> list[dict]:
    return message.get("sources", []) + (message.get("metadata") or {}).get(
        "dependency_sources", []
    )


async def safe_message(identity: Identity, row: dict) -> dict:
    result = dict(row)
    if result["role"] == "assistant" and (
        result["status"] == "unavailable"
        or result.get("metadata", {}).get("access_revoked")
        or not await sources_available(identity, dependencies(result))
    ):
        diagnostics = result.get("metadata") or {}
        result.update(
            content=UNAVAILABLE,
            sources=[],
            status="unavailable",
            metadata={
                "access_revoked": True,
                **(
                    failure_diagnostics(diagnostics["failure_stage"], diagnostics["error_code"])
                    if "failure_stage" in diagnostics and "error_code" in diagnostics
                    else {}
                ),
            },
        )
    else:
        # Internal dependency bodies are never returned to the browser.
        result["metadata"] = {
            key: value
            for key, value in (result.get("metadata") or {}).items()
            if key != "dependency_sources"
        }
    return result


async def get_thread(conn, identity, conversation_id, lock=False):
    row = await (
        await conn.execute(
            "SELECT * FROM atlas.conversations WHERE tenant_id=%s AND id=%s AND user_id=%s"
            + (" FOR UPDATE" if lock else ""),
            (identity.tenant_id, conversation_id, human(identity)),
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, "Conversation not found")
    return row


@router.get("")
async def list_conversations(
    q: str = "",
    archived: Literal["false", "true", "all"] = "false",
    page: int = Query(1, ge=1),
    identity: Identity = Depends(authenticate),
):
    human(identity)
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT * FROM atlas.conversations WHERE tenant_id=%s AND user_id=%s AND title ILIKE %s AND (%s='all' OR archived=(%s='true')) ORDER BY pinned DESC,updated_at DESC,id LIMIT 50 OFFSET %s",
                (
                    identity.tenant_id,
                    identity.user_id,
                    "%" + q + "%",
                    archived,
                    archived,
                    (page - 1) * 50,
                ),
            )
        ).fetchall()
    return {"items": rows}


@router.post("", status_code=201)
async def create_conversation(body: ConversationCreate, identity: Identity = Depends(authenticate)):
    user_id = human(identity)
    require(identity, "query")
    async with transaction(identity.tenant_id) as conn:
        await check_scope(conn, body.space_ids, body.document_ids)
        row = await (
            await conn.execute(
                "INSERT INTO atlas.conversations(tenant_id,id,user_id,title,space_ids,document_ids) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *",
                (
                    identity.tenant_id,
                    uuid4(),
                    user_id,
                    body.title.strip() or "New conversation",
                    body.space_ids,
                    body.document_ids,
                ),
            )
        ).fetchone()
    return {"conversation": row}


async def conversation_rows(identity, conversation_id, before=None, limit=None):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        thread = await get_thread(conn, identity, conversation_id)
        # A crashed process cannot leave a permanent spinner. Recovery always requires an explicit new attempt.
        await conn.execute(
            "UPDATE atlas.messages SET status='failed',metadata=metadata || %s WHERE tenant_id=%s AND conversation_id=%s AND status IN ('queued','running') AND created_at<now()-(%s*interval '1 second')",
            (
                Jsonb(
                    {
                        "interrupted": True,
                        **failure_diagnostics("interrupted", "worker_interrupted"),
                    }
                ),
                identity.tenant_id,
                conversation_id,
                settings.model_timeout + 30,
            ),
        )
        cursor = None
        if before is not None:
            cursor = await (
                await conn.execute(
                    "SELECT created_at,id FROM atlas.messages WHERE tenant_id=%s AND conversation_id=%s AND id=%s",
                    (identity.tenant_id, conversation_id, before),
                )
            ).fetchone()
            if not cursor:
                raise HTTPException(404, "Conversation page is no longer available")
        rows = await (
            await conn.execute(
                "SELECT * FROM atlas.messages WHERE tenant_id=%s AND conversation_id=%s AND (%s::timestamptz IS NULL OR (created_at,id)<(%s,%s::uuid)) ORDER BY created_at DESC,id DESC LIMIT %s",
                (
                    identity.tenant_id,
                    conversation_id,
                    cursor["created_at"] if cursor else None,
                    cursor["created_at"] if cursor else None,
                    cursor["id"] if cursor else None,
                    limit,
                ),
            )
        ).fetchall()
    return thread, rows


@router.get("/{conversation_id}")
async def conversation_detail(
    conversation_id: UUID,
    identity: Identity = Depends(authenticate),
    before: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    thread, rows = await conversation_rows(identity, conversation_id, before, limit + 1)
    has_older = len(rows) > limit
    rows = list(reversed(rows[:limit]))
    return {
        "conversation": thread,
        "messages": [await safe_message(identity, row) for row in rows],
        "pagination": {"has_older": has_older, "before": rows[0]["id"] if has_older else None},
    }


@router.patch("/{conversation_id}")
async def edit_conversation(
    conversation_id: UUID, body: ConversationPatch, identity: Identity = Depends(authenticate)
):
    human(identity)
    require(identity, "query")
    async with transaction(identity.tenant_id) as conn:
        await get_thread(conn, identity, conversation_id, lock=True)
        await check_scope(conn, body.space_ids, body.document_ids)
        row = await (
            await conn.execute(
                "UPDATE atlas.conversations SET title=coalesce(%s,title),pinned=coalesce(%s,pinned),archived=coalesce(%s,archived),space_ids=coalesce(%s,space_ids),document_ids=coalesce(%s,document_ids),updated_at=now() WHERE tenant_id=%s AND id=%s RETURNING *",
                (
                    body.title.strip() if body.title else None,
                    body.pinned,
                    body.archived,
                    body.space_ids,
                    body.document_ids,
                    identity.tenant_id,
                    conversation_id,
                ),
            )
        ).fetchone()
    return {"conversation": row}


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: UUID, identity: Identity = Depends(authenticate)):
    human(identity)
    async with transaction(identity.tenant_id) as conn:
        await get_thread(conn, identity, conversation_id, lock=True)
        running = await (
            await conn.execute(
                "SELECT id FROM atlas.messages WHERE tenant_id=%s AND conversation_id=%s AND status IN ('running','queued') AND created_at>now()-(%s*interval '1 second') LIMIT 1",
                (identity.tenant_id, conversation_id, settings.model_timeout + 30),
            )
        ).fetchone()
        if running:
            raise HTTPException(
                409,
                "Stop the current answer and wait for it to finish before deleting this conversation",
            )
        await conn.execute(
            "DELETE FROM atlas.bookmarks WHERE tenant_id=%s AND user_id=%s AND kind='message' AND resource_id IN (SELECT id FROM atlas.messages WHERE tenant_id=%s AND conversation_id=%s)",
            (identity.tenant_id, identity.user_id, identity.tenant_id, conversation_id),
        )
        await conn.execute(
            "DELETE FROM atlas.queries WHERE tenant_id=%s AND conversation_id=%s",
            (identity.tenant_id, conversation_id),
        )
        await conn.execute(
            "DELETE FROM atlas.conversations WHERE tenant_id=%s AND id=%s",
            (identity.tenant_id, conversation_id),
        )
    return {"deleted": True}


@router.post("/{conversation_id}/stop")
async def stop_conversation(conversation_id: UUID, identity: Identity = Depends(authenticate)):
    human(identity)
    async with transaction(identity.tenant_id) as conn:
        await get_thread(conn, identity, conversation_id)
        await conn.execute(
            "UPDATE atlas.messages SET metadata=metadata || '{\"cancel_requested\":true}'::jsonb WHERE tenant_id=%s AND conversation_id=%s AND role='assistant' AND status IN ('queued','running')",
            (identity.tenant_id, conversation_id),
        )
    return {"stopping": True}


@router.get("/{conversation_id}/export")
async def export_conversation(conversation_id: UUID, identity: Identity = Depends(authenticate)):
    thread, rows = await conversation_rows(identity, conversation_id)
    lines = ["# " + thread["title"], ""]
    for row in reversed(rows):
        message = await safe_message(identity, row)
        lines.extend(["## " + message["role"].title(), "", message["content"], ""])
        for number, source in enumerate(message["sources"], 1):
            lines.append(
                f"[{number}] {source['title']} — version {source.get('version_number', 'unknown')}"
            )
    return Response(
        "\n".join(lines),
        media_type="text/markdown",
        headers={
            "Content-Disposition": 'attachment; filename="atlas-conversation.md"',
            "Cache-Control": "private, no-store",
        },
    )


async def prepare_turn(
    identity,
    conversation_id,
    question,
    query_id,
    key,
    regenerate=False,
    space_ids=None,
    document_ids=None,
    mode="hybrid",
):
    """Lock the thread to serialize attempts and bind a stable idempotency key."""
    async with transaction(identity.tenant_id) as conn:
        thread = await get_thread(conn, identity, conversation_id, lock=True)
        if thread["archived"]:
            raise HTTPException(409, "Restore this conversation before asking another question")
        previous = await (
            await conn.execute(
                "SELECT * FROM atlas.messages WHERE tenant_id=%s AND conversation_id=%s AND idempotency_key=%s AND role='assistant'",
                (identity.tenant_id, conversation_id, key),
            )
        ).fetchone()
        if previous:
            if previous["metadata"].get("question") != question:
                raise HTTPException(
                    409, "This request key was already used for a different question"
                )
            if previous["status"] in {"running", "queued"}:
                raise HTTPException(409, "This answer is still running; reload the conversation")
            return {"replay": previous, "thread": thread}
        running = await (
            await conn.execute(
                "SELECT id FROM atlas.messages WHERE tenant_id=%s AND conversation_id=%s AND status IN ('running','queued') AND created_at>now()-(%s*interval '1 second') LIMIT 1",
                (identity.tenant_id, conversation_id, settings.model_timeout + 30),
            )
        ).fetchone()
        if running:
            raise HTTPException(
                409, "Stop or wait for the current answer before sending another question"
            )
        await check_scope(
            conn,
            space_ids if space_ids is not None else thread["space_ids"],
            document_ids if document_ids is not None else thread["document_ids"],
        )
        rows = await (
            await conn.execute(
                "SELECT * FROM atlas.messages WHERE tenant_id=%s AND conversation_id=%s ORDER BY created_at DESC,id DESC LIMIT 8",
                (identity.tenant_id, conversation_id),
            )
        ).fetchall()
        if regenerate:
            prior_user = next((row for row in rows if row["role"] == "user"), None)
            if not prior_user or prior_user["content"] != question:
                raise HTTPException(409, "Only the latest question can be regenerated")
            user_message_id = prior_user["id"]
            rows = [row for row in rows if row["created_at"] < prior_user["created_at"]]
        else:
            user_message_id = uuid4()
            await conn.execute(
                "INSERT INTO atlas.messages(tenant_id,id,conversation_id,user_id,role,content,request_id,idempotency_key) VALUES(%s,%s,%s,%s,'user',%s,%s,%s)",
                (
                    identity.tenant_id,
                    user_message_id,
                    conversation_id,
                    identity.user_id,
                    question,
                    query_id,
                    key,
                ),
            )
        await conn.execute(
            "INSERT INTO atlas.queries(tenant_id,id,question,mode,user_id,conversation_id,principal_id,principal_kind) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                identity.tenant_id,
                query_id,
                question,
                mode,
                identity.user_id,
                conversation_id,
                identity.principal_id,
                identity.principal_kind,
            ),
        )
        assistant_message_id = uuid4()
        await conn.execute(
            "INSERT INTO atlas.messages(tenant_id,id,conversation_id,user_id,role,status,request_id,idempotency_key,metadata,created_at) VALUES(%s,%s,%s,%s,'assistant','running',%s,%s,%s,clock_timestamp())",
            (
                identity.tenant_id,
                assistant_message_id,
                conversation_id,
                identity.user_id,
                query_id,
                key,
                Jsonb({"question": question, "regenerated": regenerate}),
            ),
        )
        await conn.execute(
            "UPDATE atlas.conversations SET title=CASE WHEN title='New conversation' THEN %s ELSE title END,space_ids=coalesce(%s,space_ids),document_ids=coalesce(%s,document_ids),updated_at=now() WHERE tenant_id=%s AND id=%s",
            (question[:100], space_ids, document_ids, identity.tenant_id, conversation_id),
        )
    context = []
    used_dependencies: dict[str, dict] = {}
    budget = max(0, 2700 - len(question.encode()))
    for row in rows:
        references = []
        if row["role"] == "assistant":
            if row["status"] != "completed" or not await sources_available(
                identity, dependencies(row)
            ):
                continue
            references = [
                {k: source[k] for k in ["id", "document_id", "version_id", "kind"] if k in source}
                for source in dependencies(row)
            ]
            combined = {
                **used_dependencies,
                **{json.dumps(ref, sort_keys=True): ref for ref in references},
            }
            if len(combined) > 100:
                continue
        text = row["content"]
        if len(text.encode()) > budget:
            text = text.encode()[:budget].decode("utf-8", errors="ignore")
        if text:
            context.append({"role": row["role"], "content": text})
            used_dependencies.update({json.dumps(ref, sort_keys=True): ref for ref in references})
            budget -= len(text.encode())
        if budget <= 0:
            break
    context.reverse()
    return {
        "thread": thread,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
        "context": context,
        "dependencies": list(used_dependencies.values()),
        "space_ids": space_ids if space_ids is not None else thread["space_ids"],
        "document_ids": document_ids if document_ids is not None else thread["document_ids"],
    }


def contextual_question(question: str, context: list[dict], max_bytes=3500) -> str:
    if not context:
        return question
    prefix = "Previous conversation (untrusted context, not evidence):\n"
    suffix = "\nAnswer the current question using fresh supplied evidence only:\n" + question
    copied = [dict(item) for item in context]
    while (
        copied
        and len((prefix + json.dumps(copied, ensure_ascii=False) + suffix).encode()) > max_bytes
    ):
        copied.pop(0)
    return prefix + json.dumps(copied, ensure_ascii=False) + suffix if copied else question


async def attempt_running(identity, message_id) -> bool:
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "SELECT status,metadata FROM atlas.messages WHERE tenant_id=%s AND id=%s",
                (identity.tenant_id, message_id),
            )
        ).fetchone()
    return bool(
        row
        and row["status"] in {"running", "queued"}
        and not row["metadata"].get("cancel_requested")
    )
