import argparse
import json
import os
import secrets
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from atlas.auth import digest
from atlas.config import settings


def create_tenant(name: str, slug: str):
    key = "atl_" + secrets.token_urlsafe(32)
    with psycopg.connect(settings.database_admin_url, row_factory=dict_row) as conn:
        row = conn.execute(
            "INSERT INTO atlas.tenants(id,name,slug) VALUES(%s,%s,%s) ON CONFLICT(slug) DO UPDATE SET name=excluded.name RETURNING id",
            (uuid4(), name, slug),
        ).fetchone()
        assert row is not None
        tenant_id = row["id"]
        conn.execute(
            "INSERT INTO atlas.api_keys(id,tenant_id,digest,prefix,label,scopes) VALUES(%s,%s,%s,%s,%s,%s)",
            (
                uuid4(),
                tenant_id,
                digest(key),
                key[:10],
                "Local console",
                ["read", "write", "query", "admin"],
            ),
        )
    settings.data_dir.mkdir(exist_ok=True, parents=True)
    path = settings.data_dir / "workspaces.json"
    current = json.loads(path.read_text()) if path.exists() else []
    current = [w for w in current if w["slug"] != slug]
    current.append({"id": str(tenant_id), "name": name, "slug": slug, "key": key})
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(current, out)
    print(f"Workspace created: {name}. Credential stored privately in .local/workspaces.json")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    tenant = sub.add_parser("tenant")
    tenant.add_argument("name")
    tenant.add_argument("slug")
    args = parser.parse_args()
    if args.command == "tenant":
        create_tenant(args.name, args.slug)


if __name__ == "__main__":
    main()
