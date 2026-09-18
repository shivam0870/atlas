import asyncio
import hashlib
import json
import time
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from psycopg.types.json import Jsonb

from atlas.chunking import split_text
from atlas.config import settings
from atlas.db import transaction
from atlas.embedding import embed, encoder, model_revision, vector_literal
from atlas.telemetry import tracer


def pipeline_hash(size: int | None = None, overlap: int | None = None) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "version": 1,
                "size": size or settings.chunk_size,
                "overlap": settings.chunk_overlap if overlap is None else overlap,
                "model": model_revision(),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


async def collection(tenant_id: UUID, size=None, overlap=None) -> dict:
    fingerprint = pipeline_hash(size, overlap)
    async with transaction(tenant_id) as conn:
        row = await (
            await conn.execute(
                "INSERT INTO atlas.collections(tenant_id,id,pipeline_hash) VALUES(%s,%s,%s) ON CONFLICT(tenant_id,name,pipeline_hash) DO UPDATE SET name=excluded.name RETURNING *",
                (tenant_id, uuid4(), fingerprint),
            )
        ).fetchone()
    assert row
    return row


async def create_document(
    tenant_id: UUID,
    title: str,
    content: str,
    source_key: str,
    metadata=None,
    queued=False,
    traceparent=None,
    space_id=None,
):
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    async with transaction(tenant_id) as conn:
        row = await (
            await conn.execute(
                "INSERT INTO atlas.documents(tenant_id,id,title,content,source_key,content_hash,metadata,space_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,source_key,content_hash) DO UPDATE SET title=excluded.title,status=CASE WHEN documents.status='deleted' THEN 'pending' ELSE documents.status END,lifecycle=CASE WHEN documents.status='deleted' THEN 'active' ELSE documents.lifecycle END RETURNING *",
                (
                    tenant_id,
                    uuid4(),
                    title,
                    content,
                    source_key,
                    content_hash,
                    Jsonb(metadata or {}),
                    space_id,
                ),
            )
        ).fetchone()
        if row and not row.get("current_version_id") and not row.get("pending_version_id"):
            version_id = uuid4()
            await conn.execute(
                "INSERT INTO atlas.document_versions(tenant_id,id,document_id,number,title,content,content_hash,media_type,filename,source_segments,status) VALUES(%s,%s,%s,1,%s,%s,%s,%s,%s,%s,'pending')",
                (
                    tenant_id,
                    version_id,
                    row["id"],
                    title,
                    content,
                    content_hash,
                    "text/plain",
                    source_key,
                    Jsonb([{"start": 0, "end": len(content), "section": "Document"}]),
                ),
            )
            await conn.execute(
                "UPDATE atlas.documents SET pending_version_id=%s WHERE tenant_id=%s AND id=%s",
                (version_id, tenant_id, row["id"]),
            )
            row["pending_version_id"] = version_id
        if queued and row:
            from atlas.serving import enqueue_conn

            row.update(await enqueue_conn(conn, tenant_id, row["id"], traceparent))
    assert row
    return row


async def index_document(tenant_id: UUID, document_id: UUID, size=None, overlap=None):
    started = time.perf_counter()
    coll = await collection(tenant_id, size, overlap)
    fingerprint = coll["pipeline_hash"]
    async with transaction(tenant_id) as conn:
        doc = await (
            await conn.execute(
                "SELECT * FROM atlas.documents WHERE tenant_id=%s AND id=%s AND status!='deleted' FOR UPDATE",
                (tenant_id, document_id),
            )
        ).fetchone()
        if not doc:
            raise ValueError("Document not found")
        version_id = doc.get("pending_version_id") or doc.get("current_version_id")
        version = None
        if version_id:
            version = await (
                await conn.execute(
                    "SELECT * FROM atlas.document_versions WHERE tenant_id=%s AND id=%s FOR UPDATE",
                    (tenant_id, version_id),
                )
            ).fetchone()
            if version:
                doc["content"] = version["content"]
        existing = await (
            await conn.execute(
                "SELECT count(*) n FROM atlas.chunks WHERE tenant_id=%s AND document_id=%s AND pipeline_hash=%s AND version_id IS NOT DISTINCT FROM %s",
                (tenant_id, document_id, fingerprint, version_id),
            )
        ).fetchone()
        if (
            existing
            and existing["n"]
            and doc["status"] == "ready"
            and not doc.get("pending_version_id")
        ):
            return {"chunks": existing["n"], "reused": True, "duration_ms": 0}
        existing_chunk_ids = {}
        if existing and existing["n"]:
            existing_rows = await (
                await conn.execute(
                    "SELECT id,ordinal FROM atlas.chunks WHERE tenant_id=%s AND document_id=%s AND pipeline_hash=%s AND version_id IS NOT DISTINCT FROM %s",
                    (tenant_id, document_id, fingerprint, version_id),
                )
            ).fetchall()
            existing_chunk_ids = {row["ordinal"]: row["id"] for row in existing_rows}
        await conn.execute(
            "UPDATE atlas.documents SET status=CASE WHEN current_version_id IS NULL THEN 'indexing' ELSE status END,updated_at=now() WHERE tenant_id=%s AND id=%s",
            (tenant_id, document_id),
        )
        if version_id:
            await conn.execute(
                "UPDATE atlas.document_versions SET status='indexing' WHERE tenant_id=%s AND id=%s",
                (tenant_id, version_id),
            )
    try:
        with tracer.start_as_current_span("ingest"):
            with tracer.start_as_current_span("chunk"):
                tokenizer = (await asyncio.to_thread(encoder)).tokenizer
                chunks = await asyncio.to_thread(
                    split_text,
                    doc["content"],
                    size or settings.chunk_size,
                    settings.chunk_overlap if overlap is None else overlap,
                    lambda s: len(tokenizer.encode(s)),
                )
            hashes = [hashlib.sha256(c.text.encode()).hexdigest() for c in chunks]
            revision = model_revision()
            async with transaction(tenant_id) as conn:
                cached = await (
                    await conn.execute(
                        "SELECT content_hash,embedding::text vector FROM atlas.embedding_cache WHERE tenant_id=%s AND model_revision=%s AND content_hash=ANY(%s)",
                        (tenant_id, revision, hashes),
                    )
                ).fetchall()
            vectors = {r["content_hash"]: json.loads(r["vector"]) for r in cached}
            missing = {h: c.text for h, c in zip(hashes, chunks, strict=True) if h not in vectors}
            if missing:
                with tracer.start_as_current_span("embed"):
                    generated = await embed(list(missing.values()))
                vectors.update(zip(missing, generated, strict=True))
            async with transaction(tenant_id) as conn:
                # Serialize the publish step, but never hold this lock during model inference.
                current = await (
                    await conn.execute(
                        "SELECT id,status,pending_version_id,current_version_id,lifecycle FROM atlas.documents WHERE tenant_id=%s AND id=%s FOR UPDATE",
                        (tenant_id, document_id),
                    )
                ).fetchone()
                if (
                    not current
                    or current["status"] == "deleted"
                    or current["lifecycle"] == "trashed"
                ):
                    raise ValueError("Document was removed during indexing")
                if version_id != (
                    current.get("pending_version_id") or current.get("current_version_id")
                ):
                    raise ValueError("Document revision changed during indexing")
                for ordinal, (chunk, h) in enumerate(zip(chunks, hashes, strict=True)):
                    chunk_id = existing_chunk_ids.get(ordinal) or uuid5(
                        NAMESPACE_URL,
                        f"{tenant_id}:{document_id}:{version_id}:{fingerprint}:{ordinal}",
                    )
                    await conn.execute(
                        "INSERT INTO atlas.embedding_cache(tenant_id,content_hash,model_revision,embedding) VALUES(%s,%s,%s,%s::vector) ON CONFLICT DO NOTHING",
                        (tenant_id, h, revision, vector_literal(vectors[h])),
                    )
                    await conn.execute(
                        "INSERT INTO atlas.chunks(tenant_id,id,document_id,pipeline_hash,ordinal,start_offset,end_offset,content,content_hash,collection_id,version_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (
                            tenant_id,
                            chunk_id,
                            document_id,
                            fingerprint,
                            ordinal,
                            chunk.start,
                            chunk.end,
                            chunk.text,
                            h,
                            coll["id"],
                            version_id,
                        ),
                    )
                    await conn.execute(
                        "INSERT INTO atlas.embeddings(tenant_id,chunk_id,model_revision,embedding) VALUES(%s,%s,%s,%s::vector) ON CONFLICT DO NOTHING",
                        (tenant_id, chunk_id, revision, vector_literal(vectors[h])),
                    )
                    # ts_debug keeps repeated lexemes; tsvector positional arrays cap occurrences.
                    await conn.execute(
                        "INSERT INTO atlas.chunk_terms(tenant_id,chunk_id,term,frequency) SELECT %s,%s,lexeme,count(*) FROM ts_debug('english',%s),unnest(lexemes) lexeme GROUP BY lexeme ON CONFLICT DO NOTHING",
                        (tenant_id, chunk_id, chunk.text),
                    )
                    await conn.execute(
                        "UPDATE atlas.chunks SET token_count=(SELECT coalesce(sum(frequency),0) FROM atlas.chunk_terms WHERE tenant_id=%s AND chunk_id=%s) WHERE tenant_id=%s AND id=%s",
                        (tenant_id, chunk_id, tenant_id, chunk_id),
                    )
                if version_id:
                    await conn.execute(
                        "UPDATE atlas.document_versions SET status='ready' WHERE tenant_id=%s AND id=%s",
                        (tenant_id, version_id),
                    )
                    await conn.execute(
                        "UPDATE atlas.documents SET current_version_id=%s,pending_version_id=NULL,content=%s,content_hash=%s,media_type=%s WHERE tenant_id=%s AND id=%s",
                        (
                            version_id,
                            doc["content"],
                            version["content_hash"] if version else doc["content_hash"],
                            version["media_type"] if version else doc["media_type"],
                            tenant_id,
                            document_id,
                        ),
                    )
                await conn.execute(
                    "UPDATE atlas.documents SET status='ready',updated_at=now() WHERE tenant_id=%s AND id=%s",
                    (tenant_id, document_id),
                )
                await conn.execute(
                    "UPDATE atlas.collections SET revision=revision+1 WHERE tenant_id=%s AND id=%s",
                    (tenant_id, coll["id"]),
                )
        return {
            "chunks": len(chunks),
            "reused": False,
            "embedding_cache_hits": len(cached),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except Exception:
        async with transaction(tenant_id) as conn:
            await conn.execute(
                "UPDATE atlas.documents SET status=CASE WHEN current_version_id IS NULL THEN 'failed' ELSE status END,updated_at=now() WHERE tenant_id=%s AND id=%s AND status!='deleted'",
                (tenant_id, document_id),
            )
            if version_id:
                await conn.execute(
                    "UPDATE atlas.document_versions SET status='failed' WHERE tenant_id=%s AND id=%s",
                    (tenant_id, version_id),
                )
        raise
