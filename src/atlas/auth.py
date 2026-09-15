import hashlib
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, Request

from atlas.db import transaction


@dataclass(frozen=True)
class Identity:
    tenant_id: UUID
    key_id: UUID
    name: str
    scopes: list[str]


def digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def authenticate(request: Request) -> Identity:
    header = request.headers.get("authorization", "")
    key = header.removeprefix("Bearer ") if header.startswith("Bearer ") else ""
    if not key:
        key = request.cookies.get("atlas_session", "")
    if not key.startswith("atl_") or len(key) < 30 or len(key) > 200:
        raise HTTPException(
            401, "A valid workspace API key is required", headers={"WWW-Authenticate": "Bearer"}
        )
    async with transaction() as conn:
        row = await (
            await conn.execute("SELECT * FROM atlas.resolve_key(%s)", (digest(key),))
        ).fetchone()
    if not row:
        raise HTTPException(
            401,
            "This API key is invalid, expired, or revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )
    identity = Identity(row["tenant_id"], row["key_id"], row["name"], row["scopes"])
    request.state.identity = identity
    return identity


def require(identity: Identity, scope: str) -> None:
    if scope not in identity.scopes:
        raise HTTPException(403, "This key does not have the required permission")
