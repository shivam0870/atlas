"""Create review candidates; never grant human approval on the user's behalf."""

import asyncio
import json
import re
from pathlib import Path
from uuid import UUID, uuid4

from atlas.db import pool, transaction


async def main():
    workspaces = json.loads(Path(".local/workspaces.json").read_text())
    tenant = UUID(next(w["id"] for w in workspaces if w["slug"] == "acme"))
    await pool.open(wait=True)
    try:
        async with transaction(tenant) as conn:
            docs = await (
                await conn.execute(
                    "SELECT * FROM atlas.documents WHERE tenant_id=%s AND status='ready' AND metadata->>'category'='Kubernetes documentation' ORDER BY title LIMIT 50",
                    (tenant,),
                )
            ).fetchall()
            existing = await (
                await conn.execute(
                    "SELECT count(*) n FROM atlas.eval_labels WHERE tenant_id=%s", (tenant,)
                )
            ).fetchone()
            for i, doc in enumerate(docs if existing["n"] < 50 else []):
                # Begin at a substantive paragraph; display the exact passage for correction.
                match = re.search(r"(?m)^[A-Za-z][^\n]{70,}", doc["content"])
                start = match.start() if match else 0
                end = min(start + 220, len(doc["content"]))
                await conn.execute(
                    "INSERT INTO atlas.eval_labels(tenant_id,id,question,document_id,source_hash,start_offset,end_offset,split) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,document_id) DO NOTHING",
                    (
                        tenant,
                        uuid4(),
                        f"What does the documentation explain about {doc['title']}?",
                        doc["id"],
                        doc["content_hash"],
                        start,
                        end,
                        "development" if i < 30 else "held_out",
                    ),
                )
            rows = await (
                await conn.execute(
                    "SELECT l.*,d.title source_title,substring(d.content FROM l.start_offset+1 FOR l.end_offset-l.start_offset) evidence FROM atlas.eval_labels l JOIN atlas.documents d ON d.tenant_id=l.tenant_id AND d.id=l.document_id WHERE l.tenant_id=%s ORDER BY l.created_at,l.id",
                    (tenant,),
                )
            ).fetchall()
            Path("datasets/labels.review.jsonl").write_text(
                "".join(json.dumps(row, default=str) + "\n" for row in rows)
            )
        print("Prepared review candidates. No labels have been marked reviewed.")
    finally:
        await pool.close()


asyncio.run(main())
