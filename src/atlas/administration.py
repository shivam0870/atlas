"""Authorized inventory, integration credentials, and metadata-only company administration."""

import asyncio
import csv
import io
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, ValidationError, field_validator

from atlas.accounts import nonempty_name
from atlas.auth import Identity, authenticate, digest, require
from atlas.config import settings
from atlas.db import transaction
from atlas.serving import cache_redis, redis

router = APIRouter(prefix="/api", tags=["administration"])


def manager(identity: Identity):
    if identity.principal_kind != "user" or identity.role not in {"owner", "admin"}:
        raise HTTPException(403, "A company administrator account is required")


def evaluator(identity: Identity):
    if identity.principal_kind != "user" or not (
        identity.role in {"owner", "admin"} or "evaluate" in identity.scopes
    ):
        raise HTTPException(403, "Evaluation permission is required")


async def audit(conn, identity, action, kind, resource_id=None, metadata=None):
    if identity.principal_kind != "user" or identity.role not in {"owner", "admin"}:
        return  # Database mutation triggers record non-administrator actions.
    await conn.execute(
        "INSERT INTO atlas.audit_events(tenant_id,id,actor_id,actor_kind,action,resource_type,resource_id,metadata) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            identity.tenant_id,
            uuid4(),
            identity.principal_id,
            identity.principal_kind,
            action,
            kind,
            resource_id,
            Jsonb(metadata or {}),
        ),
    )


class InventoryBody(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    kind: Literal["service", "deployment"] = "service"
    space_id: UUID
    attributes: dict[str, Any] = Field(default_factory=dict)
    document_ids: list[UUID] = Field(default_factory=list, max_length=30)
    _name = field_validator("name")(nonempty_name)

    @field_validator("attributes")
    @classmethod
    def attributes_valid(cls, attrs):
        if len(json.dumps(attrs)) > 16000:
            raise ValueError("Attributes exceed 16 KB")
        for field in ["owner", "environment", "status", "description"]:
            if field in attrs and (not isinstance(attrs[field], str) or len(attrs[field]) > 2000):
                raise ValueError(f"{field} must be text no longer than 2000 characters")
        if attrs.get("status") not in {None, "", "active", "degraded", "maintenance", "retired"}:
            raise ValueError("status must be active, degraded, maintenance, or retired")
        if "replicas" in attrs and (
            type(attrs["replicas"]) is not int or not 0 <= attrs["replicas"] <= 100000
        ):
            raise ValueError("replicas must be an integer from 0 to 100000")
        return attrs


class InventoryEdit(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    space_id: UUID | None = None
    attributes: dict[str, Any] | None = None
    document_ids: list[UUID] | None = Field(default=None, max_length=30)
    archived: bool | None = None
    _name = field_validator("name")(nonempty_name)


async def validate_inventory(conn, body: InventoryBody):
    allowed = await (
        await conn.execute(
            "SELECT atlas.can_space(id,true) AS allowed FROM atlas.spaces WHERE id=%s",
            (body.space_id,),
        )
    ).fetchone()
    if not allowed or not allowed["allowed"]:
        raise HTTPException(403, "You cannot edit this space")
    if body.document_ids:
        docs = await (
            await conn.execute(
                "SELECT id FROM atlas.documents WHERE id=ANY(%s) AND lifecycle='active'",
                (body.document_ids,),
            )
        ).fetchall()
        if len({r["id"] for r in docs}) != len(set(body.document_ids)):
            raise HTTPException(403, "One or more supporting documents are inaccessible")


@router.get("/inventory")
async def inventory(
    q: str = "",
    include_archived: bool = False,
    space_id: UUID | None = None,
    page_size: int = Query(50, ge=1, le=100),
    page: int = Query(1, ge=1),
    identity: Identity = Depends(authenticate),
):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT *,count(*) OVER() AS total FROM atlas.entities WHERE tenant_id=%s AND (%s OR NOT archived) AND name ILIKE %s AND (%s::uuid IS NULL OR space_id=%s) ORDER BY name,id LIMIT %s OFFSET %s",
                (
                    identity.tenant_id,
                    include_archived,
                    f"%{q[:200]}%",
                    space_id,
                    space_id,
                    page_size,
                    (page - 1) * page_size,
                ),
            )
        ).fetchall()
    return {"items": rows, "total": rows[0]["total"] if rows else 0}


@router.post("/inventory", status_code=201)
async def create_inventory(body: InventoryBody, identity: Identity = Depends(authenticate)):
    require(identity, "write")
    async with transaction(identity.tenant_id) as conn:
        await validate_inventory(conn, body)
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"inventory:{identity.tenant_id}:{body.kind}:{body.name}",),
        )
        exists = await (
            await conn.execute(
                "SELECT id FROM atlas.entities WHERE tenant_id=%s AND name=%s AND kind=%s",
                (identity.tenant_id, body.name, body.kind),
            )
        ).fetchone()
        if exists:
            raise HTTPException(409, "An inventory record with this name and kind already exists")
        row = await (
            await conn.execute(
                "INSERT INTO atlas.entities(tenant_id,id,name,kind,space_id,attributes,document_ids) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,name,kind) DO NOTHING RETURNING *",
                (
                    identity.tenant_id,
                    uuid4(),
                    body.name,
                    body.kind,
                    body.space_id,
                    Jsonb(body.attributes),
                    body.document_ids,
                ),
            )
        ).fetchone()
        if not row:
            raise HTTPException(409, "An inventory record with this name and kind already exists")
        await audit(conn, identity, "inventory.created", "entity", row["id"])
    return {"item": row}


class ImportBody(BaseModel):
    csv: str = Field(min_length=1, max_length=1_000_000)
    space_id: UUID


def parse_inventory_csv(body: ImportBody):
    try:
        reader = csv.DictReader(io.StringIO(body.csv.lstrip("\ufeff")))
        if not reader.fieldnames or "name" not in reader.fieldnames:
            raise HTTPException(422, "CSV must contain a name column")
        allowed_columns = {
            "name",
            "kind",
            "attributes",
            "document_ids",
            "owner",
            "environment",
            "status",
            "description",
            "replicas",
        }
        if (
            len(reader.fieldnames) != len(set(reader.fieldnames))
            or set(reader.fieldnames) - allowed_columns
        ):
            raise HTTPException(422, "CSV contains duplicate or unsupported columns")
        rows: list[dict[str, Any]] = []
        for number, raw in enumerate(reader, start=2):
            if number > 501:
                raise HTTPException(422, "Import at most 500 records at a time")
            try:
                if None in raw:
                    raise ValueError("Row contains more fields than the header")
                attrs = json.loads(raw.get("attributes", "") or "{}")
                if not isinstance(attrs, dict):
                    raise ValueError("attributes must be a JSON object")
                for field in ["owner", "environment", "status", "description"]:
                    if raw.get(field):
                        attrs[field] = raw[field]
                if raw.get("replicas"):
                    attrs["replicas"] = int(raw["replicas"])
                record = InventoryBody(
                    name=raw.get("name", ""),
                    kind=cast(Literal["service", "deployment"], raw.get("kind", "") or "service"),
                    space_id=body.space_id,
                    attributes=attrs,
                    document_ids=[
                        UUID(value.strip())
                        for value in (raw.get("document_ids", "") or "").split(";")
                        if value.strip()
                    ],
                )
                rows.append(
                    {
                        "row": number,
                        "valid": True,
                        "data": record.model_dump(mode="json"),
                        "errors": [],
                    }
                )
            except (ValueError, TypeError, ValidationError) as exc:
                rows.append(
                    {
                        "row": number,
                        "valid": False,
                        "data": {"name": raw.get("name", "")},
                        "errors": [str(exc)],
                    }
                )
        if not rows:
            raise HTTPException(422, "CSV contains no records")
        seen = set()
        for row in rows:
            if row["valid"]:
                key = (row["data"]["name"], row["data"]["kind"])
                if key in seen:
                    row["valid"] = False
                    row["errors"] = ["Duplicate name and kind within this CSV"]
                seen.add(key)
        return rows
    except csv.Error as exc:
        raise HTTPException(422, "Malformed CSV") from exc


async def import_preview(conn, body):
    rows = parse_inventory_csv(body)
    for row in rows:
        if row["valid"]:
            record = InventoryBody.model_validate(row["data"])
            try:
                await validate_inventory(conn, record)
                exists = await (
                    await conn.execute(
                        "SELECT id FROM atlas.entities WHERE tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid AND name=%s AND kind=%s",
                        (record.name, record.kind),
                    )
                ).fetchone()
                if exists:
                    raise HTTPException(409, "A record with this name and kind already exists")
            except HTTPException as exc:
                row["valid"], row["errors"] = False, [str(exc.detail)]
    return rows


@router.post("/inventory/import/preview")
async def preview_import(body: ImportBody, identity: Identity = Depends(authenticate)):
    require(identity, "write")
    async with transaction(identity.tenant_id) as conn:
        rows = await import_preview(conn, body)
    return {"rows": rows, "valid_count": sum(bool(row["valid"]) for row in rows)}


@router.post("/inventory/import")
async def commit_import(body: ImportBody, identity: Identity = Depends(authenticate)):
    require(identity, "write")
    async with transaction(identity.tenant_id) as conn:
        await conn.execute(
            "SELECT id FROM atlas.tenants WHERE id=%s FOR UPDATE", (identity.tenant_id,)
        )
        rows = await import_preview(conn, body)
        if not all(row["valid"] for row in rows):
            raise HTTPException(
                422, "Import contains invalid records. Preview and correct every row first."
            )
        for row in rows:
            record = InventoryBody.model_validate(row["data"])
            entity_id = uuid4()
            await conn.execute(
                "INSERT INTO atlas.entities(tenant_id,id,name,kind,space_id,attributes,document_ids) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (
                    identity.tenant_id,
                    entity_id,
                    record.name,
                    record.kind,
                    record.space_id,
                    Jsonb(record.attributes),
                    record.document_ids,
                ),
            )
            await audit(conn, identity, "inventory.imported", "entity", entity_id)
    return {"imported": len(rows)}


class CompareBody(BaseModel):
    ids: list[UUID] = Field(min_length=2, max_length=6)


@router.post("/inventory/compare")
async def compare_inventory(body: CompareBody, identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT * FROM atlas.entities WHERE tenant_id=%s AND id=ANY(%s) AND NOT archived ORDER BY name",
                (identity.tenant_id, body.ids),
            )
        ).fetchall()
        if len(rows) != len(set(body.ids)):
            raise HTTPException(404, "One or more inventory records are unavailable")
        for row in rows:
            row["documents"] = await (
                await conn.execute(
                    "SELECT id,title,current_version_id FROM atlas.documents WHERE id=ANY(%s) AND lifecycle='active'",
                    (row["document_ids"],),
                )
            ).fetchall()
    return {"items": rows, "fields": sorted({key for row in rows for key in row["attributes"]})}


@router.get("/inventory/{entity_id}")
async def inventory_detail(entity_id: UUID, identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "SELECT * FROM atlas.entities WHERE tenant_id=%s AND id=%s",
                (identity.tenant_id, entity_id),
            )
        ).fetchone()
        if not row:
            raise HTTPException(404, "Inventory record not found")
        row["documents"] = await (
            await conn.execute(
                "SELECT id,title,current_version_id FROM atlas.documents WHERE id=ANY(%s) AND lifecycle='active'",
                (row["document_ids"],),
            )
        ).fetchall()
    return {"item": row}


@router.patch("/inventory/{entity_id}")
async def edit_inventory(
    entity_id: UUID, body: InventoryEdit, identity: Identity = Depends(authenticate)
):
    require(identity, "write")
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "SELECT * FROM atlas.entities WHERE tenant_id=%s AND id=%s AND atlas.can_entity(id,true) FOR UPDATE",
                (identity.tenant_id, entity_id),
            )
        ).fetchone()
        if not row:
            raise HTTPException(404, "Editable inventory record not found")
        data = {key: row[key] for key in ["name", "kind", "space_id", "attributes", "document_ids"]}
        data.update(body.model_dump(exclude_none=True, exclude={"archived"}))
        try:
            record = InventoryBody.model_validate(data)
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        await validate_inventory(conn, record)
        exists = await (
            await conn.execute(
                "SELECT id FROM atlas.entities WHERE tenant_id=%s AND name=%s AND kind=%s AND id<>%s",
                (identity.tenant_id, record.name, record.kind, entity_id),
            )
        ).fetchone()
        if exists:
            raise HTTPException(409, "An inventory record with this name and kind already exists")
        updated = await (
            await conn.execute(
                "UPDATE atlas.entities SET name=%s,space_id=%s,attributes=%s,document_ids=%s,archived=coalesce(%s,archived),revision=revision+1,updated_at=now() WHERE tenant_id=%s AND id=%s RETURNING *",
                (
                    record.name,
                    record.space_id,
                    Jsonb(record.attributes),
                    record.document_ids,
                    body.archived,
                    identity.tenant_id,
                    entity_id,
                ),
            )
        ).fetchone()
        await audit(conn, identity, "inventory.updated", "entity", entity_id)
    return {"item": updated}


@router.delete("/inventory/{entity_id}")
async def archive_inventory(entity_id: UUID, identity: Identity = Depends(authenticate)):
    return await edit_inventory(entity_id, InventoryEdit(archived=True), identity)


class ServiceGrant(BaseModel):
    space_id: UUID
    permission: Literal["read", "write"] = "read"


class KeyBody(BaseModel):
    scopes: list[Literal["read", "query", "write"]] = Field(
        default_factory=lambda: cast(list[Literal["read", "query", "write"]], ["read", "query"]),
        min_length=1,
    )
    expires_in_days: int = Field(default=30, ge=1, le=365)


class IntegrationCreate(KeyBody):
    name: str = Field(min_length=1, max_length=120)
    grants: list[ServiceGrant] = Field(min_length=1, max_length=100)
    _name = field_validator("name")(nonempty_name)


class IntegrationEdit(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    active: bool | None = None
    grants: list[ServiceGrant] | None = Field(default=None, max_length=100)
    _name = field_validator("name")(nonempty_name)


async def write_service_grants(conn, tenant, service_id, grants):
    if len({g.space_id for g in grants}) != len(grants):
        raise HTTPException(422, "Specify each space once")
    for grant in grants:
        space = await (
            await conn.execute(
                "SELECT id FROM atlas.spaces WHERE tenant_id=%s AND id=%s", (tenant, grant.space_id)
            )
        ).fetchone()
        if not space:
            raise HTTPException(404, "Granted space not found")
    await conn.execute(
        "DELETE FROM atlas.resource_grants WHERE tenant_id=%s AND subject_type='service' AND subject_id=%s",
        (tenant, service_id),
    )
    for grant in grants:
        await conn.execute(
            "INSERT INTO atlas.resource_grants(tenant_id,id,space_id,subject_type,subject_id,permission) VALUES(%s,%s,%s,'service',%s,%s)",
            (tenant, uuid4(), grant.space_id, service_id, grant.permission),
        )


async def issue_key(conn, identity, service, body):
    raw = "atl_" + secrets.token_urlsafe(32)
    key_id = uuid4()
    await conn.execute(
        "INSERT INTO atlas.api_keys(id,tenant_id,digest,prefix,label,scopes,expires_at,service_account_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            key_id,
            identity.tenant_id,
            digest(raw),
            raw[:10],
            service["name"],
            sorted(set(body.scopes)),
            datetime.now(UTC) + timedelta(days=body.expires_in_days),
            service["id"],
        ),
    )
    await audit(
        conn,
        identity,
        "integration.key_issued",
        "api_key",
        key_id,
        {"scopes": sorted(set(body.scopes)), "expires_in_days": body.expires_in_days},
    )
    return raw


@router.get("/integrations")
async def integrations(identity: Identity = Depends(authenticate)):
    manager(identity)
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT id,name,active,legacy,created_at FROM atlas.service_accounts WHERE tenant_id=%s ORDER BY created_at DESC",
                (identity.tenant_id,),
            )
        ).fetchall()
        for row in rows:
            row["keys"] = await (
                await conn.execute(
                    "SELECT id,prefix,label,scopes,created_at,expires_at,revoked_at,last_used_at FROM atlas.api_keys WHERE tenant_id=%s AND service_account_id=%s ORDER BY created_at DESC",
                    (identity.tenant_id, row["id"]),
                )
            ).fetchall()
            row["grants"] = await (
                await conn.execute(
                    "SELECT space_id,document_id,permission FROM atlas.resource_grants WHERE tenant_id=%s AND subject_type='service' AND subject_id=%s",
                    (identity.tenant_id, row["id"]),
                )
            ).fetchall()
    return {"items": rows}


@router.post("/integrations", status_code=201)
async def create_integration(body: IntegrationCreate, identity: Identity = Depends(authenticate)):
    manager(identity)
    async with transaction(identity.tenant_id) as conn:
        service = await (
            await conn.execute(
                "INSERT INTO atlas.service_accounts(tenant_id,id,name) VALUES(%s,%s,%s) RETURNING id,name,active,created_at",
                (identity.tenant_id, uuid4(), body.name),
            )
        ).fetchone()
        assert service is not None
        await write_service_grants(conn, identity.tenant_id, service["id"], body.grants)
        raw = await issue_key(conn, identity, service, body)
    return {"integration": service, "key": raw}


@router.patch("/integrations/{service_id}")
async def edit_integration(
    service_id: UUID, body: IntegrationEdit, identity: Identity = Depends(authenticate)
):
    manager(identity)
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "UPDATE atlas.service_accounts SET name=coalesce(%s,name),active=coalesce(%s,active) WHERE tenant_id=%s AND id=%s RETURNING id,name,active,legacy,created_at",
                (body.name, body.active, identity.tenant_id, service_id),
            )
        ).fetchone()
        if not row:
            raise HTTPException(404, "Integration not found")
        if body.grants is not None:
            await write_service_grants(conn, identity.tenant_id, service_id, body.grants)
        await audit(conn, identity, "integration.updated", "service_account", service_id)
    return {"integration": row}


@router.post("/integrations/{service_id}/rotate")
async def rotate_integration(
    service_id: UUID, body: KeyBody, identity: Identity = Depends(authenticate)
):
    manager(identity)
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "SELECT id,name,active,legacy FROM atlas.service_accounts WHERE tenant_id=%s AND id=%s FOR UPDATE",
                (identity.tenant_id, service_id),
            )
        ).fetchone()
        if not row or not row["active"] or row["legacy"]:
            raise HTTPException(409, "Create an active scoped integration before issuing a key")
        await conn.execute(
            "UPDATE atlas.api_keys SET revoked_at=now() WHERE tenant_id=%s AND service_account_id=%s AND revoked_at IS NULL",
            (identity.tenant_id, service_id),
        )
        raw = await issue_key(conn, identity, row, body)
    return {"key": raw}


@router.delete("/integrations/{service_id}/keys/{key_id}")
async def revoke_integration_key(
    service_id: UUID, key_id: UUID, identity: Identity = Depends(authenticate)
):
    manager(identity)
    async with transaction(identity.tenant_id) as conn:
        await conn.execute(
            "UPDATE atlas.api_keys SET revoked_at=now() WHERE tenant_id=%s AND service_account_id=%s AND id=%s",
            (identity.tenant_id, service_id, key_id),
        )
        await audit(conn, identity, "integration.key_revoked", "api_key", key_id)
    return {"message": "Key revoked"}


class LimitsBody(BaseModel):
    requests_per_minute: int = Field(ge=1, le=10000)
    monthly_tokens: int = Field(ge=0, le=1_000_000_000)
    storage_bytes: int | None = Field(default=None, ge=0, le=10_000_000_000_000)
    uploads_per_day: int | None = Field(default=None, ge=0, le=100000)
    upload_bytes_per_day: int | None = Field(default=None, ge=0, le=1_000_000_000_000)
    max_pending_jobs: int | None = Field(default=None, ge=1, le=10000)
    concurrent_generations: int | None = Field(default=None, ge=1, le=32)
    queued_generations: int | None = Field(default=None, ge=1, le=1000)
    queries_per_day: int | None = Field(default=None, ge=0, le=1000000)


class MemberLimit(BaseModel):
    monthly_tokens: int | None = Field(default=None, ge=0, le=1_000_000_000)
    can_evaluate: bool | None = None


@router.get("/administration/limits")
async def get_limits(identity: Identity = Depends(authenticate)):
    manager(identity)
    async with transaction(identity.tenant_id) as conn:
        quota = await (
            await conn.execute(
                "INSERT INTO atlas.tenant_limits(tenant_id) VALUES(%s) ON CONFLICT(tenant_id) DO UPDATE SET tenant_id=excluded.tenant_id RETURNING *",
                (identity.tenant_id,),
            )
        ).fetchone()
        members = await (
            await conn.execute(
                "SELECT m.user_id,u.name,u.email,m.monthly_tokens,m.can_evaluate FROM atlas.memberships m JOIN atlas.users u ON u.id=m.user_id WHERE m.tenant_id=%s AND m.status='active' ORDER BY u.name",
                (identity.tenant_id,),
            )
        ).fetchall()
    return {"limits": quota, "members": members}


@router.patch("/administration/limits")
async def update_limits(body: LimitsBody, identity: Identity = Depends(authenticate)):
    manager(identity)
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "INSERT INTO atlas.tenant_limits(tenant_id,requests_per_minute,monthly_tokens,monthly_usd) VALUES(%s,%s,%s,0) ON CONFLICT(tenant_id) DO UPDATE SET requests_per_minute=excluded.requests_per_minute,monthly_tokens=excluded.monthly_tokens,monthly_usd=0 RETURNING *",
                (identity.tenant_id, body.requests_per_minute, body.monthly_tokens),
            )
        ).fetchone()
        extended = body.model_dump(
            exclude_none=True, exclude={"requests_per_minute", "monthly_tokens"}
        )
        if extended:
            # Keys come exclusively from the validated model, never client-provided SQL.
            assignments = ",".join(f"{key}=%s" for key in extended)
            row = await (
                await conn.execute(
                    f"UPDATE atlas.tenant_limits SET {assignments} WHERE tenant_id=%s RETURNING *",
                    (*extended.values(), identity.tenant_id),
                )
            ).fetchone()
        await audit(
            conn, identity, "quota.updated", "tenant", identity.tenant_id, body.model_dump()
        )
    return {"limits": row}


@router.put("/administration/limits/members/{user_id}")
async def update_member_limit(
    user_id: UUID, body: MemberLimit, identity: Identity = Depends(authenticate)
):
    manager(identity)
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "UPDATE atlas.memberships SET monthly_tokens=%s,can_evaluate=coalesce(%s,can_evaluate) WHERE tenant_id=%s AND user_id=%s RETURNING user_id,monthly_tokens,can_evaluate",
                (body.monthly_tokens, body.can_evaluate, identity.tenant_id, user_id),
            )
        ).fetchone()
        if not row:
            raise HTTPException(404, "Member not found")
        await audit(
            conn,
            identity,
            "member.policy_updated",
            "membership",
            user_id,
            body.model_dump(exclude_none=True),
        )
    return {"member": row}


@router.get("/administration/audit")
async def audit_events(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: Identity = Depends(authenticate),
):
    manager(identity)
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT *,count(*) OVER() AS total FROM atlas.audit_events WHERE tenant_id=%s ORDER BY created_at DESC,id LIMIT %s OFFSET %s",
                (identity.tenant_id, limit, offset),
            )
        ).fetchall()
    return {"items": rows, "total": rows[0]["total"] if rows else 0}


@router.get("/administration/usage")
async def usage(days: int = Query(30, ge=1, le=90), identity: Identity = Depends(authenticate)):
    manager(identity)
    async with transaction(identity.tenant_id) as conn:
        summary = await (
            await conn.execute(
                "SELECT count(*) AS requests,count(*) FILTER(WHERE cached) AS cache_hits,coalesce(sum(input_tokens),0) AS input_tokens,coalesce(sum(output_tokens),0) AS output_tokens,coalesce(sum(actual_api_cost_usd),0) AS actual_api_cost_usd,coalesce(avg(duration_ms),0) AS average_duration_ms,coalesce(percentile_cont(.95) WITHIN GROUP(ORDER BY duration_ms),0) AS p95_duration_ms,count(*) FILTER(WHERE status NOT IN ('completed','ok','cached')) AS failures FROM atlas.usage_ledger WHERE tenant_id=%s AND created_at>=now()-(%s * interval '1 day')",
                (identity.tenant_id, days),
            )
        ).fetchone()
        daily = await (
            await conn.execute(
                "SELECT created_at::date AS day,count(*) AS requests,coalesce(sum(input_tokens),0) AS input_tokens,coalesce(sum(output_tokens),0) AS output_tokens,coalesce(avg(duration_ms),0) AS average_duration_ms,count(*) FILTER(WHERE status NOT IN ('completed','ok','cached')) AS failures FROM atlas.usage_ledger WHERE tenant_id=%s AND created_at>=now()-(%s * interval '1 day') GROUP BY day ORDER BY day",
                (identity.tenant_id, days),
            )
        ).fetchall()
        members = await (
            await conn.execute(
                "SELECT l.user_id,u.name,count(*) AS requests,coalesce(sum(l.input_tokens),0) AS input_tokens,coalesce(sum(l.output_tokens),0) AS output_tokens FROM atlas.usage_ledger l LEFT JOIN atlas.users u ON u.id=l.user_id WHERE l.tenant_id=%s AND l.created_at>=now()-(%s * interval '1 day') GROUP BY l.user_id,u.name ORDER BY requests DESC",
                (identity.tenant_id, days),
            )
        ).fetchall()
        indexing = await (
            await conn.execute(
                "SELECT count(*) FILTER(WHERE status='completed') AS indexed_documents,coalesce(avg(extract(epoch FROM updated_at-created_at)*1000) FILTER(WHERE status='completed'),0) AS average_indexing_ms FROM atlas.ingestion_jobs WHERE tenant_id=%s AND created_at>=now()-(%s * interval '1 day')",
                (identity.tenant_id, days),
            )
        ).fetchone()
        assert summary is not None and indexing is not None
        summary.update(indexing)
    return {
        "summary": summary,
        "daily": daily,
        "members": members,
        "window_days": days,
        "indexing_duration_basis": "accepted-to-completed, including queue time",
    }


@router.get("/administration/system")
async def system(identity: Identity = Depends(authenticate)):
    manager(identity)
    checks: dict[str, Any] = {"paid_apis": False}
    try:
        async with transaction(identity.tenant_id) as conn:
            await conn.execute("SELECT 1")
        checks["database"] = {"ready": True}
    except Exception:
        checks["database"] = {"ready": False}
    for name, client in [("redis", redis), ("cache", cache_redis)]:
        try:
            checks[name] = {"ready": bool(await client.ping())}
        except Exception:
            checks[name] = {"ready": False}
    try:
        async with httpx.AsyncClient(timeout=3) as http_client:
            response = await http_client.get(settings.ollama_url.rstrip("/") + "/api/tags")
            response.raise_for_status()
            names = [model["name"] for model in response.json().get("models", [])]
            checks["generation"] = {
                "ready": settings.generation_model in names,
                "model": settings.generation_model,
            }
    except Exception:
        checks["generation"] = {"ready": False, "model": settings.generation_model}
    for name, path in [
        ("embeddings", settings.embedding_path),
        ("reranker", settings.reranker_path),
    ]:

        def available(path=path):
            root = Path(path)
            return (root / "config.json").is_file() and any(root.glob("*.safetensors"))

        checks[name] = {"ready": await asyncio.to_thread(available)}
    return checks


@router.get("/administration/feedback")
async def feedback(status: str | None = None, identity: Identity = Depends(authenticate)):
    evaluator(identity)
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT * FROM atlas.feedback WHERE tenant_id=%s AND (%s::text IS NULL OR status=%s) ORDER BY created_at DESC LIMIT 100",
                (identity.tenant_id, status, status),
            )
        ).fetchall()
        for row in rows:
            # Feedback is voluntarily shared, but previously accessible evidence can still be revoked.
            accessible = True
            for source in row["sources"]:
                document_id = source.get("document_id")
                try:
                    if document_id:
                        allowed = await (
                            await conn.execute(
                                "SELECT id FROM atlas.documents WHERE id=%s AND lifecycle='active'",
                                (UUID(document_id),),
                            )
                        ).fetchone()
                    elif str(source.get("id", "")).startswith("entity:"):
                        allowed = await (
                            await conn.execute(
                                "SELECT id FROM atlas.entities WHERE id=%s AND NOT archived",
                                (UUID(source["id"].removeprefix("entity:")),),
                            )
                        ).fetchone()
                    else:
                        allowed = None
                    accessible = accessible and bool(allowed)
                except (ValueError, TypeError):
                    accessible = False
            if not accessible:
                row.update(
                    question="",
                    correction="",
                    reason="Evidence is no longer accessible",
                    sources=[],
                )
    return {"items": rows}


class FeedbackStatus(BaseModel):
    status: Literal["new", "reviewed", "closed"]


@router.patch("/administration/feedback/{feedback_id}")
async def triage_feedback(
    feedback_id: UUID, body: FeedbackStatus, identity: Identity = Depends(authenticate)
):
    evaluator(identity)
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "UPDATE atlas.feedback SET status=%s WHERE tenant_id=%s AND id=%s RETURNING id,status",
                (body.status, identity.tenant_id, feedback_id),
            )
        ).fetchone()
        if not row:
            raise HTTPException(404, "Feedback not found")
    return {"feedback": row}


class Candidate(BaseModel):
    document_id: UUID
    question: str = Field(min_length=5, max_length=2000)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=1)


@router.post("/administration/feedback/{feedback_id}/candidate")
async def feedback_candidate(
    feedback_id: UUID, body: Candidate, identity: Identity = Depends(authenticate)
):
    evaluator(identity)
    async with transaction(identity.tenant_id) as conn:
        feedback_row = await (
            await conn.execute(
                "SELECT id FROM atlas.feedback WHERE tenant_id=%s AND id=%s FOR UPDATE",
                (identity.tenant_id, feedback_id),
            )
        ).fetchone()
        doc = await (
            await conn.execute(
                "SELECT id,content_hash,length(content) AS size FROM atlas.documents WHERE tenant_id=%s AND id=%s AND status='ready' AND lifecycle='active'",
                (identity.tenant_id, body.document_id),
            )
        ).fetchone()
        if not feedback_row or not doc:
            raise HTTPException(404, "Feedback or authorized indexed document not found")
        if not body.start_offset < body.end_offset <= doc["size"]:
            raise HTTPException(422, "Choose a valid evidence span")
        existing = await (
            await conn.execute(
                "SELECT id FROM atlas.eval_labels WHERE tenant_id=%s AND document_id=%s",
                (identity.tenant_id, body.document_id),
            )
        ).fetchone()
        if existing:
            raise HTTPException(
                409,
                "This document already has an evaluation label; review it without replacing its history",
            )
        label_id = uuid4()
        await conn.execute(
            "INSERT INTO atlas.eval_labels(tenant_id,id,question,document_id,source_hash,start_offset,end_offset,split,reviewed) VALUES(%s,%s,%s,%s,%s,%s,%s,'development',false)",
            (
                identity.tenant_id,
                label_id,
                body.question,
                body.document_id,
                doc["content_hash"],
                body.start_offset,
                body.end_offset,
            ),
        )
        await conn.execute(
            "UPDATE atlas.feedback SET status='candidate' WHERE tenant_id=%s AND id=%s",
            (identity.tenant_id, feedback_id),
        )
        await audit(conn, identity, "evaluation.candidate_created", "eval_label", label_id)
    return {"label_id": label_id, "reviewed": False}
