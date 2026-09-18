"""Local administrative claim of a preserved, unclaimed workspace by a verified account.

This is deliberately a local CLI using DATABASE_ADMIN_URL. It is never exposed as an HTTP route.
"""

import argparse
import json
import sys

import psycopg
from psycopg.rows import dict_row

from atlas.accounts import normalize_email
from atlas.config import settings


def claim_workspace(workspace: str, email: str) -> dict:
    email = normalize_email(email)
    if not settings.database_admin_url:
        raise ValueError("DATABASE_ADMIN_URL must be configured locally")
    with psycopg.connect(settings.database_admin_url, row_factory=dict_row) as conn:
        role = conn.execute(
            "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        if not role or not (role["rolsuper"] or role["rolbypassrls"]):
            raise PermissionError("Workspace claims require the local database administrator")
        tenant = conn.execute(
            "SELECT id,name,slug,status,claimed_at FROM atlas.tenants WHERE slug=%s OR id::text=%s FOR UPDATE",
            (workspace, workspace),
        ).fetchone()
        if not tenant or tenant["status"] != "active":
            raise ValueError("An active workspace with this ID or slug was not found")
        user = conn.execute(
            "SELECT id FROM atlas.users WHERE email=%s AND email_verified_at IS NOT NULL AND disabled_at IS NULL FOR UPDATE",
            (email,),
        ).fetchone()
        if not user:
            raise ValueError("The target account must exist, have a verified email, and be active")
        owners = conn.execute(
            "SELECT user_id FROM atlas.memberships WHERE tenant_id=%s AND role='owner' AND status='active'",
            (tenant["id"],),
        ).fetchall()
        if tenant["claimed_at"]:
            if any(owner["user_id"] == user["id"] for owner in owners):
                return {
                    "status": "already_claimed",
                    "tenant_id": str(tenant["id"]),
                    "slug": tenant["slug"],
                    "owner_id": str(user["id"]),
                }
            raise ValueError(
                "This workspace is already claimed; use the authenticated ownership-transfer flow"
            )
        if any(owner["user_id"] != user["id"] for owner in owners):
            raise ValueError(
                "This workspace already has another owner; an initial claim cannot replace them"
            )
        conn.execute(
            "SELECT set_config('app.principal_id',%s,true),set_config('app.principal_kind','user',true)",
            (str(user["id"]),),
        )
        conn.execute(
            "INSERT INTO atlas.memberships(tenant_id,user_id,role,status) VALUES(%s,%s,'owner','active') ON CONFLICT(tenant_id,user_id) DO UPDATE SET role='owner',status='active'",
            (tenant["id"], user["id"]),
        )
        conn.execute(
            "UPDATE atlas.tenants SET claimed_at=now(),auth_revision=auth_revision+1 WHERE id=%s",
            (tenant["id"],),
        )
        revoked = conn.execute(
            "UPDATE atlas.api_keys k SET revoked_at=now() FROM atlas.service_accounts s WHERE k.tenant_id=%s AND s.tenant_id=k.tenant_id AND s.id=k.service_account_id AND s.legacy AND k.revoked_at IS NULL RETURNING k.id",
            (tenant["id"],),
        ).fetchall()
        conn.execute(
            "UPDATE atlas.service_accounts SET active=false WHERE tenant_id=%s AND legacy",
            (tenant["id"],),
        )
        conn.execute(
            "UPDATE atlas.spaces SET owner_user_id=coalesce(owner_user_id,%s),created_by=coalesce(created_by,%s) WHERE tenant_id=%s",
            (user["id"], user["id"], tenant["id"]),
        )
        conn.execute(
            "INSERT INTO atlas.audit_events(tenant_id,actor_id,actor_kind,action,resource_type,resource_id) VALUES(%s,%s,'user','workspace.claimed','tenant',%s)",
            (tenant["id"], user["id"], tenant["id"]),
        )
        return {
            "status": "claimed",
            "tenant_id": str(tenant["id"]),
            "slug": tenant["slug"],
            "owner_id": str(user["id"]),
            "revoked_legacy_keys": len(revoked),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True, help="Existing unclaimed workspace slug or UUID")
    parser.add_argument("--email", required=True, help="Email of the already-verified account")
    args = parser.parse_args()
    try:
        result = claim_workspace(args.tenant, args.email)
    except (ValueError, PermissionError) as exc:
        parser.error(str(exc))
    except psycopg.Error:
        print(
            "Workspace claim failed. Check the local administrator connection and migration status.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
