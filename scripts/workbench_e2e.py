"""Run isolated browser-test services without replacing the live console assets.

Build with: npm --prefix web run build -- --outDir ../.local/workbench-e2e/dist
Run through scripts/isolated.py; start separate `api` and `worker` processes.
"""

import os
import socket
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in {"api", "worker", "run"}:
        raise SystemExit("Usage: workbench_e2e.py api|worker|run [specs] (via scripts/isolated.py)")
    for name in (
        "DATABASE_URL",
        "DATABASE_ADMIN_URL",
        "WORKER_DATABASE_URL",
        "IDENTITY_DATABASE_URL",
    ):
        if not urlsplit(os.environ.get(name, "")).path.startswith("/atlas_upgrade_verify_"):
            raise SystemExit("Browser tests require the isolated upgrade database")
    for name in ("REDIS_URL", "CACHE_REDIS_URL"):
        if urlsplit(os.environ.get(name, "")).path != "/15":
            raise SystemExit("Browser tests require isolated Redis database 15")
    root = Path(__file__).resolve().parents[1]
    workspace = root / ".local/workbench-e2e"
    if mode == "run":
        return run_suite(root, workspace, sys.argv[2:])
    runtime = workspace / "runtime"
    (runtime / "web").mkdir(parents=True, exist_ok=True)
    for link, target in (
        (runtime / ".env", root / ".env"),
        (runtime / ".models", root / ".models"),
        (runtime / "web/dist", workspace / "dist"),
    ):
        if not link.is_symlink() and not link.exists():
            link.symlink_to(target)
    if not (runtime / "web/dist/index.html").is_file():
        raise SystemExit("Build the isolated frontend output before starting test services")
    os.environ.update(
        APP_URL="http://127.0.0.1:8112",
        SMTP_HOST="127.0.0.1",
        SMTP_PORT="51025",
        SMTP_USERNAME="",
        SMTP_PASSWORD="",
        SMTP_STARTTLS="false",
        DATA_DIR=str(runtime / ".local"),
        TOKENIZERS_PARALLELISM="false",
    )
    os.chdir(runtime)
    if mode == "api":
        import uvicorn

        uvicorn.run("atlas.main:app", host="127.0.0.1", port=8112, access_log=False)
    else:
        import asyncio

        from atlas.worker import main as worker

        asyncio.run(worker())


def run_suite(root, workspace, specs):
    """Own only this run's processes; refuse to reuse or stop a preexisting API."""
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", 8112))
        except PermissionError as exc:
            raise SystemExit("The test harness needs permission to bind local port 8112") from exc
        except OSError as exc:
            raise SystemExit(
                "Port 8112 is occupied; stop only the earlier isolated test API"
            ) from exc
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    subprocess.run(
        ["npm", "run", "build", "--", "--outDir", "../.local/workbench-e2e/dist"],
        cwd=root / "web",
        check=True,
    )
    processes = []
    result = 1
    with ExitStack() as stack:
        try:
            for service in ("api", "worker"):
                log_path = workspace / f"{service}.log"
                log = stack.enter_context(log_path.open("w"))
                log_path.chmod(0o600)
                process = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), service],
                    cwd=root,
                    stdout=log,
                    stderr=log,
                )
                processes.append(process)
            for _ in range(120):
                if any(process.poll() is not None for process in processes):
                    raise RuntimeError("Isolated test service exited; inspect private service logs")
                try:
                    with urlopen("http://127.0.0.1:8112/health", timeout=2) as response:
                        if response.status == 200:
                            break
                except (URLError, TimeoutError):
                    pass
                time.sleep(0.5)
            else:
                raise RuntimeError("Isolated API did not become ready within 60 seconds")
            env = {**os.environ, "ATLAS_BROWSER_URL": "http://127.0.0.1:8112"}
            result = subprocess.run(
                ["npm", "run", "test:e2e", "--", *specs], cwd=root / "web", env=env
            ).returncode
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
            for process in reversed(processes):
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    raise SystemExit(result)


if __name__ == "__main__":
    main()
