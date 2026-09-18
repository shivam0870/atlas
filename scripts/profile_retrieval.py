"""Opt-in real PostgreSQL query-plan diagnostic; run pytest against this file explicitly."""

import hashlib
import json
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from atlas.config import settings


def test_profile_existing_corpus():
    workspace = next(
        w for w in json.loads(Path(".local/workspaces.json").read_text()) if w["slug"] == "acme"
    )
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        actor = conn.execute(
            "SELECT * FROM atlas.resolve_api_identity(%s)",
            (hashlib.sha256(workspace["key"].encode()).hexdigest(),),
        ).fetchone()
        assert actor
        for key, value in {
            "app.tenant_id": actor["tenant_id"],
            "app.principal_id": actor["service_account_id"],
            "app.principal_kind": "service",
            "app.key_id": actor["key_id"],
        }.items():
            conn.execute("SELECT set_config(%s,%s,true)", (key, str(value)))
        collection = conn.execute(
            "SELECT id FROM atlas.collections WHERE tenant_id=%s ORDER BY created_at LIMIT 1",
            (actor["tenant_id"],),
        ).fetchone()
        assert collection
        query = """SELECT c.id FROM atlas.chunks c JOIN atlas.embeddings e ON e.tenant_id=c.tenant_id AND e.chunk_id=c.id JOIN atlas.documents d ON d.tenant_id=c.tenant_id AND d.id=c.document_id LEFT JOIN atlas.document_versions v ON v.tenant_id=c.tenant_id AND v.id=c.version_id WHERE c.tenant_id=%s AND c.collection_id=%s AND d.status='ready' AND d.lifecycle='active' AND c.version_id IS NOT DISTINCT FROM d.current_version_id ORDER BY e.embedding <=> %s::vector LIMIT 20"""
        params = (actor["tenant_id"], collection["id"], "[" + ",".join(["1"] + ["0"] * 383) + "]")
        plan = conn.execute("EXPLAIN (FORMAT JSON) " + query, params).fetchone()["QUERY PLAN"]
        output = {"plan": plan}
        conn.execute("SET LOCAL statement_timeout='25s'")
        conn.execute("SET LOCAL jit=off")
        start = time.perf_counter()
        try:
            rows = conn.execute(query, params).fetchall()
            output.update(jit_off_seconds=round(time.perf_counter() - start, 3), rows=len(rows))
        except psycopg.errors.QueryCanceled:
            output.update(jit_off_seconds=round(time.perf_counter() - start, 3), timed_out=True)
        target = Path("artifacts/upgrade/retrieval-profile.json")
        target.write_text(json.dumps(output, indent=2) + "\n")
        print(json.dumps({key: value for key, value in output.items() if key != "plan"}))
