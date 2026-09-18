import hashlib
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, Request

from atlas.db import bind_identity, identity_transaction, transaction

ROLE_SCOPES = {
    "owner": ["read", "write", "query", "admin"],
    "admin": ["read", "write", "query", "admin"],
    "editor": ["read", "write", "query"],
    "viewer": ["read", "query"],
}


@dataclass(frozen=True)
class Identity:
    tenant_id: UUID
    key_id: UUID
    name: str
    scopes: list[str]
    user_id: UUID | None = None
    role: str | None = None
    principal_kind: str = "service"
    principal_id: UUID | None = None
    auth_revision: int = 0
    session_id: UUID | None = None


def digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def authenticate(request: Request) -> Identity:
    header = request.headers.get("authorization", "")
    if header:
        key = header.removeprefix("Bearer ") if header.startswith("Bearer ") else ""
        if not key.startswith("atl_") or len(key) < 30 or len(key) > 200:
            raise HTTPException(
                401,
                "A valid integration API key is required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        async with transaction() as conn:
            row = await (
                await conn.execute("SELECT * FROM atlas.resolve_api_identity(%s)", (digest(key),))
            ).fetchone()
        if not row:
            raise HTTPException(
                401,
                "This API key is invalid, expired, or revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )
        identity = Identity(
            row["tenant_id"],
            row["key_id"],
            row["name"],
            row["scopes"],
            principal_id=row.get("service_account_id") or row["key_id"],
            auth_revision=row.get("auth_revision", 0),
        )
    else:
        from atlas.accounts import current_account, require_verified

        user = await current_account(request)
        require_verified(user)
        selected = request.headers.get("x-atlas-tenant", "")
        try:
            tenant_id = UUID(selected)
        except ValueError as exc:
            raise HTTPException(400, "Select a workspace using X-Atlas-Tenant") from exc
        async with identity_transaction() as conn:
            membership = await (
                await conn.execute(
                    "SELECT m.role,m.can_evaluate,t.name,t.auth_revision FROM atlas.memberships m JOIN atlas.tenants t ON t.id=m.tenant_id WHERE m.tenant_id=%s AND m.user_id=%s AND m.status='active' AND t.status='active'",
                    (tenant_id, user["id"]),
                )
            ).fetchone()
        if not membership:
            raise HTTPException(403, "You do not have access to this workspace")
        scopes = list(ROLE_SCOPES[membership["role"]])
        if membership["can_evaluate"]:
            scopes.append("evaluate")
        identity = Identity(
            tenant_id,
            user["session_id"],
            membership["name"],
            scopes,
            user_id=user["id"],
            role=membership["role"],
            principal_kind="user",
            principal_id=user["id"],
            auth_revision=membership["auth_revision"],
            session_id=user["session_id"],
        )
    request.state.identity = identity
    bind_identity(identity)
    return identity


def require(identity: Identity, scope: str) -> None:
    if scope not in identity.scopes:
        raise HTTPException(403, "You do not have the required permission")
