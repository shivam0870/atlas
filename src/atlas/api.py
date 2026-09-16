import asyncio
import json
import secrets
import time
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from atlas import serving
from atlas.auth import Identity, authenticate, digest, require
from atlas.config import settings
from atlas.db import transaction
from atlas.generation import citations_valid, generate
from atlas.ingestion import collection, create_document, pipeline_hash
from atlas.retrieval import retrieve
from atlas.telemetry import cache_hits, latency, tokens, tracer

router = APIRouter(prefix="/api")


def local_only(request: Request):
    if (
        not settings.local_console
        or not request.client
        or request.client.host not in {"127.0.0.1", "::1"}
        or request.url.hostname not in {"localhost", "127.0.0.1"}
    ):
        raise HTTPException(404, "Not found")


@router.get("/local/workspaces")
async def local_workspaces(request: Request):
    local_only(request)
    path = settings.data_dir / "workspaces.json"
    return (
        [{k: v for k, v in row.items() if k != "key"} for row in json.loads(path.read_text())]
        if path.exists()
        else []
    )


@router.post("/local/connect/{tenant_id}")
async def local_connect(tenant_id: UUID, request: Request, response: Response):
    local_only(request)
    path = settings.data_dir / "workspaces.json"
    workspaces = json.loads(path.read_text()) if path.exists() else []
    row = next((w for w in workspaces if w["id"] == str(tenant_id)), None)
    if not row:
        raise HTTPException(404, "Workspace not found")
    response.set_cookie(
        "atlas_session",
        row["key"],
        httponly=True,
        samesite="strict",
        max_age=86400,
        secure=request.url.scheme == "https",
    )
    return {"name": row["name"]}


@router.post("/session")
async def login(response: Response, request: Request, identity: Identity = Depends(authenticate)):
    key = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not key:
        raise HTTPException(400, "Provide a key in the Authorization header")
    response.set_cookie(
        "atlas_session",
        key,
        httponly=True,
        samesite="strict",
        max_age=86400,
        secure=request.url.scheme == "https",
    )
    return {"name": identity.name}


@router.delete("/session")
async def logout(response: Response):
    response.delete_cookie("atlas_session")
    return {"ok": True}


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
            LEFT JOIN atlas.chunks c ON c.tenant_id=d.tenant_id AND c.document_id=d.id AND c.pipeline_hash=%s
            WHERE d.tenant_id=%s AND d.status!='deleted' GROUP BY d.tenant_id,d.id ORDER BY d.created_at DESC""",
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
                "SELECT * FROM atlas.documents WHERE tenant_id=%s AND id=%s AND status!='deleted'",
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
                "UPDATE atlas.documents SET status='deleted',updated_at=now() WHERE tenant_id=%s AND id=%s AND status!='deleted' RETURNING id",
                (identity.tenant_id, document_id),
            )
        ).fetchone()
        if not row:
            raise HTTPException(404, "Document not found")
        await conn.execute(
            "UPDATE atlas.collections SET revision=revision+1 WHERE tenant_id=%s",
            (identity.tenant_id,),
        )
    return {"ok": True}


@router.get("/chunks/{chunk_id}")
async def chunk(chunk_id: UUID, identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "SELECT c.*,d.title FROM atlas.chunks c JOIN atlas.documents d ON d.tenant_id=c.tenant_id AND d.id=c.document_id WHERE c.tenant_id=%s AND c.id=%s AND d.status='ready'",
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


def event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/search")
async def search(body: Query, identity: Identity = Depends(authenticate)):
    require(identity, "query")
    quota = await serving.limits(identity.tenant_id)
    await serving.rate_limit(identity.tenant_id, quota["requests_per_minute"])
    rows, _, _, _ = await retrieve(
        identity.tenant_id, body.question, body.top_k, body.mode, body.rerank
    )
    return {"sources": rows}


@router.post("/query")
async def query(body: Query, request: Request, identity: Identity = Depends(authenticate)):
    require(identity, "query")
    quota = await serving.limits(identity.tenant_id)
    await serving.rate_limit(identity.tenant_id, quota["requests_per_minute"])
    query_id = uuid4()
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
    cached = (
        await serving.cache_lookup(namespace, body.question)
        if body.mode != "agent" and body.use_cache
        else None
    )
    await serving.reserve(
        identity.tenant_id, query_id, 0 if cached else (70000 if body.mode == "agent" else 27000)
    )
    async with transaction(identity.tenant_id) as conn:
        await conn.execute(
            "INSERT INTO atlas.queries(tenant_id,id,question,mode) VALUES(%s,%s,%s,%s)",
            (identity.tenant_id, query_id, body.question, body.mode),
        )

    async def stream():
        start = time.perf_counter()
        answer, sources, status, first_token = "", [], "failed", None
        vector, cache_hit = [], False
        cache_match = cached["match"] if cached else None
        from atlas.generation import UsageMeter, fit_sources, meter_context

        meter = UsageMeter()
        meter_token = meter_context.set(meter)
        yield event("metadata", {"query_id": str(query_id), "stage": "retrieving"})
        try:
            with tracer.start_as_current_span("query") as span:
                span.set_attribute("tenant.id", str(identity.tenant_id))
                span.set_attribute("request.id", request.state.request_id)
                if cached:
                    sources, answer, cache_hit = cached["sources"], cached["answer"], True
                elif body.mode == "agent":
                    from atlas.agent import run_agent

                    yield event("metadata", {"stage": "agent"})
                    result = await run_agent(identity.tenant_id, body.question)
                    sources = result["sources"][:10]
                    for step in result["history"]:
                        yield event("step", step)
                    if result["stop_reason"]:
                        yield event(
                            "warning",
                            {
                                "message": "The agent reached a limit or encountered a tool error. Available evidence is shown below."
                            },
                        )
                else:
                    sources, vector, _, _ = await retrieve(
                        identity.tenant_id, body.question, body.top_k, body.mode, body.rerank
                    )
                if (
                    not cache_hit
                    and body.mode != "agent"
                    and body.use_cache
                    and settings.semantic_cache_threshold is not None
                ):
                    approximate = await serving.cache_lookup(namespace, body.question, vector)
                    if approximate:
                        sources, answer, cache_hit, cache_match = (
                            approximate["sources"],
                            approximate["answer"],
                            True,
                            approximate["match"],
                        )
                if not cache_hit:
                    sources = fit_sources(body.question, sources)
                yield event("sources", {"sources": sources})
                if cache_hit:
                    status = "completed"
                    first_token = (time.perf_counter() - start) * 1000
                    yield event("metadata", {"stage": "cached", "cache": cache_match})
                    yield event("delta", {"text": answer})
                elif (
                    not sources or max(s["similarity"] for s in sources) < settings.relevance_floor
                ):
                    answer = "I couldn’t find enough relevant information in this workspace to answer that. Try adding a document about this topic or asking a more specific question."
                    status = "abstained"
                    yield event("delta", {"text": answer})
                else:
                    yield event(
                        "metadata", {"stage": "generating", "model": settings.generation_model}
                    )
                    with tracer.start_as_current_span("generate"):
                        async for item in generate(body.question, sources):
                            if await request.is_disconnected():
                                status = "cancelled"
                                break
                            if item["type"] == "delta":
                                if first_token is None:
                                    first_token = (time.perf_counter() - start) * 1000
                                answer += item["text"]
                                yield event("delta", {"text": item["text"]})
                        else:
                            status = (
                                "completed" if citations_valid(answer, len(sources)) else "uncited"
                            )
                if (
                    status == "completed"
                    and not cache_hit
                    and body.mode != "agent"
                    and body.use_cache
                ):
                    await serving.cache_store(namespace, body.question, vector, answer, sources)
                span.set_attribute("cache.hit", cache_hit)
                span.set_attribute("usage.api_cost_usd", 0.0)
                span.set_attribute("query.status", status)
                span.set_attribute("usage.input_tokens", meter.input_tokens)
                span.set_attribute("usage.output_tokens", meter.output_tokens)
                span.set_attribute("usage.reconciled", meter.total is not None)
                if status == "uncited":
                    yield event(
                        "warning",
                        {
                            "message": "The model did not provide valid citations. Verify this answer against the source passages."
                        },
                    )
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception as exc:
            status = "failed"
            yield event(
                "error",
                {
                    "message": "The local model or search service is unavailable. Check System status and try again.",
                    "code": type(exc).__name__,
                },
            )
        finally:
            duration = (time.perf_counter() - start) * 1000
            attributes = {"mode": body.mode, "status": status, "cache_hit": str(cache_hit).lower()}
            latency.record(duration / 1000, attributes)
            if cache_hit:
                cache_hits.add(1)
            tokens.add(meter.input_tokens + meter.output_tokens)

            async def save():
                await serving.settle(identity.tenant_id, query_id, meter.total)
                async with transaction(identity.tenant_id) as conn:
                    await conn.execute(
                        "UPDATE atlas.queries SET answer=%s,status=%s,sources=%s,duration_ms=%s,first_token_ms=%s WHERE tenant_id=%s AND id=%s",
                        (
                            answer,
                            status,
                            Jsonb(sources),
                            duration,
                            first_token,
                            identity.tenant_id,
                            query_id,
                        ),
                    )
                    await conn.execute(
                        "INSERT INTO atlas.usage_ledger(tenant_id,id,request_id,operation,backend,model,input_tokens,output_tokens,duration_ms,status) VALUES(%s,%s,%s,'query','ollama',%s,%s,%s,%s,%s)",
                        (
                            identity.tenant_id,
                            uuid4(),
                            query_id,
                            settings.generation_model,
                            meter.input_tokens if meter.total is not None else None,
                            meter.output_tokens if meter.total is not None else None,
                            duration,
                            status,
                        ),
                    )

            await asyncio.shield(save())
            meter_context.reset(meter_token)
        yield event(
            "done",
            {
                "query_id": str(query_id),
                "status": status,
                "duration_ms": round(duration),
                "first_token_ms": first_token,
                "api_cost_usd": 0,
                "cache_hit": cache_hit,
            },
        )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/queries")
async def queries(identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT * FROM atlas.queries WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 100",
                (identity.tenant_id,),
            )
        ).fetchall()
    return jsonable_encoder(rows)


@router.get("/overview")
async def overview(identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        counts = await (
            await conn.execute(
                """SELECT
          (SELECT count(*) FROM atlas.documents WHERE tenant_id=%s AND status!='deleted') documents,
          (SELECT count(*) FROM atlas.chunks c JOIN atlas.documents d ON c.tenant_id=d.tenant_id AND c.document_id=d.id WHERE c.tenant_id=%s AND d.status='ready' AND c.pipeline_hash=%s) chunks,
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
    require(identity, "admin")
    if not set(body.scopes) <= {"read", "write", "query", "admin"}:
        raise HTTPException(422, "Unknown permission")
    key = "atl_" + secrets.token_urlsafe(32)
    key_id = uuid4()
    async with transaction(identity.tenant_id) as conn:
        await conn.execute(
            "INSERT INTO atlas.api_keys(id,tenant_id,digest,prefix,label,scopes) VALUES(%s,%s,%s,%s,%s,%s)",
            (key_id, identity.tenant_id, digest(key), key[:10], body.label, body.scopes),
        )
    return {"id": str(key_id), "key": key}


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
