"""Restore into a new database only; never overwrite the running Atlas database."""

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import psycopg
from backup import POSTGRES_CONTAINER, file_hash, local_database
from psycopg import sql
from redis import Redis
from verify_upgrade import target_url

from atlas.config import settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("backup", type=Path)
    parser.add_argument("--database", required=True)
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
        local_database(settings.database_admin_url)
        manifest = json.loads(manifest_file.read_text())
        for name, expected in [
            ("atlas.dump", manifest.get("database_sha256")),
            ("configuration.env", manifest.get("configuration_sha256")),
        ]:
            if expected and file_hash(args.backup / name) != expected:
                raise ValueError("Backup integrity verification failed")
        uploads = args.backup / "uploads"
        actual_uploads = {
            str(path.relative_to(uploads)) for path in uploads.rglob("*") if path.is_file()
        }
        expected_uploads = manifest.get("upload_sha256")
        if len(actual_uploads) != manifest.get("uploads", 0):
            raise ValueError("Backup upload count does not match its manifest")
        if expected_uploads is not None and (
            actual_uploads != set(expected_uploads)
            or any(file_hash(uploads / name) != expected_uploads[name] for name in actual_uploads)
        ):
            raise ValueError("Backup upload integrity verification failed")
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
    urls = {
        name: target_url(getattr(settings, name.lower()), args.database)
        for name in [
            "DATABASE_ADMIN_URL",
            "DATABASE_URL",
            "IDENTITY_DATABASE_URL",
            "WORKER_DATABASE_URL",
        ]
    }
    urls.update(recovery_redis)
    subprocess.run([".venv/bin/alembic", "upgrade", "head"], env={**os.environ, **urls}, check=True)
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
    print(f"Restored and migrated {args.database}. Private connection overrides: {private}.")
    print(
        "The running database is unchanged. Restore matching uploads and MFA_ENCRYPTION_KEY before switching the application."
    )
    print(
        "Recovery uses isolated Redis database 14 for queues and cache; live database 0 and test database 15 are unchanged."
    )


if __name__ == "__main__":
    main()
