"""Create a private, consistent PostgreSQL snapshot and matching immutable upload bundle."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

import psycopg
from psycopg import sql

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


VERIFIED_TABLES = (
    "tenants",
    "users",
    "memberships",
    "teams",
    "team_members",
    "resource_grants",
    "service_accounts",
    "api_keys",
    "documents",
    "document_versions",
    "chunks",
    "embeddings",
)


def table_evidence(conn):
    evidence = {}
    for table in VERIFIED_TABLES:
        digest = hashlib.sha256()
        count = 0
        # Stable row order and JSON key order allow comparison after a physical restore.
        with conn.cursor(name="backup_" + table) as rows:
            rows.execute(
                sql.SQL(
                    "SELECT to_jsonb(t)::text FROM atlas.{} t ORDER BY to_jsonb(t)::text"
                ).format(sql.Identifier(table))
            )
            for (value,) in rows:
                digest.update(value.encode())
                digest.update(b"\n")
                count += 1
        evidence[table] = {"count": count, "sha256": digest.hexdigest()}
    return evidence


def record_run(run_id, kind, status, tenants, *, verified=False, error_code=None):
    with psycopg.connect(settings.database_admin_url) as conn:
        if not conn.execute("SELECT to_regclass('atlas.recovery_runs')").fetchone()[0]:
            return
        for tenant in tenants:
            conn.execute(
                """INSERT INTO atlas.recovery_runs(tenant_id,id,kind,status,completed_at,verified_at,error_code)
                SELECT id,%s,%s,%s,CASE WHEN %s!='running' THEN now() END,
                  CASE WHEN %s THEN now() END,%s FROM atlas.tenants WHERE id=%s
                ON CONFLICT(tenant_id,id) DO UPDATE SET status=excluded.status,
                  completed_at=excluded.completed_at,verified_at=excluded.verified_at,error_code=excluded.error_code""",
                (run_id, kind, status, status, verified, error_code, tenant),
            )


def create_backup():
    database = local_database(settings.database_admin_url)
    if not Path(".env").is_file():
        raise ValueError("The local .env configuration is required to preserve encryption keys")
    run_id = uuid4()
    destination = Path(".local/backups") / datetime.now(UTC).strftime("atlas-%Y%m%dT%H%M%S%f")
    destination.mkdir(parents=True, mode=0o700)
    archive = destination / "atlas.dump"
    tenants = []
    try:
        with psycopg.connect(settings.database_admin_url) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            snapshot = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]
            tenants = [str(row[0]) for row in conn.execute("SELECT id FROM atlas.tenants")]
            originals = conn.execute(
                "SELECT DISTINCT storage_key,byte_size FROM atlas.document_versions WHERE storage_key IS NOT NULL"
            ).fetchall()
            evidence = table_evidence(conn)
            with archive.open("xb") as output:
                archive.chmod(0o600)
                subprocess.run(
                    [
                        "docker",
                        "exec",
                        POSTGRES_CONTAINER,
                        "pg_dump",
                        "-U",
                        "atlas_admin",
                        "-Fc",
                        "--snapshot",
                        snapshot,
                        "-d",
                        database,
                    ],
                    stdout=output,
                    check=True,
                )
            upload_root = destination / "uploads"
            upload_root.mkdir(mode=0o700)
            upload_hashes = {}
            for storage_key, byte_size in originals:
                key = str(UUID(storage_key))
                source = Path(".local/uploads") / key
                if (
                    source.is_symlink()
                    or not source.is_file()
                    or source.stat().st_size != byte_size
                ):
                    raise ValueError("A snapshot original is missing or changed; retry the backup")
                target = upload_root / key
                shutil.copy2(source, target)
                target.chmod(0o600)
                upload_hashes[key] = file_hash(target)
        configuration = destination / "configuration.env"
        with configuration.open("x") as output:
            configuration.chmod(0o600)
            output.write(Path(".env").read_text().rstrip() + "\n")
            # Environment overrides must travel with this snapshot, not a different .env database.
            for name in (
                "database_url",
                "database_admin_url",
                "identity_database_url",
                "worker_database_url",
                "mfa_encryption_key",
                "redis_url",
                "cache_redis_url",
            ):
                output.write(f"{name.upper()}={json.dumps(getattr(settings, name))}\n")
        if not archive.stat().st_size:
            raise ValueError("Database backup is empty")
        manifest = {
            "format": 2,
            "run_id": str(run_id),
            "database": database,
            "database_bytes": archive.stat().st_size,
            "database_sha256": file_hash(archive),
            "uploads": len(upload_hashes),
            "upload_sha256": upload_hashes,
            "configuration": "configuration.env",
            "configuration_sha256": file_hash(configuration),
            "created_at": datetime.now(UTC).isoformat(),
            "tables": evidence,
            "tenant_ids": tenants,
            "consistency": "exported PostgreSQL snapshot and immutable originals",
        }
        (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (destination / "manifest.json").chmod(0o600)
        record_run(run_id, "backup", "completed", tenants)
        return destination
    except Exception as exc:
        record_run(run_id, "backup", "failed", tenants, error_code=type(exc).__name__)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        destination = create_backup()
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Private backup created: {destination}")


if __name__ == "__main__":
    main()
