"""Force an unavailable primary; exercise the real secondary local backend."""

import asyncio
import json
import time
from pathlib import Path

from atlas.config import settings
from atlas.generation import generate
from atlas.serving import redis


async def main():
    settings.ollama_url = "http://127.0.0.1:11599"
    settings.fallback_enabled = True
    settings.model_timeout = 180
    started = time.perf_counter()
    parts = [
        part
        async for part in generate(
            "What is the release code?",
            [
                {
                    "title": "Synthetic recovery fixture",
                    "content": "The release code is COPPER-FERN.",
                }
            ],
        )
    ]
    answer = "".join(p["text"] for p in parts if p["type"] == "delta")
    assert "COPPER-FERN" in answer and "[1]" in answer
    usage = next(p for p in parts if p["type"] == "usage")
    assert usage["backend"] == "llama.cpp"
    result = {
        "forced_primary": "connection_refused",
        "recovered_backend": usage["backend"],
        "answer": answer,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "api_cost_usd": 0,
        **usage,
    }
    Path("artifacts/failover-smoke.json").write_text(json.dumps(result, indent=2) + "\n")
    # The injected outage must not affect normal traffic after the experiment.
    await redis.delete(
        "atlas:circuit:ollama",
        "atlas:cooldown:ollama",
        "atlas:failures:ollama",
        "atlas:probe:ollama",
    )
    await redis.aclose()
    print(json.dumps(result, indent=2))


asyncio.run(main())
