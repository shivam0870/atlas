"""Restore into a new database only; never overwrite the running Atlas database."""

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from backup import POSTGRES_CONTAINER, file_hash, local_database
from psycopg import sql
from redis import Redis
from verify_upgrade import target_url

from atlas.config import settings

CODE_ROOT = Path(__file__).resolve().parents[1]


def recovery_source_urls():
    """Require all restored roles to originate from one explicit local instance."""
    roles = {
        "DATABASE_ADMIN_URL": "atlas_admin",
        "DATABASE_URL": "atlas_app",
        "IDENTITY_DATABASE_URL": "atlas_identity",
        "WORKER_DATABASE_URL": "atlas_worker",
    }
    urls = {name: getattr(settings, name.lower()) for name in roles}
    try:
        parsed = {name: urlsplit(url) for name, url in urls.items()}
        valid = len({target.path for target in parsed.values()}) == 1 and all(
            target.scheme in {"postgres", "postgresql"}
            and target.hostname in {"127.0.0.1", "localhost"}
            and target.port == 55432
            and target.username == roles[name]
            and re.fullmatch(r"/[a-zA-Z0-9_]{1,63}", target.path)
            and not target.query
            and not target.fragment
            for name, target in parsed.items()
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(
            "Recovery requires all four local database roles for the same source instance without URL overrides"
        )
    return urls


def verify_bundle(backup: Path):
    """Require complete integrity evidence and opaque upload names before touching a database."""
    manifest = json.loads((backup / "manifest.json").read_text())
    for name, key in (
        ("atlas.dump", "database_sha256"),
        ("configuration.env", "configuration_sha256"),
    ):
        path = backup / name
        expected = manifest.get(key)
        if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError("Backup is missing required integrity hashes")
        if path.is_symlink() or not path.is_file() or file_hash(path) != expected:
            raise ValueError("Backup integrity verification failed")
    uploads = backup / "uploads"
    expected_uploads = manifest.get("upload_sha256")
    if not isinstance(expected_uploads, dict) or any(
        not re.fullmatch(r"[a-f0-9-]{36}", name) for name in expected_uploads
    ):
        raise ValueError("Backup upload manifest is invalid")
    actual_uploads = {
        str(path.relative_to(uploads)) for path in uploads.rglob("*") if path.is_file()
    }
    if len(actual_uploads) != manifest.get("uploads") or actual_uploads != set(expected_uploads):
        raise ValueError("Backup upload inventory does not match")
    if uploads.is_symlink() or any(
        (uploads / name).is_symlink() or file_hash(uploads / name) != expected_uploads[name]
        for name in actual_uploads
    ):
        raise ValueError("Backup upload integrity verification failed")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("backup", type=Path)
    parser.add_argument("--database", required=True)
    parser.add_argument(
        "--skip-migrations",
        action="store_true",
        help="Operator restore drills compare the exact snapshot before migrating",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"atlas_recovery_[a-z0-9_]{1,40}", args.database):
        parser.error("Use a new database name beginning atlas_recovery_")
    archive = args.backup / "atlas.dump"
    if not archive.is_file() or not archive.stat().st_size:
        parser.error("Backup must contain a nonempty atlas.dump")
    configuration = args.backup / "configuration.env"
    manifest_file = args.backup / "manifest.json"
    if not configuration.is_file() or not manifest_file.is_file():
        parser.error(
            "Backup must include configuration.env and manifest.json to preserve encryption keys and files"
        )
    try:
        source_urls = recovery_source_urls()
        local_database(settings.database_admin_url)
        verify_bundle(args.backup)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    recovery_redis = {
        "REDIS_URL": target_url(settings.redis_url, "14"),
        "CACHE_REDIS_URL": target_url(settings.cache_redis_url, "14"),
    }
    for url in recovery_redis.values():
        with Redis.from_url(url, socket_timeout=5, socket_connect_timeout=5) as client:
            if client.dbsize():
                parser.error(
                    "Recovery Redis database 14 is already in use; preserve its data and choose a separate recovery environment"
                )
    with psycopg.connect(settings.database_admin_url, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(args.database))
        )
    with archive.open("rb") as data:
        subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                POSTGRES_CONTAINER,
                "pg_restore",
                "-U",
                "atlas_admin",
                "--exit-on-error",
                "-d",
                args.database,
            ],
            stdin=data,
            check=True,
        )
    urls = {name: target_url(url, args.database) for name, url in source_urls.items()}
    urls.update(recovery_redis)
    if not args.skip_migrations:
        subprocess.run(
            [str(CODE_ROOT / ".venv/bin/alembic"), "upgrade", "head"],
            cwd=CODE_ROOT,
            env={**os.environ, **urls},
            check=True,
        )
    private = Path(".local") / (args.database + ".env")
    descriptor, temporary = tempfile.mkstemp(prefix=".recovery-", dir=private.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write("\n".join(f"{name}={value}" for name, value in urls.items()) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, private)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print(f"Restored {args.database}. Private connection overrides: {private}.")
    print(
        "The running database is unchanged. Restore matching uploads and MFA_ENCRYPTION_KEY before switching the application."
    )
    print(
        "Recovery uses isolated Redis database 14 for queues and cache; live database 0 and test database 15 are unchanged."
    )


if __name__ == "__main__":
    main()
