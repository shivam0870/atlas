"""Deterministic fixture measurements, kept separate from human-reviewed corpus quality."""

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg

from atlas.config import settings
from atlas.db import pool, transaction
from atlas.evaluation import retrieval_metrics
from atlas.ingestion import create_document, index_document, pipeline_hash
from atlas.retrieval import retrieve


async def main():
    tenant = uuid4()
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "INSERT INTO atlas.tenants(id,slug,name) VALUES(%s,%s,%s)",
            (tenant, str(tenant), "Deterministic experiment fixture"),
        )
    await pool.open(wait=True)
    results = []
    try:
        fixtures = []
        for i in range(12):
            service = f"cedar-{i:02}"
            code = f"FROST-{i * 137 + 1031}"
            before = (
                f"This is a synthetic test document for the {service} deployment. Its support team records changes in the release log. "
            ) * 8
            fact = f"The {service} release approval code is {code}."
            content = (
                before
                + "\n\n"
                + fact
                + "\n\n"
                + (
                    "Operators check readiness and restore the previous image when rollback is needed. "
                    * 10
                )
            )
            doc = await create_document(tenant, service, content, service)
            fixtures.append(
                {
                    "document_id": doc["id"],
                    "question": f"What is the release approval code for {service}?",
                    "start": len(before) + 2,
                    "end": len(before) + 2 + len(fact),
                }
            )
        configs = (
            [
                {"chunk_size": s, "mode": "hybrid", "rerank": False, "top_k": 5}
                for s in [600, 1000, 1400]
            ]
            + [
                {"chunk_size": 1000, "mode": "vector", "rerank": False, "top_k": 5},
                {"chunk_size": 1000, "mode": "hybrid", "rerank": True, "top_k": 5},
            ]
            + [{"chunk_size": 1000, "mode": "hybrid", "rerank": False, "top_k": k} for k in [3, 8]]
        )
        for config in configs:
            for fixture in fixtures:
                await index_document(tenant, fixture["document_id"], size=config["chunk_size"])
            measurements = []
            for fixture in fixtures:
                start = time.perf_counter()
                sources, *_ = await retrieve(
                    tenant,
                    fixture["question"],
                    top_k=config["top_k"],
                    mode=config["mode"],
                    rerank=config["rerank"],
                    size=config["chunk_size"],
                )
                async with transaction(tenant) as conn:
                    gold = await (
                        await conn.execute(
                            "SELECT id FROM atlas.chunks WHERE tenant_id=%s AND document_id=%s AND pipeline_hash=%s AND end_offset>%s AND start_offset<%s",
                            (
                                tenant,
                                fixture["document_id"],
                                pipeline_hash(config["chunk_size"]),
                                fixture["start"],
                                fixture["end"],
                            ),
                        )
                    ).fetchall()
                metrics = retrieval_metrics(
                    [s["id"] for s in sources], {str(g["id"]) for g in gold}, config["top_k"]
                )
                measurements.append(
                    {
                        "question": fixture["question"],
                        "milliseconds": (time.perf_counter() - start) * 1000,
                        **metrics,
                    }
                )
            result = {
                "config": config,
                "queries": measurements,
                "metrics": {
                    k: sum(m[k] for m in measurements) / len(measurements)
                    for k in measurements[0]
                    if k != "question"
                },
            }
            results.append(result)
            print(json.dumps({"config": config, "metrics": result["metrics"]}), flush=True)
        artifact = {
            "executed_at": datetime.now(UTC).isoformat(),
            "basis": "12 programmatically authored synthetic fixtures; not human-reviewed Kubernetes corpus quality",
            "api_cost_usd": 0,
            "results": results,
        }
        Path("artifacts/engineering-experiments.json").write_text(
            json.dumps(artifact, indent=2) + "\n"
        )
    finally:
        await pool.close()
        with psycopg.connect(settings.database_admin_url) as conn:
            for table in [
                "chunk_terms",
                "embedding_cache",
                "embeddings",
                "chunks",
                "collections",
                "documents",
            ]:
                conn.execute(f"DELETE FROM atlas.{table} WHERE tenant_id=%s", (tenant,))
            conn.execute("DELETE FROM atlas.tenants WHERE id=%s", (tenant,))


asyncio.run(main())
