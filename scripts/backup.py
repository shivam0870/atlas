"""Create a private recovery bundle. Stop Atlas writers before taking a release backup."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from atlas.config import settings

POSTGRES_CONTAINER = "atlas-rag-postgres-1"


def local_database(url: str) -> str:
    """Bind Docker commands to the same configured loopback PostgreSQL endpoint."""
    try:
        parsed = urlsplit(url)
        database = unquote(parsed.path.removeprefix("/"))
        valid = (
            parsed.scheme in {"postgres", "postgresql"}
            and parsed.hostname in {"127.0.0.1", "localhost"}
            and parsed.port == 55432
            and parsed.username == "atlas_admin"
            and not parsed.query
            and not parsed.fragment
            and re.fullmatch(r"[a-zA-Z0-9_]{1,63}", database)
        )
    except ValueError as exc:
        raise ValueError("Invalid local PostgreSQL configuration") from exc
    if not valid:
        raise ValueError(
            "Recovery tools require atlas_admin on the local PostgreSQL port 55432, without URL query overrides"
        )
    ports = json.loads(
        subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .NetworkSettings.Ports}}",
                POSTGRES_CONTAINER,
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    if {"HostIp": "127.0.0.1", "HostPort": "55432"} not in (ports.get("5432/tcp") or []):
        raise ValueError(
            "The Atlas PostgreSQL container is not bound to the configured local endpoint"
        )
    return database


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        database = local_database(settings.database_admin_url)
    except ValueError as exc:
        parser.error(str(exc))
    if not Path(".env").is_file():
        parser.error("The local .env configuration is required to preserve account encryption keys")
    destination = Path(".local/backups") / datetime.now(UTC).strftime("atlas-%Y%m%dT%H%M%S")
    destination.mkdir(parents=True, mode=0o700)
    archive = destination / "atlas.dump"
    with archive.open("wb") as output:
        subprocess.run(
            [
                "docker",
                "exec",
                POSTGRES_CONTAINER,
                "pg_dump",
                "-U",
                "atlas_admin",
                "-Fc",
                "-d",
                database,
            ],
            stdout=output,
            check=True,
        )
    archive.chmod(0o600)
    if not archive.stat().st_size:
        raise RuntimeError("Database backup is empty")
    shutil.copy2(".env", destination / "configuration.env")
    (destination / "configuration.env").chmod(0o600)
    uploads = Path(".local/uploads")
    if uploads.exists():
        shutil.copytree(uploads, destination / "uploads")
    upload_hashes = {
        str(path.relative_to(destination / "uploads")): file_hash(path)
        for path in sorted((destination / "uploads").rglob("*"))
        if path.is_file()
    }
    manifest = {
        "database": database,
        "database_bytes": archive.stat().st_size,
        "database_sha256": file_hash(archive),
        "uploads": len(upload_hashes),
        "upload_sha256": upload_hashes,
        "configuration": "configuration.env",
        "configuration_sha256": file_hash(destination / "configuration.env"),
        "created_at": datetime.now(UTC).isoformat(),
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Private backup created: {destination}")


if __name__ == "__main__":
    main()
