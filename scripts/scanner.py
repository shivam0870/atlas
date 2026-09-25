"""Manage Atlas's private native ClamAV scanner; never touch a system scanner."""

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

from local import identity

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / ".local/clamav"


def configure():
    DIRECTORY.mkdir(parents=True, exist_ok=True, mode=0o700)
    database = DIRECTORY / "database"
    database.mkdir(exist_ok=True, mode=0o700)
    configs = {
        "freshclam.conf": (
            f"DatabaseDirectory {database}\nDatabaseMirror database.clamav.net\nChecks 12\n"
            f"NotifyClamd {DIRECTORY / 'clamd.conf'}\n"
        ),
        "clamd.conf": (
            f"DatabaseDirectory {database}\nLogFile {DIRECTORY / 'clamd.log'}\n"
            "TCPSocket 53310\nTCPAddr 127.0.0.1\nForeground yes\n"
            "MaxThreads 2\nMaxQueue 8\nStreamMaxLength 21M\nMaxFileSize 21M\n"
            "MaxScanSize 64M\nMaxRecursion 12\nMaxFiles 2000\n"
            "AlertExceedsMax yes\nAlertEncrypted yes\nLogClean no\n"
        ),
    }
    for name, value in configs.items():
        path = DIRECTORY / name
        path.write_text(value)
        path.chmod(0o600)


def version():
    try:
        with socket.create_connection(("127.0.0.1", 53310), timeout=3) as stream:
            stream.sendall(b"zVERSION\0")
            return stream.recv(4096).decode().strip("\x00\n")
    except OSError:
        return "offline"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["configure", "update", "start", "stop", "status"])
    args = parser.parse_args()
    state_path = DIRECTORY / "process.json"
    if args.action in {"configure", "update", "start"}:
        configure()
    if args.action == "update":
        executable = shutil.which("freshclam")
        if not executable:
            parser.error("Install ClamAV first (brew install clamav on macOS)")
        subprocess.run([executable, f"--config-file={DIRECTORY / 'freshclam.conf'}"], check=True)
        (DIRECTORY / "last-update").write_text(str(time.time()))
    elif args.action == "start":
        if version() != "offline":
            print("Scanner is already listening on Atlas's loopback port.")
            return
        executable = shutil.which("clamd") or "/opt/homebrew/opt/clamav/sbin/clamd"
        if not Path(executable).is_file():
            parser.error("Install ClamAV before starting the scanner")
        if not any((DIRECTORY / "database").glob("daily.*")):
            parser.error("Download signatures with python3 scripts/scanner.py update first")
        with (DIRECTORY / "process.log").open("ab") as log:
            process = subprocess.Popen(
                [executable, f"--config-file={DIRECTORY / 'clamd.conf'}", "--foreground"],
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        time.sleep(0.3)
        state_path.write_text(json.dumps({"pid": process.pid, "identity": identity(process.pid)}))
        state_path.chmod(0o600)
        print("Scanner started; signature loading may take a minute. Use the status command.")
    elif args.action == "stop" and state_path.exists():
        process = json.loads(state_path.read_text())
        if identity(process["pid"]) == process["identity"]:
            os.killpg(process["pid"], signal.SIGTERM)
        state_path.unlink()
    if args.action == "status":
        print(version())


if __name__ == "__main__":
    main()
