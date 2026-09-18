"""Restore the private snapshot into a new database, migrate it, and verify preservation."""

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql

from atlas.config import settings


def target_url(url, database):
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path="/" + database))


def main():
    if "--fresh" in sys.argv:
        return fresh_install()
    backup = Path(Path(".local/latest-upgrade-backup").read_text().strip())
    if backup.stat().st_size == 0:
        raise RuntimeError("Backup is empty")
    database = "atlas_upgrade_verify_" + uuid4().hex[:8]
    with psycopg.connect(settings.database_admin_url, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database))
        )
    with backup.open("rb") as data:
        subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                "atlas-rag-postgres-1",
                "pg_restore",
                "-U",
                "atlas_admin",
                "--exit-on-error",
                "-d",
                database,
            ],
            stdin=data,
            check=True,
        )
    urls = {
        name: target_url(getattr(settings, name.lower()), database)
        for name in [
            "DATABASE_ADMIN_URL",
            "DATABASE_URL",
            "IDENTITY_DATABASE_URL",
            "WORKER_DATABASE_URL",
        ]
    }
    urls["REDIS_URL"] = target_url(settings.redis_url, "15")
    urls["CACHE_REDIS_URL"] = target_url(settings.cache_redis_url, "15")
    with psycopg.connect(urls["DATABASE_ADMIN_URL"]) as conn:
        before = {
            table: conn.execute(
                sql.SQL("SELECT count(*) FROM atlas.{}").format(sql.Identifier(table))
            ).fetchone()[0]
            for table in ["tenants", "documents", "chunks", "embeddings", "queries"]
        }
    config = Path(".local/upgrade-test.env")
    config.write_text("\n".join(f"{key}={value}" for key, value in urls.items()) + "\n")
    config.chmod(0o600)
    Path(".local/upgrade-db.json").write_text(
        json.dumps({"database": database, "backup": str(backup)})
    )
    subprocess.run([".venv/bin/alembic", "upgrade", "head"], env={**os.environ, **urls}, check=True)
    with psycopg.connect(urls["DATABASE_ADMIN_URL"]) as conn:
        after = {
            table: conn.execute(
                sql.SQL("SELECT count(*) FROM atlas.{}").format(sql.Identifier(table))
            ).fetchone()[0]
            for table in before
        }
        missing = conn.execute(
            "SELECT count(*) FROM atlas.chunks WHERE version_id IS NULL"
        ).fetchone()[0]
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    assert before == after, (before, after)
    assert missing == 0
    result = {
        "target_database": database,
        "source_database_modified": False,
        "backup_bytes": backup.stat().st_size,
        "before": before,
        "after": after,
        "unversioned_chunks": missing,
        "migration": version,
    }
    Path("artifacts/upgrade").mkdir(parents=True, exist_ok=True)
    Path("artifacts/upgrade/migration.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


def fresh_install():
    database = "atlas_upgrade_verify_fresh_" + uuid4().hex[:8]
    with psycopg.connect(settings.database_admin_url, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database))
        )
    urls = {
        name: target_url(getattr(settings, name.lower()), database)
        for name in [
            "DATABASE_ADMIN_URL",
            "DATABASE_URL",
            "IDENTITY_DATABASE_URL",
            "WORKER_DATABASE_URL",
        ]
    }
    subprocess.run([".venv/bin/alembic", "upgrade", "head"], env={**os.environ, **urls}, check=True)
    with psycopg.connect(urls["DATABASE_ADMIN_URL"]) as conn:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        count = conn.execute("SELECT count(*) FROM atlas.tenants").fetchone()[0]
        tables = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='atlas'"
        ).fetchone()[0]
    assert count == 0 and tables >= 35
    result = {
        "target_database": database,
        "migration": version,
        "tenants": count,
        "tables": tables,
        "source_database_modified": False,
    }
    Path("artifacts/upgrade").mkdir(parents=True, exist_ok=True)
    Path("artifacts/upgrade/fresh-install.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
