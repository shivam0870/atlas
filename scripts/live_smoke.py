import asyncio
import json
import time
from pathlib import Path

import httpx


async def main():
    workspaces = json.loads(Path(".local/workspaces.json").read_text())
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8100", timeout=180) as client:
        for workspace in workspaces:
            client.headers["Authorization"] = "Bearer " + workspace["key"]
            question = (
                "What is the checkout release approval code?"
                if workspace["slug"] == "acme"
                else "What is the catalog release approval code?"
            )
            started = time.perf_counter()
            events = []
            async with client.stream("POST", "/api/query", json={"question": question}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                        events.append(data)
                        if "text" in data:
                            print(data["text"], end="", flush=True)
            print(
                "\n",
                workspace["name"],
                "elapsed",
                round(time.perf_counter() - started, 2),
                "seconds",
            )
            out = Path("artifacts/private")
            out.mkdir(parents=True, exist_ok=True)
            (out / f"smoke-{workspace['slug']}.json").write_text(json.dumps(events, indent=2))
            if events[-1].get("status") not in {"completed", "abstained", "uncited"}:
                raise RuntimeError("Live generation did not complete")


asyncio.run(main())
