import asyncio
import hashlib
import json
import math
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from atlas.auth import Identity, authenticate, require
from atlas.db import transaction
from atlas.embedding import model_revision
from atlas.ingestion import pipeline_hash
from atlas.retrieval import retrieve

router = APIRouter(prefix="/api/evaluation")
_eval_gate = asyncio.Lock()


def label_hash(row: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                k: str(row[k])
                for k in [
                    "question",
                    "document_id",
                    "source_hash",
                    "start_offset",
                    "end_offset",
                    "split",
                ]
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def retrieval_metrics(ranked: list[str], relevant: set[str], k=5) -> dict[str, float]:
    if not relevant:
        raise ValueError("Reviewed evidence does not map to any indexed chunk")
    hits = [1 if item in relevant else 0 for item in ranked[:k]]
    recall = len(set(ranked[:k]) & relevant) / len(relevant)
    mrr = next((1 / (i + 1) for i, item in enumerate(ranked) if item in relevant), 0.0)
    dcg = sum(hit / math.log2(i + 2) for i, hit in enumerate(hits))
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(relevant))))
    return {f"recall_at_{k}": recall, "mrr": mrr, f"ndcg_at_{k}": dcg / ideal}


@router.get("/labels")
async def labels(identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                """SELECT l.*,d.title source_title,
          substring(d.content FROM l.start_offset+1 FOR l.end_offset-l.start_offset) evidence
          FROM atlas.eval_labels l JOIN atlas.documents d ON d.tenant_id=l.tenant_id AND d.id=l.document_id
          WHERE l.tenant_id=%s ORDER BY l.created_at,l.id""",
                (identity.tenant_id,),
            )
        ).fetchall()
    return jsonable_encoder(rows)


class Review(BaseModel):
    question: str = Field(min_length=5, max_length=2000)
    reviewed: bool
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=1)


@router.put("/labels/{label_id}")
async def review(label_id: UUID, body: Review, identity: Identity = Depends(authenticate)):
    require(identity, "admin")
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "SELECT l.*,d.content_hash current_hash,length(d.content) source_length,d.status source_status FROM atlas.eval_labels l JOIN atlas.documents d ON d.tenant_id=l.tenant_id AND d.id=l.document_id WHERE l.tenant_id=%s AND l.id=%s FOR UPDATE OF l",
                (identity.tenant_id, label_id),
            )
        ).fetchone()
        if not row:
            raise HTTPException(404, "Label not found")
        if row["current_hash"] != row["source_hash"]:
            raise HTTPException(409, "The source has changed; refresh the label before reviewing")
        row["question"] = body.question
        if body.start_offset is not None:
            row["start_offset"] = body.start_offset
        if body.end_offset is not None:
            row["end_offset"] = body.end_offset
        if (
            row["source_status"] != "ready"
            or not 0 <= row["start_offset"] < row["end_offset"] <= row["source_length"]
        ):
            raise HTTPException(422, "Select a valid evidence span in an indexed document")
        await conn.execute(
            "UPDATE atlas.eval_labels SET start_offset=%s,end_offset=%s,question=%s,reviewed=%s,reviewed_by=%s,reviewed_at=CASE WHEN %s THEN now() ELSE NULL END,review_hash=%s WHERE tenant_id=%s AND id=%s",
            (
                row["start_offset"],
                row["end_offset"],
                body.question,
                body.reviewed,
                identity.key_id if body.reviewed else None,
                body.reviewed,
                label_hash(row) if body.reviewed else None,
                identity.tenant_id,
                label_id,
            ),
        )
    return {"ok": True}


class EvalConfig(BaseModel):
    mode: str = Field(default="hybrid", pattern="^(hybrid|vector)$")
    rerank: bool = False
    with_judge: bool = False
    top_k: int = Field(default=5, ge=1, le=10)
    chunk_size: int = Field(default=1000, ge=300, le=2000)
    split: str = Field(default="development", pattern="^(development|held_out|all)$")


async def run_evaluation(tenant: UUID, config: EvalConfig):
    async with transaction(tenant) as conn:
        rows = await (
            await conn.execute(
                "SELECT l.*,d.content_hash current_hash,length(d.content) source_length,d.status source_status FROM atlas.eval_labels l JOIN atlas.documents d ON d.tenant_id=l.tenant_id AND d.id=l.document_id WHERE l.tenant_id=%s ORDER BY l.id",
                (tenant,),
            )
        ).fetchall()
    if len(rows) < 40 or any(
        r["source_status"] != "ready"
        or not r["reviewed"]
        or r["review_hash"] != label_hash(r)
        or r["source_hash"] != r["current_hash"]
        for r in rows
    ):
        raise HTTPException(
            409,
            "Review at least 40 valid labels before running the corpus evaluation. Unreviewed or changed labels are never used.",
        )
    selected = [r for r in rows if config.split == "all" or r["split"] == config.split]
    if not selected:
        raise HTTPException(409, "No reviewed labels in the selected split")
    if _eval_gate.locked():
        raise HTTPException(409, "Another evaluation is running")
    async with _eval_gate:
        results = []
        for row in selected:
            sources, _, _, _ = await retrieve(
                tenant,
                row["question"],
                max(5, config.top_k),
                config.mode,
                config.rerank,
                size=config.chunk_size,
            )
            async with transaction(tenant) as conn:
                gold = await (
                    await conn.execute(
                        "SELECT id FROM atlas.chunks WHERE tenant_id=%s AND document_id=%s AND pipeline_hash=%s AND end_offset>%s AND start_offset<%s",
                        (
                            tenant,
                            row["document_id"],
                            pipeline_hash(config.chunk_size),
                            row["start_offset"],
                            row["end_offset"],
                        ),
                    )
                ).fetchall()
            relevant = {str(c["id"]) for c in gold}
            if not relevant:
                raise HTTPException(
                    409,
                    "A source span does not map to the requested chunk configuration. Reindex or review the mapping.",
                )
            metrics = retrieval_metrics([s["id"] for s in sources], relevant)
            metrics.update(
                retrieval_metrics(
                    [s["id"] for s in sources][: config.top_k], relevant, config.top_k
                )
            )
            assessment = None
            if config.with_judge:
                from atlas import serving
                from atlas.generation import UsageMeter, fit_sources, generate, meter_context
                from atlas.judge import judge_answer

                operation = uuid4()
                await serving.limits(tenant)
                await serving.reserve(tenant, operation, 45000)
                meter = UsageMeter()
                token = meter_context.set(meter)
                try:
                    selected_sources = fit_sources(row["question"], sources[: config.top_k])
                    answer = "".join(
                        [
                            part["text"]
                            async for part in generate(row["question"], selected_sources)
                            if part["type"] == "delta"
                        ]
                    )
                    assessment = {"answer": answer, **await judge_answer(answer, selected_sources)}
                finally:
                    await serving.settle(tenant, operation, meter.total)
                    meter_context.reset(token)
            results.append(
                {
                    "judgment": assessment,
                    "label_id": str(row["id"]),
                    "question": row["question"],
                    "expected": sorted(relevant),
                    "retrieved": [s["id"] for s in sources],
                    **metrics,
                }
            )
        metrics = {
            key: sum(r[key] for r in results) / len(results)
            for key in results[0]
            if key not in {"label_id", "question", "expected", "retrieved", "judgment"}
        }
        if config.with_judge:
            scored = [r["judgment"]["score"] for r in results if r["judgment"]["score"] is not None]
            if scored:
                metrics["faithfulness_proxy"] = sum(scored) / len(scored)
        run_id = uuid4()
        manifest = {
            "embedding_revision": model_revision(),
            "labels_hash": hashlib.sha256(
                "".join(r["review_hash"] for r in selected).encode()
            ).hexdigest(),
            "quality_basis": "human-reviewed evidence",
            "judge_calibrated": False,
            "judge_prompt_hash": hashlib.sha256(
                await asyncio.to_thread(Path("prompts/faithfulness.md").read_bytes)
            ).hexdigest()
            if config.with_judge
            else None,
        }
        async with transaction(tenant) as conn:
            run = await (
                await conn.execute(
                    "INSERT INTO atlas.eval_runs(tenant_id,id,mode,config,label_count,metrics,results,manifest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                    (
                        tenant,
                        run_id,
                        config.mode,
                        Jsonb(config.model_dump()),
                        len(selected),
                        Jsonb(metrics),
                        Jsonb(results),
                        Jsonb(manifest),
                    ),
                )
            ).fetchone()
        return jsonable_encoder(run)


@router.post("/run")
async def run(config: EvalConfig, identity: Identity = Depends(authenticate)):
    require(identity, "admin")
    return await run_evaluation(identity.tenant_id, config)


@router.get("/runs")
async def runs(identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT * FROM atlas.eval_runs WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 30",
                (identity.tenant_id,),
            )
        ).fetchall()
    return jsonable_encoder(rows)


def regression_gate(metrics: dict, threshold_path: Path) -> None:
    threshold = json.loads(threshold_path.read_text())
    if threshold.get("recall_at_5") is None:
        raise ValueError("No accepted measured baseline yet")
    if metrics["recall_at_5"] < threshold["recall_at_5"]:
        raise ValueError("Recall@5 regressed below the accepted baseline")
