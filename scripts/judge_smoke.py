"""Exercise the local judge on explicit positive/negative synthetic claims."""

import asyncio
import json
from pathlib import Path

from atlas.judge import judge_answer


async def main():
    sources = [{"content": "The synthetic Juniper service runs with three replicas."}]
    supported = await judge_answer("Juniper runs with three replicas [1].", sources)
    unsupported = await judge_answer("Juniper runs with eight replicas [1].", sources)
    result = {
        "basis": "two deterministic synthetic claims; not a human calibration study",
        "supported": supported,
        "unsupported": unsupported,
        "api_cost_usd": 0,
    }
    Path("artifacts/judge-smoke.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    assert supported["score"] == 1 and unsupported["score"] == 0


asyncio.run(main())
