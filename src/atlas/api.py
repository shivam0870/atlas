import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi import Query as QueryParam
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from atlas import serving
from atlas.auth import Identity, authenticate, require
from atlas.config import settings
from atlas.db import transaction
from atlas.evidence_safety import VERIFIER_REVISION, evidence_fallback, verify_answer
from atlas.generation import generate
from atlas.ingestion import collection, create_document, pipeline_hash
from atlas.retrieval import retrieve
from atlas.telemetry import cache_hits, latency, tokens, tracer

router = APIRouter(prefix="/api")


@router.get("/local/workspaces")
@router.post("/local/connect/{tenant_id}")
@router.post("/session")
async def retired_workspace_login():
    raise HTTPException(
        410,
        "Workspace-key browser login has been replaced by individual accounts. Sign in at /login.",
    )


@router.delete("/session")
async def logout(response: Response):
    response.delete_cookie("atlas_session")
    return {"ok": True, "message": "Use /api/auth/logout for individual sessions"}


class TextDocument(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=2_000_000)


@router.post("/documents/text", status_code=202)
async def upload_text(
    body: TextDocument, request: Request, identity: Identity = Depends(authenticate)
):
    require(identity, "write")
    if not body.content.strip() or "\x00" in body.content:
        raise HTTPException(422, "Document cannot be empty")
    if len(body.content.encode()) > settings.max_upload_bytes:
        raise HTTPException(413, "Document exceeds the upload size limit")
    quota = await serving.limits(identity.tenant_id)
    await serving.rate_limit(identity.tenant_id, quota["requests_per_minute"])
    row = await create_document(
        identity.tenant_id,
        body.title,
        body.content,
        body.title,
        queued=True,
        traceparent=request.headers.get("traceparent"),
    )
    return {
        "id": str(row["id"]),
        "title": body.title,
        "job_id": row["job_id"],
        "status": row["status"],
    }


@router.post("/documents/upload", status_code=202)
async def upload_file(
    request: Request, file: UploadFile = File(...), identity: Identity = Depends(authenticate)
):
    require(identity, "write")
    filename = Path(file.filename or "document.txt").name
    if Path(filename).suffix.lower() not in {".txt", ".md", ".markdown"}:
        raise HTTPException(415, "Upload a UTF-8 .txt or Markdown file")
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, "Document exceeds the upload size limit")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "The file must use UTF-8 encoding") from exc
    if not content.strip() or "\x00" in content:
        raise HTTPException(422, "Upload a nonempty text document")
    quota = await serving.limits(identity.tenant_id)
    await serving.rate_limit(identity.tenant_id, quota["requests_per_minute"])
    row = await create_document(
        identity.tenant_id,
        filename,
        content,
        filename,
        queued=True,
        traceparent=request.headers.get("traceparent"),
    )
    return {
        "id": str(row["id"]),
        "title": filename,
        "job_id": row["job_id"],
        "status": row["status"],
    }


@router.get("/documents")
async def documents(identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                """SELECT d.id,d.title,d.source_key,d.status,d.created_at,d.updated_at,
            length(d.content) characters,d.metadata,count(c.id) chunks FROM atlas.documents d
            LEFT JOIN atlas.chunks c ON c.tenant_id=d.tenant_id AND c.document_id=d.id AND c.pipeline_hash=%s AND c.version_id=d.current_version_id
            WHERE d.tenant_id=%s AND d.status!='deleted' AND d.lifecycle='active' GROUP BY d.tenant_id,d.id ORDER BY d.created_at DESC""",
                (pipeline_hash(), identity.tenant_id),
            )
        ).fetchall()
    return jsonable_encoder(rows)


@router.get("/documents/{document_id}")
async def document(document_id: UUID, identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "SELECT * FROM atlas.documents WHERE tenant_id=%s AND id=%s AND status!='deleted' AND lifecycle='active'",
                (identity.tenant_id, document_id),
            )
        ).fetchone()
    if not row:
        raise HTTPException(404, "Document not found")
    return jsonable_encoder(row)


@router.delete("/documents/{document_id}")
async def delete_document(document_id: UUID, identity: Identity = Depends(authenticate)):
    require(identity, "write")
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "UPDATE atlas.documents SET status='deleted',lifecycle='trashed',updated_at=now() WHERE tenant_id=%s AND id=%s AND status!='deleted' RETURNING id",
                (identity.tenant_id, document_id),
            )
        ).fetchone()
        if not row:
            raise HTTPException(404, "Document not found")
        await conn.execute(
            "UPDATE atlas.collections SET revision=revision+1 WHERE tenant_id=%s",
            (identity.tenant_id,),
        )
        await conn.execute(
            "UPDATE atlas.tenants SET auth_revision=auth_revision+1 WHERE id=%s",
            (identity.tenant_id,),
        )
    return {"ok": True}


@router.get("/chunks/{chunk_id}")
async def chunk(chunk_id: UUID, identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "SELECT c.*,d.title,v.number version_number,v.source_segments,v.content version_content,v.created_at version_created_at,d.updated_at document_updated_at,d.review_due_at,v.publication_status,v.effective_at,(v.id IS DISTINCT FROM d.current_version_id OR v.publication_status<>'published') historical FROM atlas.chunks c JOIN atlas.documents d ON d.tenant_id=c.tenant_id AND d.id=c.document_id JOIN atlas.document_versions v ON v.tenant_id=c.tenant_id AND v.id=c.version_id WHERE c.tenant_id=%s AND c.id=%s AND d.status='ready' AND d.lifecycle='active' AND v.status='ready'",
                (identity.tenant_id, chunk_id),
            )
        ).fetchone()
    if not row:
        raise HTTPException(404, "Source not found")
    return jsonable_encoder(row)


class Query(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    mode: str = Field(default="hybrid", pattern="^(hybrid|vector|agent)$")
    top_k: int = Field(default=5, ge=1, le=10)
    rerank: bool = False
    use_cache: bool = True
    conversation_id: UUID | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=100)
    regenerate: bool = False
    space_ids: list[UUID] | None = Field(default=None, max_length=50)
    document_ids: list[UUID] | None = Field(default=None, max_length=100)
    summary_document_id: UUID | None = None


def event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/search")
async def search(body: Query, identity: Identity = Depends(authenticate)):
    require(identity, "query")
    quota = await serving.limits(identity.tenant_id)
    await serving.rate_limit(identity.tenant_id, quota["requests_per_minute"])
    await serving.query_limit(identity.tenant_id)
    from atlas.conversations import authorization_revision, sources_available

    revision = await authorization_revision(identity)
    rows, _, _, _ = await retrieve(
        identity.tenant_id,
        body.question,
        body.top_k,
        body.mode,
        body.rerank,
        space_ids=body.space_ids or None,
        document_ids=body.document_ids or None,
    )
    if revision != await authorization_revision(identity) or not await sources_available(
        identity, rows
    ):
        raise HTTPException(403, "Access changed while searching; retry with current permissions")
    return {"sources": rows}


@router.post("/query")
async def query(body: Query, request: Request, identity: Identity = Depends(authenticate)):
    from atlas.conversations import (
        attempt_running,
        authorization_revision,
        check_scope,
        contextual_question,
        failure_code,
        failure_diagnostics,
        prepare_turn,
        safe_message,
        sources_available,
    )
    from atlas.generation import UsageMeter, fit_sources, meter_context

    require(identity, "query")
    if len(body.question.encode()) > 3000:
        raise HTTPException(422, "Shorten the question to at most 3000 UTF-8 bytes")
    if body.mode == "agent" and len(body.question.encode()) > 1800:
        raise HTTPException(422, "Shorten an agent question to at most 1800 UTF-8 bytes")
    if body.summary_document_id and body.mode == "agent":
        raise HTTPException(422, "Document summaries use document retrieval mode")
    quota = await serving.limits(identity.tenant_id)
    await serving.rate_limit(identity.tenant_id, quota["requests_per_minute"])
    await serving.query_limit(identity.tenant_id)
    query_id = uuid4()
    turn = None
    space_ids, document_ids = body.space_ids, body.document_ids
    if body.summary_document_id:
        document_ids = [body.summary_document_id]
    if body.conversation_id:
        if not body.idempotency_key:
            raise HTTPException(422, "A conversation request needs an idempotency key")
        turn = await prepare_turn(
            identity,
            body.conversation_id,
            body.question,
            query_id,
            body.idempotency_key,
            body.regenerate,
            space_ids,
            document_ids,
            mode=body.mode,
        )
        if turn.get("replay"):

            async def replay():
                previous = await safe_message(identity, turn["replay"])
                yield event(
                    "metadata",
                    {
                        "query_id": str(previous["request_id"]),
                        "conversation_id": str(body.conversation_id),
                        "assistant_message_id": str(previous["id"]),
                        "replayed": True,
                    },
                )
                yield event("sources", {"sources": previous["sources"]})
                previous = await safe_message(identity, turn["replay"])
                if previous["status"] == "unavailable":
                    yield event(
                        "error",
                        {
                            "code": "access_changed",
                            "reset": True,
                            "message": "Evidence access changed",
                        },
                    )
                yield event("delta", {"text": previous["content"]})
                yield event(
                    "done",
                    {
                        "query_id": str(previous["request_id"]),
                        "status": previous["status"],
                        "replayed": True,
                        "api_cost_usd": 0,
                    },
                )

            return StreamingResponse(
                replay(),
                media_type="text/event-stream",
                headers={"Cache-Control": "private, no-store"},
            )
        space_ids, document_ids = turn["space_ids"], turn["document_ids"]
    elif body.regenerate:
        raise HTTPException(422, "Regeneration requires a conversation")
    space_ids, document_ids = space_ids or None, document_ids or None
    prompt_question = contextual_question(
        body.question,
        turn["context"] if turn else [],
        max_bytes=1800 if body.mode == "agent" else 3500,
    )
    turn_dependencies = turn["dependencies"] if turn else []
    try:
        async with transaction(identity.tenant_id) as conn:
            await check_scope(conn, space_ids, document_ids)
            await conn.execute(
                "INSERT INTO atlas.queries(tenant_id,id,question,mode,user_id,conversation_id,principal_id,principal_kind) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,id) DO NOTHING",
                (
                    identity.tenant_id,
                    query_id,
                    body.question,
                    body.mode,
                    identity.user_id,
                    body.conversation_id,
                    identity.principal_id,
                    identity.principal_kind,
                ),
            )
        revision = await authorization_revision(identity)
        coll = await collection(identity.tenant_id)
        namespace = serving.cache_namespace(
            identity.tenant_id,
            str(coll["id"]),
            coll["revision"],
            body.mode,
            body.top_k,
            body.rerank,
            identity.scopes,
        )
        visibility = hashlib.sha256(
            json.dumps(
                [
                    str(identity.principal_id),
                    identity.principal_kind,
                    revision,
                    [str(i) for i in space_ids or []],
                    [str(i) for i in document_ids or []],
                    str(body.summary_document_id or ""),
                    prompt_question,
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        namespace += ":verification:" + VERIFIER_REVISION + ":visibility:" + visibility
        cached = (
            await serving.cache_lookup(namespace, prompt_question)
            if body.mode != "agent" and body.use_cache
            else None
        )
        if cached and not await sources_available(identity, cached["sources"] + turn_dependencies):
            cached = None
        if cached and not verify_answer(cached["answer"], cached["sources"]).supported:
            cached = None
        await serving.reserve(
            identity.tenant_id,
            query_id,
            0 if cached else (70000 if body.mode == "agent" else 27000),
        )
    except Exception as exc:
        # This narrowly scoped finalizer remains callable after a session or membership
        # is revoked, and safely closes any attempt created before preflight failed.
        async with transaction(identity.tenant_id) as conn:
            await conn.execute(
                "SELECT atlas.finalize_revoked_attempt(%s,0,0,0,0,%s)",
                (query_id, settings.generation_model),
            )
            if turn:
                await conn.execute(
                    "UPDATE atlas.messages SET metadata=metadata || %s WHERE tenant_id=%s AND id=%s",
                    (
                        Jsonb(failure_diagnostics("preflight", failure_code(exc))),
                        identity.tenant_id,
                        turn["assistant_message_id"],
                    ),
                )
        raise

    async def stream():
        start = time.perf_counter()
        sources: list[dict] = []
        answer, status, first_token = "", "failed", None
        vector, cache_hit, revoked = [], False, False
        steps = []
        stage = "retrieving"
        diagnostics: dict[str, Any] = {}
        verification = {}
        request_dependencies = list(turn_dependencies)
        meter = UsageMeter()
        meter_token = meter_context.set(meter)
        metadata = {"query_id": str(query_id), "stage": "retrieving"}
        if turn:
            metadata.update(
                conversation_id=str(body.conversation_id),
                user_message_id=str(turn["user_message_id"]),
                assistant_message_id=str(turn["assistant_message_id"]),
            )
        yield event("metadata", metadata)

        async def verify_access():
            nonlocal revoked
            try:
                current_revision = await authorization_revision(identity)
                if current_revision != revision or not await sources_available(
                    identity, sources + request_dependencies
                ):
                    raise HTTPException(403, "Knowledge access changed while answering")
            except Exception:
                revoked = True
                raise
            if turn and not await attempt_running(identity, turn["assistant_message_id"]):
                raise QueryStopped()

        try:
            with tracer.start_as_current_span("query") as span:
                span.set_attribute("tenant.id", str(identity.tenant_id))
                span.set_attribute(
                    "request.id", getattr(request.state, "request_id", str(query_id))
                )
                await verify_access()
                if cached:
                    sources, answer, cache_hit = cached["sources"], cached["answer"], True
                elif body.mode == "agent":
                    from atlas.agent import run_agent

                    stage = "agent"
                    yield event("metadata", {"stage": stage})

                    async def agent_access(evidence):
                        await verify_access()
                        if await request.is_disconnected():
                            raise QueryStopped()
                        if not await sources_available(identity, evidence):
                            raise HTTPException(403, "Agent evidence access changed")

                    result = await run_agent(
                        identity.tenant_id,
                        prompt_question,
                        space_ids=space_ids,
                        document_ids=document_ids,
                        access_check=agent_access,
                    )
                    # Tool diagnostics can quote evidence that does not fit the final
                    # answer budget. Retain every reference before trimming sources.
                    request_dependencies.extend(
                        {
                            key: source[key]
                            for key in ("id", "document_id", "version_id", "kind")
                            if key in source
                        }
                        for source in result["sources"]
                    )
                    sources = result["sources"][:10]
                    steps = result["history"]
                    await verify_access()
                    for step in steps:
                        yield event("step", step)
                    if result["stop_reason"]:
                        yield event(
                            "warning",
                            {
                                "message": "The agent reached a limit or encountered a tool error. Available evidence is shown below."
                            },
                        )
                elif body.summary_document_id:
                    sources = await summary_sources(identity, body.summary_document_id)
                    yield event(
                        "warning",
                        {
                            "message": "This summary covers the selected source passages. Open the document to inspect the complete text."
                        },
                    )
                else:
                    sources, vector, _, _ = await retrieve(
                        identity.tenant_id,
                        prompt_question,
                        body.top_k,
                        body.mode,
                        body.rerank,
                        space_ids=space_ids,
                        document_ids=document_ids,
                    )
                # Semantic reuse stays disabled for permission-sensitive conversations.
                if not cache_hit:
                    sources = fit_sources(prompt_question, sources)
                await verify_access()
                yield event("sources", {"sources": sources})
                if any(source.get("stale") for source in sources):
                    yield event(
                        "warning",
                        {
                            "message": "Some source documents are past their review date. Confirm their currency with the document owner."
                        },
                    )
                if any(source.get("possible_conflict") for source in sources):
                    yield event(
                        "warning",
                        {
                            "message": "Some sources contain matching statements with different quantities. Inspect the passages and versions before relying on the answer."
                        },
                    )
                if cache_hit:
                    status = "completed"
                    stage = "cached"
                    first_token = (time.perf_counter() - start) * 1000
                    yield event("metadata", {"stage": "cached", "cache": "exact"})
                    await verify_access()
                    yield event("delta", {"text": answer})
                elif (
                    not sources or max(s["similarity"] for s in sources) < settings.relevance_floor
                ):
                    answer = "I couldn’t find enough relevant information in your selected knowledge to answer that. Try adding a document or adjusting the scope."
                    status = "abstained"
                    yield event("delta", {"text": answer})
                else:
                    stage = "generating"
                    yield event("metadata", {"stage": stage, "model": settings.generation_model})
                    with tracer.start_as_current_span("generate"):
                        async for item in generate(prompt_question, sources):
                            if await request.is_disconnected():
                                status = "cancelled"
                                diagnostics = failure_diagnostics(stage, "client_disconnected")
                                break
                            await verify_access()
                            if item["type"] == "delta":
                                answer += item["text"]
                        else:
                            stage = "verifying"
                            yield event("metadata", {"stage": stage})
                            check = verify_answer(answer, sources)
                            verification = {
                                "verification_method": check.method,
                                "supported_claims": check.claims - check.rejected,
                                "unsupported_claims": check.rejected,
                            }
                            if check.supported:
                                status = "completed"
                            else:
                                answer = evidence_fallback(sources)
                                status = "abstained"
                                yield event(
                                    "warning",
                                    {
                                        "message": "Unsupported answer withheld. Inspect the exact source passages or refine the question.",
                                        **verification,
                                    },
                                )
                            await verify_access()
                            first_token = (time.perf_counter() - start) * 1000
                            yield event("delta", {"text": answer})
                await verify_access()
                if (
                    status == "completed"
                    and not cache_hit
                    and body.mode != "agent"
                    and body.use_cache
                ):
                    await serving.cache_store(namespace, prompt_question, vector, answer, sources)
                span.set_attribute("cache.hit", cache_hit)
                span.set_attribute("usage.api_cost_usd", 0.0)
                span.set_attribute("query.status", status)
                span.set_attribute("usage.input_tokens", meter.input_tokens)
                span.set_attribute("usage.output_tokens", meter.output_tokens)
                if status == "uncited":
                    yield event(
                        "warning",
                        {
                            "message": "The model did not provide valid citations. Verify this answer against the source passages."
                        },
                    )
        except QueryStopped:
            answer = ""
            status = "cancelled"
            diagnostics = failure_diagnostics(stage, "stopped")
        except asyncio.CancelledError:
            answer = ""
            status = "cancelled"
            diagnostics = failure_diagnostics(stage, "worker_interrupted")
            raise
        except Exception as exc:
            status = "cancelled" if revoked else "failed"
            diagnostics = dict[str, Any](
                failure_diagnostics(stage, "access_changed" if revoked else failure_code(exc))
            )
            if not revoked and sources and stage == "generating":
                try:
                    await verify_access()
                except Exception:
                    revoked = True
                else:
                    answer = evidence_fallback(sources, unavailable=True)
                    status = "abstained"
                    diagnostics["search_fallback"] = True
                    yield event(
                        "warning",
                        {
                            "message": answer,
                            "code": "generation_unavailable",
                            "search_fallback": True,
                        },
                    )
                    yield event("delta", {"text": answer})
            if revoked:
                answer, sources, steps = "", [], []
            elif status == "failed":
                # A partially generated, unverified answer must never be persisted.
                answer = ""
            if not diagnostics.get("search_fallback") or revoked:
                yield event(
                    "error",
                    {
                        "message": "Your access changed. The answer has been cleared; reload your workspace."
                        if revoked
                        else "The local model or search service is unavailable. Check System status and retry explicitly.",
                        "code": diagnostics["error_code"],
                        "reset": revoked,
                        **diagnostics,
                    },
                )
        finally:
            duration = (time.perf_counter() - start) * 1000
            latency.record(
                duration / 1000,
                {"mode": body.mode, "status": status, "cache_hit": str(cache_hit).lower()},
            )
            if cache_hit:
                cache_hits.add(1)
            tokens.add(meter.input_tokens + meter.output_tokens)

            async def save():
                nonlocal revoked, answer, sources, steps, diagnostics
                try:
                    # Revocation between the final token and persistence must also redact the stored answer.
                    if not await sources_available(identity, sources + request_dependencies):
                        revoked, answer, sources, steps = True, "", [], []
                        if not diagnostics:
                            diagnostics = failure_diagnostics("persisting", "access_changed")
                    await serving.settle(identity.tenant_id, query_id, meter.total)
                    async with transaction(identity.tenant_id) as conn:
                        updated = await (
                            await conn.execute(
                                "UPDATE atlas.queries SET answer=%s,status=%s,sources=%s,duration_ms=%s,first_token_ms=%s,cached=%s WHERE tenant_id=%s AND id=%s RETURNING id",
                                (
                                    answer,
                                    "cancelled" if revoked else status,
                                    Jsonb(sources),
                                    duration,
                                    first_token,
                                    cache_hit,
                                    identity.tenant_id,
                                    query_id,
                                ),
                            )
                        ).fetchone()
                        if not updated:
                            raise PermissionError("Attempt principal was revoked")
                        if turn:
                            message_status = (
                                "unavailable"
                                if revoked
                                else "completed"
                                if status in {"completed", "uncited", "abstained"}
                                else status
                            )
                            await conn.execute(
                                "UPDATE atlas.messages SET content=%s,status=%s,sources=%s,metadata=metadata || %s WHERE tenant_id=%s AND id=%s",
                                (
                                    answer,
                                    message_status,
                                    Jsonb(sources),
                                    Jsonb(
                                        {
                                            "dependency_sources": []
                                            if revoked
                                            else request_dependencies,
                                            "mode": body.mode,
                                            "query_status": status,
                                            "duration_ms": duration,
                                            "first_token_ms": first_token,
                                            "input_tokens": meter.input_tokens,
                                            "output_tokens": meter.output_tokens,
                                            "cache_hit": cache_hit,
                                            "access_revoked": revoked,
                                            "steps": steps,
                                            **diagnostics,
                                            **verification,
                                        }
                                    ),
                                    identity.tenant_id,
                                    turn["assistant_message_id"],
                                ),
                            )
                            await conn.execute(
                                "UPDATE atlas.conversations SET updated_at=now() WHERE tenant_id=%s AND id=%s",
                                (identity.tenant_id, body.conversation_id),
                            )
                        await conn.execute(
                            "INSERT INTO atlas.usage_ledger(tenant_id,id,request_id,operation,backend,model,input_tokens,output_tokens,duration_ms,status,user_id,principal_id,principal_kind) VALUES(%s,%s,%s,'query',%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (
                                identity.tenant_id,
                                uuid4(),
                                query_id,
                                "cache" if cache_hit else "ollama",
                                settings.generation_model,
                                meter.input_tokens if meter.total is not None else None,
                                meter.output_tokens if meter.total is not None else None,
                                duration,
                                "cancelled" if revoked else status,
                                identity.user_id,
                                identity.principal_id,
                                identity.principal_kind,
                            ),
                        )
                except Exception as exc:
                    revoked, answer, sources, steps = True, "", [], []
                    if not diagnostics:
                        diagnostics = failure_diagnostics("persisting", failure_code(exc))
                    async with transaction(identity.tenant_id) as conn:
                        await conn.execute(
                            "SELECT atlas.finalize_revoked_attempt(%s,%s,%s,%s,%s,%s)",
                            (
                                query_id,
                                meter.total,
                                meter.input_tokens if meter.total is not None else None,
                                meter.output_tokens if meter.total is not None else None,
                                duration,
                                settings.generation_model,
                            ),
                        )
                        if turn:
                            await conn.execute(
                                "UPDATE atlas.messages SET metadata=metadata || %s WHERE tenant_id=%s AND id=%s",
                                (
                                    Jsonb(diagnostics),
                                    identity.tenant_id,
                                    turn["assistant_message_id"],
                                ),
                            )

            try:
                await asyncio.shield(save())
            finally:
                meter_context.reset(meter_token)
        yield event(
            "done",
            {
                "query_id": str(query_id),
                "conversation_id": str(body.conversation_id) if body.conversation_id else None,
                "assistant_message_id": str(turn["assistant_message_id"]) if turn else None,
                "status": "cancelled" if revoked else status,
                "duration_ms": round(duration),
                "first_token_ms": first_token,
                "api_cost_usd": 0,
                "cache_hit": cache_hit,
                "input_tokens": meter.input_tokens,
                "output_tokens": meter.output_tokens,
                "reset": revoked,
                **verification,
                **diagnostics,
            },
        )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no"},
    )


class QueryStopped(Exception):
    pass


async def summary_sources(identity, document_id):
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                """WITH ordered AS (
          SELECT c.*,d.title,d.source_key,v.number version_number,v.source_segments,
          row_number() OVER(ORDER BY c.ordinal) rn,count(*) OVER() total
          FROM atlas.chunks c JOIN atlas.documents d ON d.tenant_id=c.tenant_id AND d.id=c.document_id
          JOIN atlas.document_versions v ON v.tenant_id=c.tenant_id AND v.id=c.version_id
          WHERE c.tenant_id=%s AND d.id=%s AND d.status='ready' AND d.lifecycle='active'
          AND c.version_id=d.current_version_id AND v.publication_status='published' AND v.effective_at<=now() AND c.pipeline_hash=%s)
          SELECT id,document_id,version_id,title,source_key,content,start_offset,end_offset,version_number,source_segments,1.0 similarity
          FROM ordered WHERE (rn-1) %% greatest(1,ceil(total/10.0)::int)=0 ORDER BY rn LIMIT 10""",
                (identity.tenant_id, document_id, pipeline_hash()),
            )
        ).fetchall()
    return jsonable_encoder(rows)


@router.get("/queries")
async def queries(identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT * FROM atlas.queries WHERE tenant_id=%s AND (user_id IS NOT NULL OR principal_kind='service') ORDER BY created_at DESC LIMIT 100",
                (identity.tenant_id,),
            )
        ).fetchall()
    from atlas.conversations import safe_message

    for row in rows:
        if row.get("conversation_id"):
            async with transaction(identity.tenant_id) as conn:
                message = await (
                    await conn.execute(
                        "SELECT * FROM atlas.messages WHERE tenant_id=%s AND request_id=%s AND role='assistant'",
                        (identity.tenant_id, row["id"]),
                    )
                ).fetchone()
            if message:
                safe = await safe_message(identity, message)
                row.update(answer=safe["content"], sources=safe["sources"], status=safe["status"])
            else:
                row.update(
                    answer="This conversation is unavailable.", sources=[], status="unavailable"
                )
        else:
            safe = await safe_message(
                identity,
                {
                    "role": "assistant",
                    "content": row["answer"] or "",
                    "sources": row["sources"],
                    "status": "completed"
                    if row["status"] in {"completed", "uncited", "abstained"}
                    else row["status"],
                    "metadata": {"query_status": row["status"]},
                },
            )
            row.update(answer=safe["content"], sources=safe["sources"])
            if safe["status"] == "unavailable":
                row["status"] = "unavailable"
    return jsonable_encoder(rows)


@router.get("/overview")
async def overview(identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        counts = await (
            await conn.execute(
                """SELECT
          (SELECT count(*) FROM atlas.documents WHERE tenant_id=%s AND status!='deleted' AND lifecycle='active') documents,
          (SELECT count(*) FROM atlas.chunks c JOIN atlas.documents d ON c.tenant_id=d.tenant_id AND c.document_id=d.id JOIN atlas.document_versions v ON v.tenant_id=c.tenant_id AND v.id=c.version_id WHERE c.tenant_id=%s AND d.status='ready' AND d.lifecycle='active' AND c.version_id=d.current_version_id AND v.publication_status='published' AND v.effective_at<=now() AND c.pipeline_hash=%s) chunks,
          (SELECT count(*) FROM atlas.queries WHERE tenant_id=%s) queries,
          (SELECT avg(duration_ms) FROM atlas.queries WHERE tenant_id=%s AND status='completed') avg_latency_ms,
          (SELECT coalesce(sum(actual_api_cost_usd),0) FROM atlas.usage_ledger WHERE tenant_id=%s) api_spend""",
                (
                    identity.tenant_id,
                    identity.tenant_id,
                    pipeline_hash(),
                    identity.tenant_id,
                    identity.tenant_id,
                    identity.tenant_id,
                ),
            )
        ).fetchone()
    return jsonable_encoder(counts)


@router.get("/keys")
async def keys(identity: Identity = Depends(authenticate)):
    require(identity, "admin")
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT id,label,prefix,scopes,created_at,expires_at,revoked_at FROM atlas.api_keys WHERE tenant_id=%s ORDER BY created_at DESC",
                (identity.tenant_id,),
            )
        ).fetchall()
    return jsonable_encoder(rows)


class NewKey(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(default_factory=lambda: ["read", "query"])


@router.post("/keys", status_code=201)
async def new_key(body: NewKey, identity: Identity = Depends(authenticate)):
    raise HTTPException(410, "Create a scoped service identity and key through Integrations")


@router.delete("/keys/{key_id}")
async def revoke_key(key_id: UUID, identity: Identity = Depends(authenticate)):
    require(identity, "admin")
    if key_id == identity.key_id:
        raise HTTPException(409, "Create or use another key before revoking your current session")
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "UPDATE atlas.api_keys SET revoked_at=now() WHERE tenant_id=%s AND id=%s RETURNING id",
                (identity.tenant_id, key_id),
            )
        ).fetchone()
    if not row:
        raise HTTPException(404, "Key not found")
    return {"ok": True}


@router.get("/system")
async def system(identity: Identity = Depends(authenticate)):
    require(identity, "read")
    from atlas.generation import client

    try:
        models = await client().list()
        available = [m.model for m in models.models]
        model_ready = settings.generation_model in available
    except Exception:
        available, model_ready = [], False
    return {
        "generation": {
            "ready": model_ready,
            "model": settings.generation_model,
            "available": available,
        },
        "embeddings": {
            "ready": await asyncio.to_thread(Path(settings.embedding_path).exists),
            "model": "BAAI/bge-small-en-v1.5",
        },
        "reranker": {"ready": await asyncio.to_thread(Path(settings.reranker_path).exists)},
        "paid_apis": False,
        "tenant_isolation": "PostgreSQL RLS + application scope",
    }


@router.get("/jobs")
async def jobs(identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT id,document_id,status,attempts,error_code,result,created_at,updated_at FROM atlas.ingestion_jobs WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 100",
                (identity.tenant_id,),
            )
        ).fetchall()
    return jsonable_encoder(rows)


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: UUID, identity: Identity = Depends(authenticate)):
    require(identity, "write")
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "UPDATE atlas.ingestion_jobs SET status='queued',attempts=0,error_code=NULL WHERE tenant_id=%s AND id=%s AND status='dead' RETURNING document_id",
                (identity.tenant_id, job_id),
            )
        ).fetchone()
        if not row:
            raise HTTPException(409, "Only a failed job in this workspace can be retried")
        await conn.execute(
            "INSERT INTO atlas.outbox(tenant_id,id,job_id) VALUES(%s,%s,%s)",
            (identity.tenant_id, uuid4(), job_id),
        )
    return {"status": "queued"}


@router.get("/budget")
async def budget(identity: Identity = Depends(authenticate)):
    require(identity, "read")
    quota = await serving.limits(identity.tenant_id)
    async with transaction(identity.tenant_id) as conn:
        usage = await (
            await conn.execute(
                "SELECT * FROM atlas.budget_periods WHERE tenant_id=%s AND period=date_trunc('month',now())::date",
                (identity.tenant_id,),
            )
        ).fetchone()
    return jsonable_encoder(
        {
            "limits": quota,
            "usage": usage,
            "semantic_cache_enabled": settings.semantic_cache_threshold is not None,
        }
    )


@router.get("/legacy-queries")
async def legacy_queries(
    identity: Identity = Depends(authenticate),
    offset: int = QueryParam(0, ge=0),
    limit: int = QueryParam(25, ge=1, le=100),
):
    from atlas.conversations import human, sources_available

    human(identity)
    require(identity, "admin")
    async with transaction(identity.tenant_id) as conn:
        count = await (
            await conn.execute(
                "SELECT count(*) n FROM atlas.queries WHERE tenant_id=%s AND user_id IS NULL AND principal_kind IS NULL",
                (identity.tenant_id,),
            )
        ).fetchone()
        rows = await (
            await conn.execute(
                "SELECT * FROM atlas.queries WHERE tenant_id=%s AND user_id IS NULL AND principal_kind IS NULL ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (identity.tenant_id, limit, offset),
            )
        ).fetchall()
    for row in rows:
        if not await sources_available(identity, row["sources"]):
            row.update(answer="Evidence is no longer available.", sources=[], status="unavailable")
    return {
        "items": rows,
        "total": count["n"] if count else 0,
        "offset": offset,
        "limit": limit,
        "notice": "These shared workspace queries predate individual accounts. Access is restricted to company administrators.",
    }
