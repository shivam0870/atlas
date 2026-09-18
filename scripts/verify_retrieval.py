"""Measure actual local embedding and permission-filtered retrieval on preserved corpus data."""

import asyncio
import json
import time
from pathlib import Path

from starlette.requests import Request

from atlas.auth import authenticate
from atlas.db import pool
from atlas.retrieval import retrieve


async def main():
    workspace = next(
        row
        for row in json.loads(Path(".local/workspaces.json").read_text())
        if row["slug"] == "acme"
    )
    await pool.open(wait=True)
    try:
        request = Request(
            {
                "type": "http",
                "headers": [(b"authorization", ("Bearer " + workspace["key"]).encode())],
            }
        )
        identity = await authenticate(request)
        start = time.perf_counter()
        result = await retrieve(identity.tenant_id, "What is the release approval code?", top_k=5)
        elapsed = time.perf_counter() - start
        assert result[0]
        artifact = {
            "mode": "hybrid",
            "real_local_embedding": True,
            "real_database_rls": True,
            "seconds": round(elapsed, 3),
            "sources": len(result[0]),
        }
        Path("artifacts/upgrade/retrieval-performance.json").write_text(
            json.dumps(artifact, indent=2) + "\n"
        )
        print(json.dumps(artifact, indent=2))
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
