"""Start/stop only this project's native processes; keep state and logs private."""

import argparse
import json
import os
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".local/processes.json"


def identity(pid):
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="], capture_output=True, text=True
    )
    return result.stdout.strip()


def healthy(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "status", "restart"])
    parser.add_argument(
        "--services",
        nargs="+",
        choices=["ollama", "api", "worker", "operator-scheduler"],
        help="Operate only on selected native services; preserve other running processes",
    )
    args = parser.parse_args()
    os.chdir(ROOT)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    selected = set(args.services or ("ollama", "api", "worker", "operator-scheduler"))
    if args.action in {"stop", "restart"}:
        for name, process in reversed(list(state.items())):
            if name not in selected:
                continue
            if identity(process["pid"]) == process["identity"]:
                os.killpg(process["pid"], signal.SIGTERM)
                print("Stopping " + name)
        for _ in range(35):
            if not any(
                identity(p["pid"]) == p["identity"] for name, p in state.items() if name in selected
            ):
                break
            time.sleep(1)
        state = {
            name: p
            for name, p in state.items()
            if name not in selected or identity(p["pid"]) == p["identity"]
        }
        STATE.write_text(json.dumps(state))
        if any(name in selected for name in state):
            raise SystemExit("A worker is finishing an in-flight document. Retry after it drains.")
    if args.action in {"start", "restart"}:
        if any((ROOT / ".local/clamav/database").glob("daily.*")):
            subprocess.run(["python3", "scripts/scanner.py", "start"], check=True)
        subprocess.run(
            ["docker-compose", "up", "-d", "postgres", "redis", "cache", "collector", "mailpit"],
            check=True,
        )
        logs = ROOT / ".local/logs"
        logs.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "TOKENIZERS_PARALLELISM": "false",
            "OMP_NUM_THREADS": "4",
            "OTEL_ENABLED": "true",
            "OLLAMA_MODELS": str(ROOT / ".models/ollama"),
            "OLLAMA_HOST": "127.0.0.1:11435",
            "OLLAMA_NO_CLOUD": "1",
            "OLLAMA_NUM_PARALLEL": "1",
            "OLLAMA_CONTEXT_LENGTH": "8192",
        }
        programs = {
            "ollama": (["ollama", "serve"], "http://127.0.0.1:11435/api/tags"),
            "api": (
                [
                    str(ROOT / ".venv/bin/uvicorn"),
                    "atlas.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8100",
                    "--no-access-log",
                    "--timeout-graceful-shutdown",
                    "30",
                ],
                "http://127.0.0.1:8100/ready",
            ),
            "worker": ([str(ROOT / ".venv/bin/python"), "-m", "atlas.worker"], None),
            "operator-scheduler": (
                [str(ROOT / ".venv/bin/python"), "scripts/operations_schedule.py"],
                None,
            ),
        }
        for name, (command, url) in programs.items():
            if name not in selected:
                continue
            if name in state and identity(state[name]["pid"]) == state[name]["identity"]:
                continue
            if url and healthy(url):
                print(name + " is already available (not managed by this launcher)")
                continue
            with (logs / (name + ".log")).open("ab") as log:
                process = subprocess.Popen(
                    command, cwd=ROOT, env=env, stdout=log, stderr=log, start_new_session=True
                )
            time.sleep(0.3)
            state[name] = {"pid": process.pid, "identity": identity(process.pid)}
            STATE.write_text(json.dumps(state, indent=2) + "\n")
            STATE.chmod(0o600)
            (logs / (name + ".log")).chmod(0o600)
            if url:
                for _ in range(60):
                    if healthy(url):
                        break
                    if process.poll() is not None:
                        raise SystemExit(name + " exited; see .local/logs/" + name + ".log")
                    time.sleep(1)
                else:
                    raise SystemExit(name + " did not become ready; see its local log")
            print("Started " + name)
    if args.action != "stop":
        print(
            "Atlas: http://127.0.0.1:8100 — "
            + ("ready" if healthy("http://127.0.0.1:8100/ready") else "offline")
        )


if __name__ == "__main__":
    main()
