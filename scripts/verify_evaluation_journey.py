"""Opt-in isolated synthetic durable-worker journey with real local inference.

Run ONLY through scripts/isolated.py after other model jobs finish. This creates
40 machine-authored label fixtures to exercise the existing review gate, selects
one question, and removes the disposable tenant afterwards. The approval fields
are fixture data, NOT human review or corpus-quality acceptance. All application,
worker, retrieval, generation and judging paths are the real implementation.
"""

import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import psycopg
from psycopg.rows import dict_row

from atlas import db
from atlas.accounts import COOKIE
from atlas.auth import digest
from atlas.config import settings
from atlas.evaluation import label_hash
from atlas.main import app

OUTPUT = Path("artifacts/upgrade/evaluation-journey.json")


def assert_isolated():
    for value in [
        settings.database_url,
        settings.database_admin_url,
        settings.worker_database_url,
        settings.identity_database_url,
    ]:
        assert urlsplit(value).path.startswith("/atlas_upgrade_verify_"), (
            "Refusing non-isolated PostgreSQL"
        )
    assert urlsplit(settings.redis_url).path == "/15"
    assert urlsplit(settings.cache_redis_url).path == "/15"
    assert not settings.paid_providers_enabled


def clean_fixture(tenant, user):
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "UPDATE atlas.documents SET current_version_id=NULL,pending_version_id=NULL WHERE tenant_id=%s",
            (tenant,),
        )
        for table in [
            "outbox",
            "ingestion_jobs",
            "evaluation_jobs",
            "eval_runs",
            "eval_labels",
            "bookmarks",
            "messages",
            "conversations",
            "queries",
            "feedback",
            "notifications",
            "access_requests",
            "resource_grants",
            "entities",
            "chunk_terms",
            "embeddings",
            "chunks",
            "document_versions",
            "embedding_cache",
            "documents",
            "collections",
            "reservations",
            "budget_periods",
            "tenant_limits",
            "usage_ledger",
            "team_members",
            "memberships",
            "invitations",
            "teams",
            "api_keys",
            "service_accounts",
            "spaces",
            "audit_events",
        ]:
            conn.execute(f"DELETE FROM atlas.{table} WHERE tenant_id=%s", (tenant,))
        conn.execute("DELETE FROM atlas.tenants WHERE id=%s", (tenant,))
        conn.execute("DELETE FROM atlas.users WHERE id=%s", (user,))


def checked(response, status=200):
    assert response.status_code == status, (
        f"{response.request.url.path}: expected {status}, got {response.status_code}"
    )
    return response.json()


async def main():
    assert_isolated()
    started = time.monotonic()
    tenant, user, session = uuid4(), uuid4(), uuid4()
    raw = secrets.token_urlsafe(40)
    worker = None
    snapshots = []
    report = {
        "basis": "Automated synthetic fixture only; NOT human review, corpus-quality acceptance or judge calibration",
        "approval_fields": "Populated only in disposable isolated test database to exercise the existing review gate",
        "reviewer": "Automated synthetic fixture (not a human reviewer)",
        "transport": "Real ASGI HTTP routes and separate real atlas.worker process; isolated PostgreSQL and Redis15",
        "started_at": datetime.now(UTC).isoformat(),
        "isolated_database": urlsplit(settings.database_url).path.removeprefix("/"),
        "selected_questions": 1,
        "gate_fixture_labels": 40,
    }
    with psycopg.connect(settings.database_admin_url) as conn:
        pending = conn.execute(
            "SELECT count(*) FROM atlas.evaluation_jobs WHERE status IN ('queued','running')"
        ).fetchone()
        assert pending is not None and pending[0] == 0, "Other isolated evaluation jobs are pending"
        conn.execute(
            "INSERT INTO atlas.tenants(id,slug,name,claimed_at) VALUES(%s,%s,'Synthetic evaluation verification',now())",
            (tenant, f"synthetic-eval-{tenant.hex[:12]}"),
        )
        conn.execute(
            "INSERT INTO atlas.users(id,email,name,password_hash,email_verified_at) VALUES(%s,%s,'Automated synthetic fixture (not a human reviewer)','!',now())",
            (user, f"synthetic-{user}@example.test"),
        )
        conn.execute(
            "INSERT INTO atlas.memberships(tenant_id,user_id,role) VALUES(%s,%s,'owner')",
            (tenant, user),
        )
        conn.execute(
            "INSERT INTO atlas.user_sessions(id,user_id,digest,expires_at) VALUES(%s,%s,%s,now()+interval '1 hour')",
            (session, user, digest(raw)),
        )
    try:
        await db.pool.open(wait=True)
        print(
            "Isolated synthetic fixture created; starting real worker and document indexing",
            flush=True,
        )
        worker = await asyncio.to_thread(
            subprocess.Popen,
            [sys.executable, "-m", "atlas.worker"],
            env={**os.environ, "TOKENIZERS_PARALLELISM": "false"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://verification.local",
            headers={
                "X-Atlas-Client": "console",
                "Origin": settings.app_url,
                "X-Atlas-Tenant": str(tenant),
            },
            timeout=40,
        ) as client:
            client.cookies.set(COOKIE, raw)
            documents = []
            for index in range(40):
                content = (
                    "The synthetic Juniper service runs three replicas. Its release approval code is MAPLE-392. The Platform team owns synthetic Juniper."
                    if index == 0
                    else f"Synthetic fixture {index:02d} describes unrelated training notes for demonstration only. The author is an automated verification fixture."
                )
                record = checked(
                    await client.post(
                        "/api/library/text",
                        json={
                            "title": f"Synthetic evaluation fixture {index:02d}",
                            "content": content,
                        },
                    ),
                    202,
                )
                documents.append(record["id"])
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                response = checked(await client.get("/api/library?page_size=100"))
                rows = response["items"]
                if len(rows) == 40 and all(row["status"] == "ready" for row in rows):
                    break
                assert not any(row["status"] == "failed" for row in rows), (
                    "Actual worker indexing failed"
                )
                assert worker.poll() is None, "Worker exited during indexing"
                await asyncio.sleep(2)
            else:
                raise AssertionError(
                    "Real worker did not index40 fixture documents within240seconds"
                )
            question = "How many replicas does synthetic Juniper run, and what is its release approval code?"
            with psycopg.connect(settings.database_admin_url, row_factory=dict_row) as conn:
                rows = conn.execute(
                    "SELECT id,content,content_hash FROM atlas.documents WHERE tenant_id=%s",
                    (tenant,),
                ).fetchall()
                for row in rows:
                    primary = str(row["id"]) == documents[0]
                    label = {
                        "question": question
                        if primary
                        else f"What does synthetic fixture {row['id']} describe?",
                        "document_id": row["id"],
                        "source_hash": row["content_hash"],
                        "start_offset": 0,
                        "end_offset": len(row["content"]),
                        "split": "development" if primary else "held_out",
                    }
                    assert (
                        hashlib.sha256(row["content"].encode()).hexdigest() == row["content_hash"]
                    )
                    conn.execute(
                        "INSERT INTO atlas.eval_labels(tenant_id,id,question,document_id,source_hash,start_offset,end_offset,split,reviewed,review_hash,reviewed_by,reviewed_at) VALUES(%s,%s,%s,%s,%s,0,%s,%s,true,%s,%s,now())",
                        (
                            tenant,
                            uuid4(),
                            label["question"],
                            row["id"],
                            row["content_hash"],
                            label["end_offset"],
                            label["split"],
                            label_hash(label),
                            user,
                        ),
                    )
            job = checked(
                await client.post(
                    "/api/evaluation/jobs",
                    json={"with_judge": True, "top_k": 3, "split": "development"},
                ),
                202,
            )["job"]
            print(
                "40 synthetic documents indexed; one real generation/judge evaluation queued",
                flush=True,
            )
            snapshots.append({key: job[key] for key in ["status", "progress", "total"]})
            deadline = time.monotonic() + 360
            while time.monotonic() < deadline:
                job = checked(await client.get(f"/api/evaluation/jobs/{job['id']}"))["job"]
                snapshot = {key: job[key] for key in ["status", "progress", "total"]}
                if snapshot != snapshots[-1]:
                    snapshots.append(snapshot)
                if job["status"] in {"completed", "failed", "cancelled"}:
                    break
                assert worker.poll() is None, "Worker exited during evaluation"
                await asyncio.sleep(2)
            assert job["status"] == "completed", (
                f"Durable job failed: {job['status']} / {job.get('error_code')}"
            )
            assert job["progress"] == job["total"] == 1
            runs = checked(await client.get("/api/evaluation/runs"))
            run = next(row for row in runs if row["id"] == str(job["result_id"]))
            assert run["label_count"] == 1
            result = run["results"][0]
            assert result["expected"] and result["retrieved"] and result["dependencies"]
            assert result["recall_at_5"] > 0
            judgment = result["judgment"]
            assert judgment and judgment["answer"] and judgment["score"] is not None
            assert "MAPLE-392" in judgment["answer"]
            with psycopg.connect(settings.database_admin_url, row_factory=dict_row) as conn:
                usage = conn.execute(
                    "SELECT operation,model,input_tokens,output_tokens,actual_api_cost_usd,status,principal_kind FROM atlas.usage_ledger WHERE tenant_id=%s AND operation='evaluation'",
                    (tenant,),
                ).fetchall()
            assert len(usage) == 1 and usage[0]["status"] == "completed"
            assert usage[0]["input_tokens"] > 0 and usage[0]["output_tokens"] > 0
            assert float(usage[0]["actual_api_cost_usd"]) == 0
            report.update(
                result="passed",
                progress=snapshots,
                metrics=run["metrics"],
                judgment={key: judgment.get(key) for key in ["answer", "score", "reason"]},
                evidence_count=len(result["dependencies"]),
                usage=usage,
                elapsed_seconds=round(time.monotonic() - started, 2),
                fixture_cleanup="completed in finally",
            )
    finally:
        if worker is not None:
            worker.terminate()
            try:
                await asyncio.to_thread(worker.wait, 15)
            except subprocess.TimeoutExpired:
                worker.kill()
                await asyncio.to_thread(worker.wait, 5)
        await db.pool.close()
        await db.application_pool.close()
        await db.identity_pool.close()
        clean_fixture(tenant, user)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(
        json.dumps(
            {
                "result": report["result"],
                "basis": report["basis"],
                "elapsed_seconds": report["elapsed_seconds"],
                "artifact": str(OUTPUT),
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
