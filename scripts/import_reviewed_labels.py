"""Rebind an already human-reviewed export to byte-identical sources in a clean CI database."""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from atlas.db import pool, transaction
from atlas.evaluation import label_hash


async def main():
    rows = [
        json.loads(line)
        for line in Path("datasets/labels.review.jsonl").read_text().splitlines()
        if line.strip()
    ]
    if not 40 <= len(rows) <= 60 or any(
        not r["reviewed"]
        or not r.get("reviewed_by")
        or not r.get("reviewed_at")
        or r.get("review_hash") != label_hash(r)
        for r in rows
    ):
        raise SystemExit(
            "Import refused: every exported label must carry a valid existing human review."
        )
    tenant = UUID(
        next(
            w["id"]
            for w in json.loads(Path(".local/workspaces.json").read_text())
            if w["slug"] == "acme"
        )
    )
    await pool.open(wait=True)
    try:
        async with transaction(tenant) as conn:
            for row in rows:
                docs = await (
                    await conn.execute(
                        "SELECT id,length(content) n FROM atlas.documents WHERE tenant_id=%s AND content_hash=%s AND status='ready'",
                        (tenant, row["source_hash"]),
                    )
                ).fetchall()
                if (
                    len(docs) != 1
                    or not 0 <= row["start_offset"] < row["end_offset"] <= docs[0]["n"]
                ):
                    raise ValueError(
                        "Reviewed evidence cannot be uniquely rebound to identical source bytes"
                    )
                rebound = {**row, "document_id": docs[0]["id"]}
                await conn.execute(
                    "INSERT INTO atlas.eval_labels(tenant_id,id,question,document_id,source_hash,start_offset,end_offset,split,reviewed,reviewed_by,reviewed_at,review_hash) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,true,%s,%s,%s) ON CONFLICT(tenant_id,document_id) DO NOTHING",
                    (
                        tenant,
                        uuid4(),
                        row["question"],
                        docs[0]["id"],
                        row["source_hash"],
                        row["start_offset"],
                        row["end_offset"],
                        row["split"],
                        UUID(row["reviewed_by"]),
                        datetime.fromisoformat(row["reviewed_at"]),
                        label_hash(rebound),
                    ),
                )
        print("Imported existing human reviews; no new approvals were generated.")
    finally:
        await pool.close()


asyncio.run(main())
