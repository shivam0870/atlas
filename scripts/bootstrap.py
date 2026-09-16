"""One-command native setup after installing Python 3.12, uv, Node, Docker, and Ollama."""

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)


def run(*args):
    subprocess.run(args, check=True)


run("python3", "scripts/configure.py")
run("uv", "sync", "--locked")
run("docker-compose", "up", "-d", "postgres", "redis", "cache", "collector")
run(".venv/bin/alembic", "upgrade", "head")
workspaces = (
    json.loads(Path(".local/workspaces.json").read_text())
    if Path(".local/workspaces.json").exists()
    else []
)
for name, slug in [("Acme Engineering", "acme"), ("Northstar Labs", "northstar")]:
    if not any(w["slug"] == slug for w in workspaces):
        run(".venv/bin/python", "-m", "atlas.cli", "tenant", name, slug)
run(".venv/bin/python", "scripts/download_models.py")
subprocess.run(["npm", "ci"], cwd=ROOT / "web", check=True)
subprocess.run(["npm", "run", "build"], cwd=ROOT / "web", check=True)
run("python3", "scripts/local.py", "start")
run(
    "curl",
    "-fsS",
    "http://127.0.0.1:11435/api/pull",
    "-H",
    "Content-Type: application/json",
    "-d",
    json.dumps({"model": "qwen3:4b-instruct-2507-q4_K_M", "stream": False}),
)
run(".venv/bin/python", "scripts/seed_corpus.py")
run(".venv/bin/python", "scripts/seed_inventory.py")
run(".venv/bin/python", "scripts/prepare_labels.py")
print("Ready: http://127.0.0.1:8100")
