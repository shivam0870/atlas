"""Daily snapshot backups and weekly isolated restore drills for the native operator runtime."""

import argparse
import fcntl
import json
import os
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def tick(backup_hours=24, restore_days=7):
    # Configuration is loaded after selecting the instance, before atlas.settings
    # is imported by either helper.
    from backup import create_backup
    from recovery_drill import rehearse

    signatures = ROOT / ".local/clamav/database"
    updates = list(signatures.glob("daily.*"))
    marker = ROOT / ".local/clamav/last-update"
    checked_at = (
        marker.stat().st_mtime
        if marker.exists()
        else max((path.stat().st_mtime for path in updates), default=0)
    )
    if updates and time.time() - checked_at >= 12 * 3600:
        result = subprocess.run(
            [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/scanner.py"), "update"],
            cwd=ROOT,
            timeout=180,
        )
        if result.returncode:
            print(
                "Scanner signature refresh failed; upload scanning will refuse stale signatures.",
                flush=True,
            )
    backups = sorted(Path(".local/backups").glob("atlas-*/manifest.json"))
    now = datetime.now(UTC)
    latest = backups[-1] if backups else None
    last_at = (
        datetime.fromisoformat(json.loads(latest.read_text())["created_at"]) if latest else None
    )
    if last_at is None or (now - last_at).total_seconds() >= backup_hours * 3600:
        latest = create_backup() / "manifest.json"
        print("Scheduled snapshot backup completed.", flush=True)
    drills = list(Path(".local/recovery-drills").glob("*.json"))
    last_drill = max((path.stat().st_mtime for path in drills), default=0)
    if latest and now.timestamp() - last_drill >= restore_days * 86400:
        rehearse(latest.parent)
        print("Scheduled restore drill completed; recovery database retained.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--instance-root", type=Path, default=ROOT)
    args = parser.parse_args()
    instance = args.instance_root.resolve(strict=True)
    if not (instance / ".env").is_file():
        parser.error("The selected instance must have its own private .env")
    if instance != ROOT and not (instance / "migration.env").is_file():
        parser.error("A dedicated instance needs private operator configuration")
    if instance != ROOT:
        from public_demo import operator_environment

        os.environ.update(operator_environment(instance))
    else:
        for filename in ("migration.env", ".env", "worker.env"):
            path = instance / filename
            if path.is_file():
                os.environ.update({k: v for k, v in dotenv_values(path).items() if v is not None})
    os.chdir(instance)
    Path(".local").mkdir(exist_ok=True, mode=0o700)
    stop = False

    def shutdown(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    with Path(".local/operations-schedule.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("An Atlas operator scheduler is already running") from None
        while not stop:
            try:
                tick()
            except Exception as exc:
                # Exception type only: driver errors can contain credentials or source values.
                print("Scheduled recovery failed: " + type(exc).__name__, flush=True)
                if args.once:
                    raise SystemExit(1) from None
            if args.once:
                break
            for _ in range(300):
                if stop:
                    break
                time.sleep(1)


if __name__ == "__main__":
    main()
