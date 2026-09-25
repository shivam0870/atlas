import asyncio
from datetime import UTC, datetime
from functools import lru_cache
from uuid import UUID

from atlas.config import settings
from atlas.db import transaction
from atlas.embedding import embed, model_revision, vector_literal
from atlas.ingestion import collection
from atlas.telemetry import tracer


def rrf(
    vector_ids: list[str], lexical_ids: list[str], c=60.0, vector_weight=1.0, lexical_weight=1.0
):
    scores: dict[str, float] = {}
    for ids, weight in [(vector_ids, vector_weight), (lexical_ids, lexical_weight)]:
        for rank, item in enumerate(ids, 1):
            scores[item] = scores.get(item, 0) + weight / (c + rank)
    return scores


@lru_cache(maxsize=1)
def reranker():
    from sentence_transformers import CrossEncoder

    return CrossEncoder(settings.reranker_path, local_files_only=True, device="cpu")


async def retrieve(
    tenant_id: UUID,
    question: str,
    top_k=5,
    mode="hybrid",
    rerank=False,
    size=None,
    overlap=None,
    space_ids: list[UUID] | None = None,
    document_ids: list[UUID] | None = None,
):
    coll = await collection(tenant_id, size, overlap)
    query_vector = (await embed([question], query=True))[0]
    with tracer.start_as_current_span("retrieve"):
        async with transaction(tenant_id) as conn:
            await conn.execute("SET LOCAL hnsw.iterative_scan='strict_order'")
            vectors = await (
                await conn.execute(
                    """
                SELECT c.id,c.document_id,c.content,c.start_offset,c.end_offset,d.title,d.source_key,c.version_id,v.number version_number,v.source_segments,v.created_at version_created_at,v.effective_at,v.publication_status,d.owner_user_id,d.updated_at document_updated_at,d.review_due_at,
                  1-(e.embedding <=> %s::vector) similarity
                FROM atlas.chunks c JOIN atlas.embeddings e ON e.tenant_id=c.tenant_id AND e.chunk_id=c.id
                JOIN atlas.documents d ON d.tenant_id=c.tenant_id AND d.id=c.document_id
                LEFT JOIN atlas.document_versions v ON v.tenant_id=c.tenant_id AND v.id=c.version_id
                WHERE c.tenant_id=%s AND c.collection_id=%s AND e.model_revision=%s AND d.status='ready' AND d.lifecycle='active' AND c.version_id IS NOT DISTINCT FROM d.current_version_id AND EXISTS (SELECT 1 FROM atlas.document_versions pv WHERE pv.tenant_id=d.tenant_id AND pv.id=c.version_id AND pv.publication_status='published' AND pv.effective_at<=now())
                AND (%s::uuid[] IS NULL OR d.space_id=ANY(%s::uuid[])) AND (%s::uuid[] IS NULL OR d.id=ANY(%s::uuid[]))
                ORDER BY e.embedding <=> %s::vector LIMIT 20
            """,
                    (
                        vector_literal(query_vector),
                        tenant_id,
                        coll["id"],
                        model_revision(),
                        space_ids,
                        space_ids,
                        document_ids,
                        document_ids,
                        vector_literal(query_vector),
                    ),
                )
            ).fetchall()
            lexical = []
            if mode != "vector":
                # Compute tenant/collection-local statistics in the same statement snapshot.
                # This favors correctness and readable SQL over mutable aggregate counters.
                lexical = await (
                    await conn.execute(
                        """
                    WITH corpus AS MATERIALIZED (
                      SELECT c.* FROM atlas.chunks c JOIN atlas.documents d ON d.tenant_id=c.tenant_id AND d.id=c.document_id
                      WHERE c.tenant_id=%s AND c.collection_id=%s AND d.status='ready' AND d.lifecycle='active' AND c.version_id IS NOT DISTINCT FROM d.current_version_id AND EXISTS (SELECT 1 FROM atlas.document_versions pv WHERE pv.tenant_id=d.tenant_id AND pv.id=c.version_id AND pv.publication_status='published' AND pv.effective_at<=now())
                      AND (%s::uuid[] IS NULL OR d.space_id=ANY(%s::uuid[])) AND (%s::uuid[] IS NULL OR d.id=ANY(%s::uuid[]))
                    ), stats AS (SELECT count(*)::float n,avg(token_count)::float avgdl FROM corpus),
                    terms AS (SELECT DISTINCT unnest(lexemes) term FROM ts_debug('english',%s)),
                    matches AS (
                      SELECT ct.chunk_id,ct.term,ct.frequency,c.token_count
                      FROM atlas.chunk_terms ct JOIN corpus c ON c.id=ct.chunk_id AND c.tenant_id=ct.tenant_id
                      JOIN terms q ON q.term=ct.term WHERE ct.tenant_id=%s
                    ), dfs AS (SELECT term,count(*)::float df FROM matches GROUP BY term)
                    SELECT m.chunk_id id,sum(ln(1+(s.n-d.df+0.5)/(d.df+0.5)) *
                      (m.frequency*2.2)/(m.frequency+1.2*(0.25+0.75*m.token_count/nullif(s.avgdl,0)))) score
                    FROM matches m JOIN dfs d ON d.term=m.term CROSS JOIN stats s
                    GROUP BY m.chunk_id ORDER BY score DESC,m.chunk_id LIMIT 20
                """,
                        (
                            tenant_id,
                            coll["id"],
                            space_ids,
                            space_ids,
                            document_ids,
                            document_ids,
                            question,
                            tenant_id,
                        ),
                    )
                ).fetchall()
            rows = {str(r["id"]): dict(r) for r in vectors}
            extra = [r["id"] for r in lexical if str(r["id"]) not in rows]
            if extra:
                additional = await (
                    await conn.execute(
                        """SELECT c.id,c.document_id,c.content,c.start_offset,c.end_offset,d.title,d.source_key,c.version_id,v.number version_number,v.source_segments,v.created_at version_created_at,v.effective_at,v.publication_status,d.owner_user_id,d.updated_at document_updated_at,d.review_due_at,
                  1-(e.embedding <=> %s::vector) similarity FROM atlas.chunks c
                  JOIN atlas.documents d ON d.tenant_id=c.tenant_id AND d.id=c.document_id
                  JOIN atlas.embeddings e ON e.tenant_id=c.tenant_id AND e.chunk_id=c.id
                  LEFT JOIN atlas.document_versions v ON v.tenant_id=c.tenant_id AND v.id=c.version_id
                  WHERE c.tenant_id=%s AND c.id=ANY(%s) AND e.model_revision=%s AND d.status='ready' AND d.lifecycle='active' AND c.version_id IS NOT DISTINCT FROM d.current_version_id AND EXISTS (SELECT 1 FROM atlas.document_versions pv WHERE pv.tenant_id=d.tenant_id AND pv.id=c.version_id AND pv.publication_status='published' AND pv.effective_at<=now())""",
                        (vector_literal(query_vector), tenant_id, extra, model_revision()),
                    )
                ).fetchall()
                rows.update({str(r["id"]): dict(r) for r in additional})
        scores = rrf(
            [str(r["id"]) for r in vectors],
            [str(r["id"]) for r in lexical],
            settings.rrf_constant,
            settings.vector_weight,
            settings.lexical_weight,
        )
        vector_ranks = {str(row["id"]): rank for rank, row in enumerate(vectors, 1)}
        lexical_ranks = {str(row["id"]): rank for rank, row in enumerate(lexical, 1)}
        ordered = sorted(rows.values(), key=lambda r: (-scores[str(r["id"])], str(r["id"])))
        if rerank and ordered:
            with tracer.start_as_current_span("rerank"):
                values = await asyncio.to_thread(
                    lambda: reranker().predict([(question, r["content"]) for r in ordered]).tolist()
                )
            for row, value in zip(ordered, values, strict=True):
                row["rerank_score"] = float(value)
            ordered.sort(key=lambda r: -r["rerank_score"])
        for row in ordered:
            row["id"], row["document_id"] = str(row["id"]), str(row["document_id"])
            if row.get("version_id"):
                row["version_id"] = str(row["version_id"])
            row["source_segments"] = [
                segment
                for segment in row.get("source_segments") or []
                if segment["start"] < row["end_offset"] and segment["end"] > row["start_offset"]
            ]
            row["stale"] = bool(
                row.get("review_due_at") and row["review_due_at"] < datetime.now(UTC)
            )
            if row.get("review_due_at"):
                row["review_due_at"] = row["review_due_at"].isoformat()
            for timestamp in ["version_created_at", "document_updated_at", "effective_at"]:
                if row.get(timestamp):
                    row[timestamp] = row[timestamp].isoformat()
            if row.get("owner_user_id"):
                row["owner_user_id"] = str(row["owner_user_id"])
            row["score"] = scores[row["id"]]
            row["vector_rank"] = vector_ranks.get(row["id"])
            row["lexical_rank"] = lexical_ranks.get(row["id"])
        from atlas.evidence_safety import flag_possible_conflicts

        selected = ordered[:top_k]
        flag_possible_conflicts(selected)
        return selected, query_vector, str(coll["id"]), coll["revision"]
