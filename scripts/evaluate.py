"""Human-reviewed corpus experiments. Exits explicitly if the review gate is closed."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException

from atlas.db import pool, transaction
from atlas.evaluation import EvalConfig, regression_gate, run_evaluation
from atlas.ingestion import index_document


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiments", action="store_true")
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--judge", action="store_true")
    parser.add_argument(
        "--split", default="development", choices=["development", "held_out", "all"]
    )
    args = parser.parse_args()
    workspace = next(
        w for w in json.loads(Path(".local/workspaces.json").read_text()) if w["slug"] == "acme"
    )
    tenant = UUID(workspace["id"])
    await pool.open(wait=True)
    try:
        baseline = await run_evaluation(tenant, EvalConfig(split=args.split, with_judge=args.judge))
        configs = (
            [EvalConfig(chunk_size=size, split=args.split) for size in [600, 1000, 1400]]
            + [
                EvalConfig(mode="vector", split=args.split),
                EvalConfig(rerank=True, split=args.split),
            ]
            + [EvalConfig(top_k=k, split=args.split) for k in [3, 5, 8]]
            if args.experiments
            else []
        )
        results = [baseline]
        async with transaction(tenant) as conn:
            docs = await (
                await conn.execute(
                    "SELECT id FROM atlas.documents WHERE tenant_id=%s AND status='ready'",
                    (tenant,),
                )
            ).fetchall()
        for config in configs:
            for doc in docs:
                await index_document(tenant, doc["id"], size=config.chunk_size)
            results.append(await run_evaluation(tenant, config))
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        directory = Path("artifacts/evaluation") / stamp
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print("chunk | mode | rerank | k | recall@k | MRR | nDCG@k")
        for result in results:
            c, m = result["config"], result["metrics"]
            print(c, m)
        if args.gate:
            regression_gate(baseline["metrics"], Path("datasets/baseline.json"))
    except HTTPException as exc:
        raise SystemExit("Evaluation blocked: " + str(exc.detail)) from None
    finally:
        await pool.close()


asyncio.run(main())
