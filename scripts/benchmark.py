"""Run cached queries, uncached queries, and indexing separately in disposable tenants."""

import json
import os
import platform
import secrets
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from psycopg.rows import dict_row

from atlas.auth import digest
from atlas.config import settings

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8100"
TABLES = [
    "outbox",
    "ingestion_jobs",
    "eval_runs",
    "eval_labels",
    "reservations",
    "budget_periods",
    "tenant_limits",
    "entities",
    "chunk_terms",
    "embedding_cache",
    "embeddings",
    "chunks",
    "queries",
    "collections",
    "usage_ledger",
    "documents",
    "api_keys",
]


def provision(copy_corpus=False):
    tenant, key = uuid4(), "atl_" + secrets.token_urlsafe(32)
    with psycopg.connect(settings.database_admin_url, row_factory=dict_row) as conn:
        conn.execute(
            "INSERT INTO atlas.tenants(id,slug,name) VALUES(%s,%s,%s)",
            (tenant, str(tenant), "Disposable benchmark workspace"),
        )
        conn.execute(
            "INSERT INTO atlas.api_keys(tenant_id,id,digest,prefix,scopes) VALUES(%s,%s,%s,%s,%s)",
            (tenant, uuid4(), digest(key), key[:10], ["read", "write", "query", "admin"]),
        )
        conn.execute(
            "INSERT INTO atlas.tenant_limits(tenant_id,requests_per_minute,monthly_tokens) VALUES(%s,100000,100000000)",
            (tenant,),
        )
        if copy_corpus:
            source = conn.execute("SELECT id FROM atlas.tenants WHERE slug='acme'").fetchone()["id"]
            for table, columns in {
                "documents": "id,title,source_key,content_hash,content,status,metadata",
                "collections": "id,name,pipeline_hash,revision",
                "chunks": "id,document_id,pipeline_hash,ordinal,start_offset,end_offset,content,content_hash,collection_id,token_count",
                "embeddings": "chunk_id,model_revision,embedding",
                "chunk_terms": "chunk_id,term,frequency",
            }.items():
                conn.execute(
                    f"INSERT INTO atlas.{table}(tenant_id,{columns}) SELECT %s,{columns} FROM atlas.{table} WHERE tenant_id=%s",
                    (tenant, source),
                )
    return tenant, key


def cleanup(tenant):
    with psycopg.connect(settings.database_admin_url) as conn:
        for table in TABLES:
            conn.execute(f"DELETE FROM atlas.{table} WHERE tenant_id=%s", (tenant,))
        conn.execute("DELETE FROM atlas.tenants WHERE id=%s", (tenant,))


def run_k6(script, mode, key, path):
    env = {
        **os.environ,
        "ATLAS_BASE": BASE,
        "ATLAS_BENCH_KEY": key,
        "BENCH_MODE": mode,
        "BENCH_RUN": path.parent.name,
        "BENCH_OUTPUT": str(path),
    }
    subprocess.run(["k6", "run", "--quiet", script], env=env, check=True)
    return json.loads(path.read_text())


def main():
    os.chdir(ROOT)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "artifacts/benchmarks" / stamp
    out.mkdir(parents=True)
    results = {}
    tenant, key = provision(copy_corpus=True)
    try:
        with httpx.Client(
            base_url=BASE, headers={"Authorization": "Bearer " + key}, timeout=150
        ) as client:
            for _ in range(2):
                warm = client.post(
                    "/api/query",
                    json={"question": "What is the checkout release approval code?", "top_k": 3},
                )
                assert '"status": "completed"' in warm.text, "Warmup failed"
            results["cached"] = run_k6("benchmarks/query.js", "cached", key, out / "cached.json")
            results["uncached"] = run_k6(
                "benchmarks/query.js", "uncached", key, out / "uncached.json"
            )
        with psycopg.connect(settings.database_admin_url, row_factory=dict_row) as conn:
            ledger = conn.execute(
                "SELECT count(*) requests,sum(input_tokens) input_tokens,sum(output_tokens) output_tokens,sum(actual_api_cost_usd) api_cost_usd FROM atlas.usage_ledger WHERE tenant_id=%s",
                (tenant,),
            ).fetchone()
    finally:
        cleanup(tenant)
    ingest_tenant, ingest_key = provision()
    try:
        start = time.perf_counter()
        results["ingestion_http"] = run_k6(
            "benchmarks/ingestion.js", "ingestion", ingest_key, out / "ingestion-http.json"
        )
        with psycopg.connect(settings.database_admin_url, row_factory=dict_row) as conn:
            deadline = time.monotonic() + 180
            while True:
                rows = conn.execute(
                    "SELECT status,count(*) n FROM atlas.ingestion_jobs WHERE tenant_id=%s GROUP BY status",
                    (ingest_tenant,),
                ).fetchall()
                conn.commit()
                counts = {r["status"]: r["n"] for r in rows}
                if counts.get("completed") == 40:
                    break
                if counts.get("dead") or time.monotonic() > deadline:
                    raise RuntimeError("Indexing did not complete: " + str(counts))
                time.sleep(0.2)
            elapsed = time.perf_counter() - start
            count = conn.execute(
                "SELECT count(*) n FROM atlas.chunks WHERE tenant_id=%s", (ingest_tenant,)
            ).fetchone()["n"]
            results["indexing"] = {
                "documents": 40,
                "chunks": count,
                "elapsed_seconds": elapsed,
                "documents_per_minute": 40 / elapsed * 60,
                "includes": "HTTP submission, queue wait, chunking, embedding, publication; unique documents and empty tenant embedding cache",
            }
    finally:
        cleanup(ingest_tenant)
    summary = {
        "executed_at": datetime.now(UTC).isoformat(),
        "machine": platform.platform(),
        "processor": subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
        ).strip(),
        "memory_bytes": int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)),
        "local_model": settings.generation_model,
        "duration_note": "30-second query workloads; laptop characterization, not a production capacity claim",
        "ledger_including_warmup": ledger,
        "indexing": results["indexing"],
    }
    for name in ["cached", "uncached", "ingestion_http"]:
        metrics = results[name]["metrics"]
        duration = metrics["http_req_duration"]["values"]
        summary[name] = {
            "requests": metrics["http_reqs"]["values"]["count"],
            "requests_per_second": metrics["http_reqs"]["values"]["rate"],
            "p50_ms": duration["med"],
            "p95_ms": duration["p(95)"],
            "p99_ms": duration["p(99)"],
            "http_error_rate": metrics["http_req_failed"]["values"]["rate"],
            "dropped_iterations": metrics.get("dropped_iterations", {})
            .get("values", {})
            .get("count", 0),
            "cache_hit_rate": metrics.get("answer_cache_hit", {}).get("values", {}).get("rate"),
            "answer_failure_rate": metrics.get("answer_failure", {}).get("values", {}).get("rate"),
        }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    (ROOT / "artifacts/benchmarks/latest.json").write_text(
        json.dumps(summary, indent=2, default=str) + "\n"
    )
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
