import asyncio
import hashlib
import json
import math
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from atlas.administration import evaluator
from atlas.auth import Identity, authenticate
from atlas.config import settings
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
    evaluator(identity)
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                """SELECT l.*,d.title source_title,
          substring(d.content FROM l.start_offset+1 FOR l.end_offset-l.start_offset) evidence
          FROM atlas.eval_labels l JOIN atlas.documents d ON d.tenant_id=l.tenant_id AND d.id=l.document_id
          WHERE l.tenant_id=%s AND d.lifecycle='active' ORDER BY l.created_at,l.id""",
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
    evaluator(identity)
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "SELECT l.*,d.content_hash current_hash,length(d.content) source_length,d.status source_status,d.current_version_id source_version_id FROM atlas.eval_labels l JOIN atlas.documents d ON d.tenant_id=l.tenant_id AND d.id=l.document_id WHERE l.tenant_id=%s AND l.id=%s AND d.lifecycle='active' FOR UPDATE OF l",
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
                (identity.user_id or identity.key_id) if body.reviewed else None,
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


async def run_evaluation(tenant: UUID, config: EvalConfig, checkpoint=None):
    async with transaction(tenant) as conn:
        rows = await (
            await conn.execute(
                "SELECT l.*,d.content_hash current_hash,length(d.content) source_length,d.status source_status,d.current_version_id source_version_id FROM atlas.eval_labels l JOIN atlas.documents d ON d.tenant_id=l.tenant_id AND d.id=l.document_id WHERE l.tenant_id=%s AND d.lifecycle='active' ORDER BY l.id",
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
        for position, row in enumerate(selected):
            if checkpoint is not None:
                await checkpoint(position, len(selected))
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
                        "SELECT c.id FROM atlas.chunks c JOIN atlas.documents d ON d.tenant_id=c.tenant_id AND d.id=c.document_id WHERE c.tenant_id=%s AND c.document_id=%s AND c.pipeline_hash=%s AND c.end_offset>%s AND c.start_offset<%s AND c.version_id=d.current_version_id",
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
                started = perf_counter()
                operation_status = "failed"
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
                    operation_status = "completed"
                except asyncio.CancelledError:
                    operation_status = "cancelled"
                    raise
                finally:
                    from atlas.db import access_context

                    actor = access_context.get()
                    try:
                        await serving.settle(tenant, operation, meter.total)
                        async with transaction(tenant) as conn:
                            await conn.execute(
                                "INSERT INTO atlas.usage_ledger(tenant_id,id,request_id,operation,backend,model,input_tokens,output_tokens,actual_api_cost_usd,duration_ms,status,user_id,principal_id,principal_kind) VALUES(%s,%s,%s,'evaluation','ollama',%s,%s,%s,0,%s,%s,%s,%s,%s)",
                                (
                                    tenant,
                                    uuid4(),
                                    operation,
                                    settings.generation_model,
                                    meter.input_tokens if meter.total is not None else None,
                                    meter.output_tokens if meter.total is not None else None,
                                    (perf_counter() - started) * 1000,
                                    operation_status,
                                    actor.user_id if actor else None,
                                    actor.principal_id if actor else None,
                                    actor.principal_kind if actor else None,
                                ),
                            )
                    finally:
                        meter_context.reset(token)
            results.append(
                {
                    "judgment": assessment,
                    "dependencies": [
                        {
                            "document_id": str(row["document_id"]),
                            "version_id": str(row["source_version_id"])
                            if row["source_version_id"]
                            else None,
                            "kind": "document",
                        },
                        *[
                            {
                                key: source.get(key)
                                for key in ["id", "document_id", "version_id", "kind"]
                            }
                            for source in sources
                        ],
                    ],
                    "label_id": str(row["id"]),
                    "question": row["question"],
                    "expected": sorted(relevant),
                    "retrieved": [s["id"] for s in sources],
                    **metrics,
                }
            )
        if checkpoint is not None:
            await checkpoint(len(selected), len(selected))
        metrics = {
            key: sum(r[key] for r in results) / len(results)
            for key in results[0]
            if key
            not in {"label_id", "question", "expected", "retrieved", "judgment", "dependencies"}
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


@router.post("/run", status_code=202)
async def run(config: EvalConfig, identity: Identity = Depends(authenticate)):
    return await enqueue_evaluation(config, identity)


@router.get("/runs")
async def runs(identity: Identity = Depends(authenticate)):
    evaluator(identity)
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT * FROM atlas.eval_runs WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 30",
                (identity.tenant_id,),
            )
        ).fetchall()
        for row in rows:
            await filter_run_access(conn, row, identity)
    return jsonable_encoder(rows)


async def filter_run_access(conn, run, identity: Identity):
    from atlas.conversations import sources_available

    ids = [UUID(row["label_id"]) for row in run["results"]]
    authorized = await (
        await conn.execute(
            "SELECT l.id FROM atlas.eval_labels l JOIN atlas.documents d ON d.tenant_id=l.tenant_id AND d.id=l.document_id WHERE l.tenant_id=%s AND l.id=ANY(%s) AND d.lifecycle='active'",
            (run["tenant_id"], ids),
        )
    ).fetchall()
    available = {row["id"] for row in authorized} == set(ids)
    dependencies = []
    for result in run["results"]:
        if "dependencies" in result:
            dependencies.extend(result["dependencies"])
        else:
            # Older runs did not persist dependency references. Resolve every retrieved/gold
            # chunk through current RLS, and hide the entire result if any reference is gone.
            try:
                chunks = {
                    UUID(value)
                    for value in result.get("retrieved", []) + result.get("expected", [])
                }
            except (TypeError, ValueError):
                available = False
                break
            rows = await (
                await conn.execute(
                    "SELECT c.id,c.document_id,c.version_id FROM atlas.chunks c JOIN atlas.documents d ON d.tenant_id=c.tenant_id AND d.id=c.document_id WHERE c.tenant_id=%s AND c.id=ANY(%s) AND d.lifecycle='active'",
                    (run["tenant_id"], list(chunks)),
                )
            ).fetchall()
            if {row["id"] for row in rows} != chunks:
                available = False
                break
            dependencies.extend(
                {
                    "id": str(row["id"]),
                    "document_id": str(row["document_id"]),
                    "version_id": str(row["version_id"]) if row["version_id"] else None,
                    "kind": "document",
                }
                for row in rows
            )
    unique = {
        json.dumps(reference, sort_keys=True, default=str): reference for reference in dependencies
    }
    if not available or not await sources_available(identity, list(unique.values())):
        run.update(results=[], metrics={}, manifest={}, unavailable=True)
    return run


def regression_gate(metrics: dict, threshold_path: Path) -> None:
    threshold = json.loads(threshold_path.read_text())
    if threshold.get("recall_at_5") is None:
        raise ValueError("No accepted measured baseline yet")
    if metrics["recall_at_5"] < threshold["recall_at_5"]:
        raise ValueError("Recall@5 regressed below the accepted baseline")


async def validated_label_count(tenant: UUID, config: EvalConfig):
    async with transaction(tenant) as conn:
        rows = await (
            await conn.execute(
                "SELECT l.*,d.content_hash current_hash,d.status source_status,d.current_version_id source_version_id FROM atlas.eval_labels l JOIN atlas.documents d ON d.tenant_id=l.tenant_id AND d.id=l.document_id WHERE l.tenant_id=%s AND d.lifecycle='active' ORDER BY l.id",
                (tenant,),
            )
        ).fetchall()
    if len(rows) < 40 or any(
        row["source_status"] != "ready"
        or not row["reviewed"]
        or row["review_hash"] != label_hash(row)
        or row["source_hash"] != row["current_hash"]
        for row in rows
    ):
        raise HTTPException(
            409,
            "Review at least 40 valid labels before running an evaluation. Every accessible label must have current human-reviewed evidence.",
        )
    count = sum(config.split == "all" or row["split"] == config.split for row in rows)
    if not count:
        raise HTTPException(409, "No reviewed labels in the selected split")
    return count


@router.post("/jobs", status_code=202)
async def enqueue_evaluation(config: EvalConfig, identity: Identity = Depends(authenticate)):
    evaluator(identity)
    if not identity.user_id or not identity.session_id:
        raise HTTPException(403, "Sign in with a personal account to run evaluations")
    total = await validated_label_count(identity.tenant_id, config)
    async with transaction(identity.tenant_id) as conn:
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"eval:{identity.tenant_id}",)
        )
        pending = await (
            await conn.execute(
                "SELECT * FROM atlas.evaluation_jobs WHERE tenant_id=%s AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1",
                (identity.tenant_id,),
            )
        ).fetchone()
        if pending:
            if pending["user_id"] == identity.user_id and pending["config"] == config.model_dump():
                return {"job": pending}
            raise HTTPException(409, "Another evaluation is queued or running in this workspace")
        row = await (
            await conn.execute(
                "INSERT INTO atlas.evaluation_jobs(tenant_id,id,user_id,session_id,auth_revision,config,total) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (
                    identity.tenant_id,
                    uuid4(),
                    identity.user_id,
                    identity.session_id,
                    identity.auth_revision,
                    Jsonb(config.model_dump()),
                    total,
                ),
            )
        ).fetchone()
    return {"job": row}


@router.get("/jobs")
async def evaluation_jobs(identity: Identity = Depends(authenticate)):
    evaluator(identity)
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT * FROM atlas.evaluation_jobs WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 50",
                (identity.tenant_id,),
            )
        ).fetchall()
    return {"items": rows}


@router.get("/jobs/{job_id}")
async def evaluation_job(job_id: UUID, identity: Identity = Depends(authenticate)):
    evaluator(identity)
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "SELECT * FROM atlas.evaluation_jobs WHERE tenant_id=%s AND id=%s",
                (identity.tenant_id, job_id),
            )
        ).fetchone()
    if not row:
        raise HTTPException(404, "Evaluation job not found")
    return {"job": row}


@router.post("/jobs/{job_id}/cancel")
async def cancel_evaluation(job_id: UUID, identity: Identity = Depends(authenticate)):
    evaluator(identity)
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "UPDATE atlas.evaluation_jobs SET cancel_requested=true,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,updated_at=now() WHERE tenant_id=%s AND id=%s AND status IN ('queued','running') RETURNING *",
                (identity.tenant_id, job_id),
            )
        ).fetchone()
    if not row:
        raise HTTPException(409, "This evaluation is already finished or unavailable")
    return {"job": row}


def compare_results(left: dict, right: dict):
    reasons = []
    for key in ["labels_hash", "quality_basis", "embedding_revision"]:
        if left["manifest"].get(key) != right["manifest"].get(key):
            reasons.append(f"Different {key.replace('_', ' ')}")
    if left["config"].get("split") != right["config"].get("split"):
        reasons.append("Different evaluation splits")
    if left["label_count"] != right["label_count"]:
        reasons.append("Different label counts")
    deltas = {
        key: right["metrics"][key] - value
        for key, value in left["metrics"].items()
        if key in right["metrics"]
        and isinstance(value, (int, float))
        and isinstance(right["metrics"][key], (int, float))
    }
    left_questions = {row["label_id"]: row for row in left["results"]}
    regressions = []
    for row in right["results"]:
        before = left_questions.get(row["label_id"])
        if before and row.get("recall_at_5", 0) < before.get("recall_at_5", 0):
            regressions.append(
                {
                    "label_id": row["label_id"],
                    "question": row["question"],
                    "before": before["recall_at_5"],
                    "after": row["recall_at_5"],
                }
            )
    return {
        "left": left,
        "right": right,
        "deltas": deltas,
        "comparable": not reasons,
        "reasons": reasons,
        "regressions": regressions,
    }


@router.get("/compare")
async def compare_runs(left: UUID, right: UUID, identity: Identity = Depends(authenticate)):
    evaluator(identity)
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT * FROM atlas.eval_runs WHERE tenant_id=%s AND id=ANY(%s)",
                (identity.tenant_id, [left, right]),
            )
        ).fetchall()
        for row in rows:
            await filter_run_access(conn, row, identity)
            if row.get("unavailable"):
                raise HTTPException(403, "Evaluation evidence is no longer accessible")
    by_id = {row["id"]: row for row in rows}
    if left not in by_id or right not in by_id:
        raise HTTPException(404, "Evaluation run not found")
    return compare_results(by_id[left], by_id[right])


async def process_evaluation_job(tenant: UUID, job_id: UUID, owner: str):
    """Worker entry point. Lease metadata uses worker role; evaluated content uses the app role."""
    from atlas import db
    from atlas.auth import ROLE_SCOPES

    async with transaction(tenant) as conn:
        row = await (
            await conn.execute(
                "SELECT * FROM atlas.evaluation_jobs WHERE tenant_id=%s AND id=%s FOR UPDATE",
                (tenant, job_id),
            )
        ).fetchone()
        if not row or row["status"] not in {"queued", "running"}:
            return
        if row["status"] == "running" and row["lease_until"] is not None:
            from datetime import UTC, datetime

            if row["lease_until"] > datetime.now(UTC):
                return
        if row["cancel_requested"]:
            await conn.execute(
                "UPDATE atlas.evaluation_jobs SET status='cancelled',updated_at=now(),lease_until=NULL WHERE tenant_id=%s AND id=%s",
                (tenant, job_id),
            )
            return
        if row["attempts"] >= 3 or (row["attempts"] and row["config"].get("with_judge")):
            await conn.execute(
                "UPDATE atlas.evaluation_jobs SET status='failed',error_code='interrupted_review_required',lease_until=NULL,updated_at=now() WHERE tenant_id=%s AND id=%s",
                (tenant, job_id),
            )
            return
        await conn.execute(
            "UPDATE atlas.evaluation_jobs SET status='running',owner=%s,lease_until=now()+interval '45 seconds',attempts=attempts+1,updated_at=now() WHERE tenant_id=%s AND id=%s",
            (owner, tenant, job_id),
        )
    cancelled = asyncio.Event()

    async def checkpoint(progress, total):
        async with transaction(tenant) as conn:
            state = await (
                await conn.execute(
                    "SELECT j.cancel_requested,j.auth_revision,t.auth_revision AS current_revision,atlas.can_evaluate() AS allowed FROM atlas.evaluation_jobs j JOIN atlas.tenants t ON t.id=j.tenant_id WHERE j.tenant_id=%s AND j.id=%s AND j.owner=%s",
                    (tenant, job_id, owner),
                )
            ).fetchone()
            if (
                not state
                or not state["allowed"]
                or state["current_revision"] != state["auth_revision"]
            ):
                raise HTTPException(
                    403, "Evaluation access changed; restart after reviewing permissions"
                )
            if state["cancel_requested"]:
                cancelled.set()
                raise asyncio.CancelledError()
            await conn.execute(
                "UPDATE atlas.evaluation_jobs SET progress=%s,total=%s,updated_at=now() WHERE tenant_id=%s AND id=%s AND owner=%s",
                (progress, total, tenant, job_id, owner),
            )

    async def execute():
        async with db.identity_transaction() as conn:
            account = await (
                await conn.execute(
                    "SELECT u.name,m.role,m.can_evaluate,t.auth_revision FROM atlas.users u JOIN atlas.memberships m ON m.user_id=u.id JOIN atlas.tenants t ON t.id=m.tenant_id JOIN atlas.user_sessions s ON s.user_id=u.id WHERE u.id=%s AND m.tenant_id=%s AND m.status='active' AND u.disabled_at IS NULL AND u.email_verified_at IS NOT NULL AND s.id=%s AND s.revoked_at IS NULL AND s.expires_at>now() AND s.last_seen_at>now()-(%s * interval '1 second')",
                    (row["user_id"], tenant, row["session_id"], settings.session_idle_seconds),
                )
            ).fetchone()
        if not account or not (account["role"] in {"owner", "admin"} or account["can_evaluate"]):
            raise HTTPException(403, "Evaluation access was revoked")
        scopes = list(ROLE_SCOPES[account["role"]]) + (
            ["evaluate"] if account["can_evaluate"] else []
        )
        identity = Identity(
            tenant,
            row["session_id"],
            account["name"],
            scopes,
            user_id=row["user_id"],
            role=account["role"],
            principal_kind="user",
            principal_id=row["user_id"],
            auth_revision=account["auth_revision"],
            session_id=row["session_id"],
        )
        context_token = db.bind_identity(identity)
        try:
            async with db.application_transactions():
                return await run_evaluation(
                    tenant, EvalConfig.model_validate(row["config"]), checkpoint
                )
        finally:
            db.access_context.reset(context_token)

    work = asyncio.create_task(execute())
    try:
        while not work.done():
            done, _ = await asyncio.wait({work}, timeout=2)
            if done:
                break
            async with transaction(tenant) as conn:
                state = await (
                    await conn.execute(
                        "UPDATE atlas.evaluation_jobs SET lease_until=now()+interval '45 seconds' WHERE tenant_id=%s AND id=%s AND owner=%s AND status='running' RETURNING cancel_requested",
                        (tenant, job_id, owner),
                    )
                ).fetchone()
            if not state or state["cancel_requested"]:
                cancelled.set()
                work.cancel()
                break
        result = await work
        async with transaction(tenant) as conn:
            await conn.execute(
                "UPDATE atlas.evaluation_jobs SET status='completed',result_id=%s,lease_until=NULL,updated_at=now() WHERE tenant_id=%s AND id=%s AND owner=%s",
                (UUID(result["id"]), tenant, job_id, owner),
            )
    except asyncio.CancelledError:
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        async with transaction(tenant) as conn:
            await conn.execute(
                "UPDATE atlas.evaluation_jobs SET status=%s,error_code=%s,lease_until=NULL,updated_at=now() WHERE tenant_id=%s AND id=%s AND owner=%s",
                (
                    "cancelled" if cancelled.is_set() else "failed",
                    None if cancelled.is_set() else "worker_interrupted",
                    tenant,
                    job_id,
                    owner,
                ),
            )
        if not cancelled.is_set():
            raise
    except Exception as exc:
        async with transaction(tenant) as conn:
            await conn.execute(
                "UPDATE atlas.evaluation_jobs SET status='failed',error_code=%s,lease_until=NULL,updated_at=now() WHERE tenant_id=%s AND id=%s AND owner=%s",
                (
                    {
                        402: "budget_exhausted",
                        403: "access_revoked",
                        409: "review_or_index_required",
                        429: "rate_limited",
                        503: "dependency_unavailable",
                    }.get(exc.status_code, "evaluation_rejected")
                    if isinstance(exc, HTTPException)
                    else type(exc).__name__,
                    tenant,
                    job_id,
                    owner,
                ),
            )
