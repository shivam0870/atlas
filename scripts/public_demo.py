"""Manage the dedicated native demo and its ngrok endpoint, never the daily-use app.

Requires the private, migrated .local/public-demo instance and configured ngrok.
Run with .venv/bin/python scripts/public_demo.py start|stop|status|backup|upgrade.
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key
from local import healthy, identity

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / ".local/public-demo"
STATE = DEMO / "processes.json"
API = "http://127.0.0.1:8120"
INSPECTOR = "http://127.0.0.1:4041/api/tunnels"
PRIVATE_KEYS = {
    "DATABASE_ADMIN_URL",
    "POSTGRES_PASSWORD",
    "APP_DB_PASSWORD",
    "IDENTITY_DB_PASSWORD",
    "WORKER_DB_PASSWORD",
    "WORKER_DATABASE_URL",
}


def save(state):
    STATE.write_text(json.dumps(state, indent=2) + "\n")
    STATE.chmod(0o600)


def active(process):
    return process and identity(process["pid"]) == process["identity"]


def stop(state, names):
    for name in names:
        process = state.get(name)
        if active(process):
            os.killpg(process["pid"], signal.SIGTERM)
    deadline = time.monotonic() + 40
    while any(active(state.get(name)) for name in names) and time.monotonic() < deadline:
        time.sleep(0.5)
    for name in names:
        if active(state.get(name)):
            raise SystemExit(f"{name} is draining. Retry after its in-flight work finishes.")
        state.pop(name, None)
    save(state)


def launch(state, name, command, env):
    if active(state.get(name)):
        return
    path = DEMO / "logs" / f"{name}.log"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "ab") as log:
        process = subprocess.Popen(
            command, cwd=DEMO, env=env, stdout=log, stderr=log, start_new_session=True
        )
    time.sleep(0.2)
    if process.poll() is not None:
        raise SystemExit(f"{name} exited; inspect its private demo log.")
    state[name] = {"pid": process.pid, "identity": identity(process.pid)}
    save(state)


def tunnel_url():
    with urllib.request.urlopen(INSPECTOR, timeout=2) as response:
        tunnels = json.load(response)["tunnels"]
    for tunnel in tunnels:
        url = tunnel.get("public_url", "")
        if urlsplit(url).scheme == "https" and tunnel["config"]["addr"] == API:
            return url.rstrip("/")
    raise RuntimeError("The dedicated HTTPS tunnel is not ready")


def operator_environment(instance=None):
    instance = instance or DEMO
    values = {}
    for name in ("migration.env", ".env", "worker.env"):
        values.update(
            {k.upper(): v for k, v in dotenv_values(instance / name).items() if v is not None}
        )
    roles = {
        "DATABASE_ADMIN_URL": "atlas_admin",
        "DATABASE_URL": "atlas_app",
        "IDENTITY_DATABASE_URL": "atlas_identity",
        "WORKER_DATABASE_URL": "atlas_worker",
    }
    try:
        targets = {name: urlsplit(values.get(name, "")) for name in roles}
        valid = len({target.path for target in targets.values()}) == 1 and all(
            target.scheme in {"postgres", "postgresql"}
            and target.hostname in {"localhost", "127.0.0.1"}
            and target.port == 55432
            and target.username == roles[name]
            and re.fullmatch(r"/atlas_public_demo_[a-zA-Z0-9_]{1,45}", target.path)
            and not target.query
            and not target.fragment
            for name, target in targets.items()
        )
        queues = [urlsplit(values.get(name, "")) for name in ("REDIS_URL", "CACHE_REDIS_URL")]
        valid = (
            valid
            and len({queue.path for queue in queues}) == 1
            and all(
                queue.scheme == "redis"
                and queue.hostname in {"localhost", "127.0.0.1"}
                and queue.port == port
                and re.fullmatch(r"/(?:[1-9]|1[0-3])", queue.path)
                and not queue.query
                and not queue.fragment
                for queue, port in zip(queues, (56379, 56380), strict=True)
            )
        )
    except ValueError:
        valid = False
    if not valid:
        raise SystemExit(
            "Operator configuration must use dedicated public-demo database roles and isolated queues, without URL overrides"
        )
    return {**os.environ, **values}


def runtime_environment(values):
    if any(key.upper() in PRIVATE_KEYS for key in values):
        raise SystemExit("Migration credentials must not be in the runtime configuration.")
    return {**{k: v for k, v in os.environ.items() if k.upper() not in PRIVATE_KEYS}, **values}


def backup(state):
    env = operator_environment()
    stop(state, ["tunnel", "api", "worker", "operator-scheduler", "awake"])
    subprocess.run(
        [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/backup.py")],
        cwd=DEMO,
        env=env,
        check=True,
    )
    destination = max((DEMO / ".local/backups").glob("atlas-*"))
    for name in ("migration.env", "worker.env", "instance.json"):
        shutil.copy2(DEMO / name, destination / name)
        (destination / name).chmod(0o600)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status", "backup", "upgrade"])
    args = parser.parse_args()
    if not (DEMO / "instance.json").exists():
        raise SystemExit("Prepare the dedicated migrated demo instance first.")
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if args.action in {"backup", "upgrade"}:
        if args.action == "upgrade" and not (ROOT / "web/dist/index.html").is_file():
            raise SystemExit("Build the frontend before upgrading the public demo")
        backup(state)
        if args.action == "backup":
            print("Backup complete. Run public_demo.py start to reopen the demo.")
            return
        subprocess.run(
            [str(ROOT / ".venv/bin/alembic"), "upgrade", "head"],
            cwd=ROOT,
            env=operator_environment(),
            check=True,
        )
        # The post-migration backup records recovery evidence in the new schema.
        backup(state)
        args.action = "start"
    if args.action == "stop":
        stop(state, ["tunnel", "api", "worker", "operator-scheduler", "awake"])
        print("Dedicated demo stopped. Local app, database and model services are preserved.")
        return
    if args.action == "start":
        # Validate the complete target before starting a tunnel or any runtime
        # process; scheduler validation after launch is too late.
        operator_environment()
        values = {k: v for k, v in dotenv_values(DEMO / ".env").items() if v is not None}
        if not values.get("SMTP_PASSWORD") or values.get("SMTP_STARTTLS", "").lower() != "true":
            raise SystemExit("Configure real SMTP with TLS before opening the public demo.")
        runtime_environment(values)
        if not healthy("http://127.0.0.1:11435/api/tags"):
            raise SystemExit("The local model server must be running before starting the demo.")
        if healthy(API + "/ready") and not active(state.get("api")):
            raise SystemExit("Port 8120 belongs to another process; refusing to expose it.")
        if healthy(INSPECTOR) and not active(state.get("tunnel")):
            raise SystemExit("Port 4041 belongs to another process; refusing to reuse it.")
        config = Path.home() / "Library/Application Support/ngrok/ngrok.yml"
        if not config.exists():
            raise SystemExit("Configure ngrok locally before starting the demo.")
        overlay = DEMO / "ngrok-agent.yml"
        overlay.write_text('version: "3"\nagent:\n  web_addr: 127.0.0.1:4041\n')
        overlay.chmod(0o600)
        launch(
            state,
            "tunnel",
            [
                "ngrok",
                "http",
                API,
                "--config",
                f"{config},{overlay}",
                "--inspect=false",
                "--log=stdout",
                "--log-format=json",
            ],
            dict(os.environ),
        )
        url = None
        for _ in range(40):
            try:
                url = tunnel_url()
                break
            except (OSError, ValueError, KeyError, RuntimeError):
                if not active(state.get("tunnel")):
                    raise SystemExit("Tunnel failed; inspect the private tunnel log.") from None
                time.sleep(1)
        if not url:
            stop(state, ["tunnel"])
            raise SystemExit("Tunnel did not become ready.")
        if values.get("APP_URL") != url:
            stop(state, ["api", "worker"])
            set_key(DEMO / ".env", "APP_URL", url)
        values["APP_URL"] = url
        env = runtime_environment(values)
        launch(state, "awake", ["caffeinate", "-i"], dict(os.environ))
        launch(
            state,
            "api",
            [
                str(ROOT / ".venv/bin/uvicorn"),
                "atlas.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8120",
                "--no-access-log",
                "--timeout-graceful-shutdown",
                "30",
            ],
            env,
        )
        worker_env = {
            **env,
            **{k: v for k, v in dotenv_values(DEMO / "worker.env").items() if v is not None},
        }
        launch(state, "worker", [str(ROOT / ".venv/bin/python"), "-m", "atlas.worker"], worker_env)
        for _ in range(40):
            if healthy(API + "/ready"):
                break
            if not active(state.get("api")):
                raise SystemExit("Demo API failed; inspect its private log.")
            time.sleep(1)
        else:
            raise SystemExit("Demo API did not become ready; inspect its private log.")
        launch(
            state,
            "operator-scheduler",
            [
                str(ROOT / ".venv/bin/python"),
                str(ROOT / "scripts/operations_schedule.py"),
                "--instance-root",
                str(DEMO),
            ],
            operator_environment(),
        )
    url = dotenv_values(DEMO / ".env").get("APP_URL", "not configured")
    print(
        json.dumps(
            {
                "url": url,
                "ready": healthy(API + "/ready"),
                "processes": {name: bool(active(p)) for name, p in state.items()},
            }
        )
    )


if __name__ == "__main__":
    main()
