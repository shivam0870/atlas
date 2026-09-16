"""Exercise the non-root packaged API and UI using the native local inference endpoint."""

import json
import time
from pathlib import Path

import httpx

workspace = next(
    w for w in json.loads(Path(".local/workspaces.json").read_text()) if w["slug"] == "acme"
)
start = time.perf_counter()
with httpx.Client(
    base_url="http://127.0.0.1:8101",
    headers={"Authorization": "Bearer " + workspace["key"]},
    timeout=180,
) as client:
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    assert '<div id="root">' in client.get("/").text
    assert client.get("/api/me").json()["tenant_id"] == workspace["id"]
    response = client.post(
        "/api/query",
        json={
            "question": "From the checkout runbook, state only its release approval code.",
            "top_k": 1,
            "use_cache": False,
        },
    )
    response.raise_for_status()
    answer = ""
    done = {}
    for frame in response.text.split("\n\n"):
        if "\ndata: " not in frame:
            continue
        event, raw = frame.split("\ndata: ", 1)
        data = json.loads(raw)
        if event == "event: delta":
            answer += data["text"]
        if event == "event: done":
            done = data
    assert done.get("status") == "completed" and "AMBER-ORCHID" in answer, response.text
result = {
    "packaged_ui": True,
    "health": True,
    "readiness": True,
    "authenticated_tenant": True,
    "answer": answer,
    "status": done["status"],
    "cache_hit": done["cache_hit"],
    "elapsed_seconds": round(time.perf_counter() - start, 3),
    "inference": "native Ollama via host.lima.internal; Colima has 2 GiB and is not used for 4B CPU inference",
    "api_cost_usd": 0,
}
Path("artifacts/container-smoke.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
