"""Restore a backup to a fresh database and verify documents, access records and MFA keys."""

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from backup import record_run, table_evidence
from cryptography.fernet import Fernet
from dotenv import dotenv_values
from restore_backup import recovery_source_urls, verify_bundle
from verify_upgrade import target_url

from atlas.config import settings

CODE_ROOT = Path(__file__).resolve().parents[1]


def rehearse(backup: Path):
    source_urls = recovery_source_urls()
    manifest = verify_bundle(backup)
    if not manifest.get("tables"):
        raise ValueError("Create a new snapshot backup with table evidence before a restore drill")
    run_id = uuid4()
    target = "atlas_recovery_drill_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    tenants = manifest.get("tenant_ids", [])
    record_run(run_id, "restore", "running", tenants)
    try:
        subprocess.run(
            [
                sys.executable,
                str(CODE_ROOT / "scripts/restore_backup.py"),
                str(backup),
                "--database",
                target,
                "--skip-migrations",
            ],
            check=True,
        )
        target_admin = target_url(settings.database_admin_url, target)
        with psycopg.connect(target_admin) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            if table_evidence(conn) != manifest["tables"]:
                raise ValueError(
                    "Restored document, identity or permission evidence differs from the snapshot"
                )
            secret = dotenv_values(backup / "configuration.env").get("MFA_ENCRYPTION_KEY")
            encrypted = conn.execute(
                "SELECT mfa_secret FROM atlas.users WHERE mfa_secret IS NOT NULL"
            ).fetchall()
            if encrypted:
                if not secret:
                    raise ValueError("Backup lacks the matching MFA key")
                cipher = Fernet(secret.encode())
                for (value,) in encrypted:
                    cipher.decrypt(value.encode())
        urls = {key: target_url(url, target) for key, url in source_urls.items()}
        subprocess.run(
            [str(CODE_ROOT / ".venv/bin/alembic"), "upgrade", "head"],
            cwd=CODE_ROOT,
            env={**os.environ, **urls},
            check=True,
        )
        with psycopg.connect(urls["DATABASE_URL"]) as conn:
            role = conn.execute(
                "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            if not role or any(role):
                raise ValueError("Restored runtime role can bypass row security")
            if conn.execute("SELECT count(*) FROM atlas.documents").fetchone()[0] != 0:
                raise ValueError("Restored documents are visible without a tenant context")
        result = {
            "run_id": str(run_id),
            "database": target,
            "completed_at": datetime.now(UTC).isoformat(),
            "status": "completed",
            "verified_tables": len(manifest["tables"]),
            "verified_originals": manifest["uploads"],
            "verified_mfa_enrollments": len(encrypted),
            "permission_records_match": True,
            "unscoped_runtime_reads_blocked": True,
        }
        output = Path(".local/recovery-drills")
        output.mkdir(exist_ok=True, parents=True, mode=0o700)
        (output / (str(run_id) + ".json")).write_text(json.dumps(result, indent=2) + "\n")
        record_run(run_id, "restore", "completed", tenants, verified=True)
        return result
    except Exception as exc:
        record_run(run_id, "restore", "failed", tenants, error_code=type(exc).__name__)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    args = parser.parse_args()
    result = rehearse(args.backup)
    print(json.dumps(result, indent=2))
    print("Recovery database retained for inspection. No live database or queue was changed.")


if __name__ == "__main__":
    main()
