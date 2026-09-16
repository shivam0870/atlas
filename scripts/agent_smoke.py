"""Verify a real agent answer that requires both inventory and runbook evidence."""

import json
import time
from pathlib import Path

import httpx

workspace = next(
    w for w in json.loads(Path(".local/workspaces.json").read_text()) if w["slug"] == "acme"
)
start = time.perf_counter()
with httpx.Client(timeout=180, headers={"Authorization": "Bearer " + workspace["key"]}) as client:
    response = client.post(
        "http://127.0.0.1:8100/api/query",
        json={
            "question": "Look up the checkout service in structured inventory and search its release runbook. How many replicas does it use, and which team and code approve its releases?",
            "mode": "agent",
        },
    )
    response.raise_for_status()
events = []
for frame in response.text.split("\n\n"):
    if "\ndata: " in frame:
        event, data = frame.split("\ndata: ", 1)
        events.append((event.removeprefix("event: "), json.loads(data)))
answer = "".join(data["text"] for event, data in events if event == "delta")
steps = [data for event, data in events if event == "step"]
sources = next(data["sources"] for event, data in events if event == "sources")
status = next(data["status"] for event, data in events if event == "done")
assert {"search_documents", "query_structured_data"} <= {s.get("tool") for s in steps}, steps
assert any(s.get("kind") == "structured" for s in sources)
assert "AMBER-ORCHID" in answer and "3" in answer and "Platform" in answer, answer
assert status == "completed", status
result = {
    "answer": answer,
    "tools": [s.get("tool") for s in steps],
    "source_kinds": sorted({s.get("kind", "document") for s in sources}),
    "status": status,
    "elapsed_seconds": round(time.perf_counter() - start, 3),
    "api_cost_usd": 0,
}
Path("artifacts/agent-smoke.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
