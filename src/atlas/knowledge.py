"""Permission-aware spaces and versioned knowledge library."""

import asyncio
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from psycopg import sql
from psycopg.errors import ForeignKeyViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from atlas.auth import Identity, authenticate, require
from atlas.db import identity_transaction, transaction
from atlas.extraction import MAX_UPLOAD_BYTES, ExtractedDocument, ExtractionError, extract_upload
from atlas.serving import enqueue_conn

router = APIRouter(prefix="/api", tags=["knowledge"])
UPLOAD_ROOT = Path(".local/uploads")


class SpaceBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    visibility: Literal["company", "restricted"] = "company"
    tags: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("name")
    @classmethod
    def visible_name(cls, value):
        if not value.strip():
            raise ValueError("Enter a space name")
        return value.strip()


class Grant(BaseModel):
    subject_type: Literal["user", "team", "service"]
    subject_id: UUID
    permission: Literal["read", "write"] = "read"


class GrantBody(BaseModel):
    grants: list[Grant] = Field(max_length=200)

    @field_validator("grants")
    @classmethod
    def unique_subjects(cls, value):
        if len({(grant.subject_type, grant.subject_id) for grant in value}) != len(value):
            raise ValueError("Each recipient may have only one grant")
        return value


class TextBody(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    content: str = Field(min_length=1, max_length=1_000_000)
    space_id: UUID | None = None
    replace_document_id: UUID | None = None


class DocumentPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=250)
    tags: list[str] | None = Field(default=None, max_length=30)
    space_id: UUID | None = None
    owner_user_id: UUID | None = None
    review_due_at: datetime | None = None
    restricted: bool | None = None
    lifecycle: Literal["active", "archived", "trashed"] | None = None


async def accessible(conn, kind: str, resource_id: UUID, write=False):
    function = "atlas.can_space" if kind == "space" else "atlas.can_document"
    row = await (
        await conn.execute(f"SELECT {function}(%s,%s) allowed", (resource_id, write))
    ).fetchone()
    if not row or not row["allowed"]:
        raise HTTPException(404, "Resource not found or access unavailable")


async def invalidate(conn, tenant):
    await conn.execute(
        "UPDATE atlas.collections SET revision=revision+1 WHERE tenant_id=%s", (tenant,)
    )
    await conn.execute(
        "UPDATE atlas.tenants SET auth_revision=auth_revision+1 WHERE id=%s", (tenant,)
    )


def owner(identity):
    return getattr(identity, "user_id", None)


async def owner_labels(owner_ids):
    """Resolve only owner IDs obtained from resource-filtered rows, never a member directory."""
    ids = list({value for value in owner_ids if value})
    if not ids:
        return {}
    async with identity_transaction() as conn:
        rows = await (
            await conn.execute("SELECT id,name FROM atlas.users WHERE id=ANY(%s)", (ids,))
        ).fetchall()
    return {row["id"]: row["name"] for row in rows}


@router.get("/spaces")
async def spaces(identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT * FROM atlas.spaces WHERE tenant_id=%s ORDER BY name,id",
                (identity.tenant_id,),
            )
        ).fetchall()
    labels = await owner_labels(row["owner_user_id"] for row in rows)
    return [{**row, "owner_name": labels.get(row["owner_user_id"])} for row in rows]


@router.post("/spaces", status_code=201)
async def create_space(body: SpaceBody, identity: Identity = Depends(authenticate)):
    require(identity, "admin")
    async with transaction(identity.tenant_id) as conn:
        exists = await (
            await conn.execute(
                "SELECT id FROM atlas.spaces WHERE tenant_id=%s AND name=%s",
                (identity.tenant_id, body.name),
            )
        ).fetchone()
        if exists:
            raise HTTPException(409, "A space with this name already exists")
        return await (
            await conn.execute(
                "INSERT INTO atlas.spaces(tenant_id,id,name,description,visibility,owner_user_id,tags) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (
                    identity.tenant_id,
                    uuid4(),
                    body.name.strip(),
                    body.description,
                    body.visibility,
                    owner(identity),
                    body.tags,
                ),
            )
        ).fetchone()


@router.patch("/spaces/{space_id}")
async def edit_space(space_id: UUID, body: SpaceBody, identity: Identity = Depends(authenticate)):
    require(identity, "write")
    async with transaction(identity.tenant_id) as conn:
        await accessible(conn, "space", space_id, True)
        if getattr(identity, "role", "") not in {"owner", "admin"}:
            row = await (
                await conn.execute(
                    "SELECT owner_user_id FROM atlas.spaces WHERE tenant_id=%s AND id=%s",
                    (identity.tenant_id, space_id),
                )
            ).fetchone()
            if not owner(identity) or not row or row["owner_user_id"] != owner(identity):
                raise HTTPException(
                    403, "Only a space owner or company administrator can edit its access settings"
                )
        row = await (
            await conn.execute(
                "UPDATE atlas.spaces SET name=%s,description=%s,visibility=%s,tags=%s WHERE tenant_id=%s AND id=%s RETURNING *",
                (
                    body.name.strip(),
                    body.description,
                    body.visibility,
                    body.tags,
                    identity.tenant_id,
                    space_id,
                ),
            )
        ).fetchone()
        await invalidate(conn, identity.tenant_id)
        return row


async def effective_access(conn, identity, kind, resource_id):
    """Manager-only capability projection; authorization remains enforced by database policies."""
    manager = await (await conn.execute("SELECT atlas.can_manage() allowed")).fetchone()
    if identity.principal_kind != "user" or not manager or not manager["allowed"]:
        return None
    if kind == "space":
        resource = await (
            await conn.execute(
                "SELECT id space_id,false restricted FROM atlas.spaces WHERE id=%s", (resource_id,)
            )
        ).fetchone()
    else:
        resource = await (
            await conn.execute(
                "SELECT space_id,restricted FROM atlas.documents WHERE id=%s", (resource_id,)
            )
        ).fetchone()
    assert resource
    space = await (
        await conn.execute(
            "SELECT visibility FROM atlas.spaces WHERE id=%s", (resource["space_id"],)
        )
    ).fetchone()
    assert space
    grants = await (
        await conn.execute(
            "SELECT * FROM atlas.resource_grants WHERE tenant_id=%s AND (space_id=%s OR document_id=%s)",
            (identity.tenant_id, resource["space_id"], resource_id if kind == "document" else None),
        )
    ).fetchall()
    memberships = await (
        await conn.execute(
            "SELECT user_id,role FROM atlas.memberships WHERE tenant_id=%s AND status='active'",
            (identity.tenant_id,),
        )
    ).fetchall()
    teams = await (
        await conn.execute(
            "SELECT tm.user_id,tm.team_id,t.name FROM atlas.team_members tm JOIN atlas.teams t ON t.tenant_id=tm.tenant_id AND t.id=tm.team_id WHERE tm.tenant_id=%s",
            (identity.tenant_id,),
        )
    ).fetchall()
    services = await (
        await conn.execute(
            "SELECT id,name,legacy FROM atlas.service_accounts WHERE tenant_id=%s AND active",
            (identity.tenant_id,),
        )
    ).fetchall()
    keys = await (
        await conn.execute(
            "SELECT service_account_id,scopes FROM atlas.api_keys WHERE tenant_id=%s AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>now())",
            (identity.tenant_id,),
        )
    ).fetchall()
    tenant = await (
        await conn.execute(
            "SELECT claimed_at FROM atlas.tenants WHERE id=%s", (identity.tenant_id,)
        )
    ).fetchone()
    async with identity_transaction() as accounts:
        users = await (
            await accounts.execute(
                "SELECT id,name FROM atlas.users WHERE id=ANY(%s) AND disabled_at IS NULL AND email_verified_at IS NOT NULL",
                ([member["user_id"] for member in memberships],),
            )
        ).fetchall()
    names = {user["id"]: user["name"] for user in users}
    result = []
    subjects = [
        {
            "subject_type": "user",
            "subject_id": member["user_id"],
            "name": names[member["user_id"]],
            "role": member["role"],
        }
        for member in memberships
        if member["user_id"] in names
    ]
    subjects += [
        {
            "subject_type": "service",
            "subject_id": service["id"],
            "name": service["name"],
            "role": None,
            "legacy": service["legacy"],
        }
        for service in services
    ]
    for subject in subjects:
        human = subject["subject_type"] == "user"
        if human and subject["role"] in {"owner", "admin"}:
            result.append(
                {
                    **subject,
                    "can_read": True,
                    "can_edit": True,
                    "reasons": ["Company owner/admin override: all company-owned knowledge"],
                }
            )
            continue
        team_names = {
            team["team_id"]: team["name"]
            for team in teams
            if human and team["user_id"] == subject["subject_id"]
        }
        matched = [
            grant
            for grant in grants
            if (
                grant["subject_type"] == subject["subject_type"]
                and grant["subject_id"] == subject["subject_id"]
            )
            or (grant["subject_type"] == "team" and grant["subject_id"] in team_names)
        ]
        space_grants = [grant for grant in matched if grant["space_id"] == resource["space_id"]]
        document_grants = (
            [grant for grant in matched if grant["document_id"] == resource_id]
            if kind == "document"
            else []
        )
        legacy = bool(not human and subject.pop("legacy", False))
        legacy_allowed = legacy and tenant is not None and tenant["claimed_at"] is None
        inherited_company = human and space["visibility"] == "company"
        space_read = inherited_company or legacy_allowed or bool(space_grants)
        space_write = (
            inherited_company
            or legacy_allowed
            or any(grant["permission"] == "write" for grant in space_grants)
        )
        document_read = not resource["restricted"] or bool(document_grants)
        document_write = not resource["restricted"] or any(
            grant["permission"] == "write" for grant in document_grants
        )
        reasons = []
        if inherited_company:
            reasons.append("Inherited from company-wide space")
        elif legacy_allowed:
            reasons.append("Legacy integration access to an unclaimed workspace")
        for grant in matched:
            origin = "space" if grant["space_id"] else "document"
            recipient = (
                f"team {team_names[grant['subject_id']]}"
                if grant["subject_type"] == "team"
                else "direct grant"
            )
            reasons.append(f"{origin.title()} {grant['permission']} via {recipient}")
        if not space_read:
            reasons.append("No access to the enclosing space")
        if resource["restricted"] and not document_read:
            reasons.append("Document restriction has no matching person, team or integration grant")
        if human:
            readable = True
            editable = subject["role"] == "editor"
            if not editable:
                reasons.append("Viewer role prevents edits even when a grant says write")
        else:
            scopes = {
                scope
                for key in keys
                if key["service_account_id"] == subject["subject_id"]
                for scope in key["scopes"]
            }
            readable, editable = bool(scopes & {"read", "query"}), "write" in scopes
            if legacy and not legacy_allowed:
                readable, editable = False, False
                reasons.append(
                    "Legacy credentials are disabled after workspace ownership is claimed"
                )
            if not readable:
                reasons.append("No active unexpired integration key with read or query scope")
            if not editable:
                reasons.append("No active unexpired integration key with write scope")
        can_read = bool(readable and space_read and document_read)
        can_edit = bool(editable and space_write and document_write)
        if can_read and not can_edit and editable:
            reasons.append(
                "Editing requires write permission through both space and any document restriction"
            )
        result.append({**subject, "can_read": can_read, "can_edit": can_edit, "reasons": reasons})
    return sorted(
        result,
        key=lambda row: (row["subject_type"], row["name"].casefold(), str(row["subject_id"])),
    )


async def grants_for(identity, kind, resource_id):
    require(identity, "read")
    column = "space_id" if kind == "space" else "document_id"
    async with transaction(identity.tenant_id) as conn:
        await accessible(conn, kind, resource_id)
        rows = await (
            await conn.execute(
                f"SELECT subject_type,subject_id,permission FROM atlas.resource_grants WHERE tenant_id=%s AND {column}=%s",
                (identity.tenant_id, resource_id),
            )
        ).fetchall()
        effective = await effective_access(conn, identity, kind, resource_id)
        result = {"grants": rows, "administrators_can_access": True}
        if effective is not None:
            result["effective_access"] = effective
        return result


async def update_grants(identity, kind, resource_id, body):
    require(identity, "admin")
    column = "space_id" if kind == "space" else "document_id"
    async with transaction(identity.tenant_id) as conn:
        await accessible(conn, kind, resource_id, True)
        # Validate every principal belongs to this organization before saving grants.
        for grant in body.grants:
            table, field = {
                "user": ("memberships", "user_id"),
                "team": ("teams", "id"),
                "service": ("service_accounts", "id"),
            }[grant.subject_type]
            active = (
                " AND status='active'"
                if grant.subject_type == "user"
                else " AND active"
                if grant.subject_type == "service"
                else ""
            )
            exists = await (
                await conn.execute(
                    f"SELECT 1 FROM atlas.{table} WHERE tenant_id=%s AND {field}=%s{active}",
                    (identity.tenant_id, grant.subject_id),
                )
            ).fetchone()
            if not exists:
                raise HTTPException(422, "Grant principal is not a member of this company")
        await conn.execute(
            f"DELETE FROM atlas.resource_grants WHERE tenant_id=%s AND {column}=%s",
            (identity.tenant_id, resource_id),
        )
        for grant in body.grants:
            await conn.execute(
                f"INSERT INTO atlas.resource_grants(tenant_id,id,{column},subject_type,subject_id,permission) VALUES(%s,%s,%s,%s,%s,%s)",
                (
                    identity.tenant_id,
                    uuid4(),
                    resource_id,
                    grant.subject_type,
                    grant.subject_id,
                    grant.permission,
                ),
            )
        await invalidate(conn, identity.tenant_id)
    return {"updated": True}


@router.get("/spaces/{space_id}/grants")
async def space_grants(space_id: UUID, identity: Identity = Depends(authenticate)):
    return await grants_for(identity, "space", space_id)


@router.put("/spaces/{space_id}/grants")
async def set_space_grants(
    space_id: UUID, body: GrantBody, identity: Identity = Depends(authenticate)
):
    return await update_grants(identity, "space", space_id, body)


@router.get("/library/{document_id}/grants")
async def document_grants(document_id: UUID, identity: Identity = Depends(authenticate)):
    return await grants_for(identity, "document", document_id)


@router.put("/library/{document_id}/grants")
async def set_document_grants(
    document_id: UUID, body: GrantBody, identity: Identity = Depends(authenticate)
):
    return await update_grants(identity, "document", document_id, body)


@router.get("/library/owners")
async def library_owners(
    space_id: UUID | None = None,
    lifecycle: Literal["active", "archived", "trashed", "all"] = "active",
    identity: Identity = Depends(authenticate),
):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT DISTINCT owner_user_id FROM atlas.documents WHERE tenant_id=%s AND owner_user_id IS NOT NULL AND (%s::uuid IS NULL OR space_id=%s) AND (%s='all' OR lifecycle=%s)",
                (identity.tenant_id, space_id, space_id, lifecycle, lifecycle),
            )
        ).fetchall()
    labels = await owner_labels(row["owner_user_id"] for row in rows)
    return {
        "items": sorted(
            [{"user_id": user_id, "name": name} for user_id, name in labels.items()],
            key=lambda row: row["name"].casefold(),
        )
    }


@router.get("/library")
async def library(
    q: str = "",
    space_id: UUID | None = None,
    tag: str | None = None,
    status: str | None = None,
    lifecycle: Literal["active", "archived", "trashed", "all"] = "active",
    media_type: str | None = None,
    owner_user_id: UUID | None = None,
    updated_since: datetime | None = None,
    sort: Literal["updated", "title", "oldest"] = "updated",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    identity: Identity = Depends(authenticate),
):
    require(identity, "read")
    where = ["d.tenant_id=%s"]
    params: list[Any] = [identity.tenant_id]
    for value, clause in [
        (q, "d.title ILIKE %s"),
        (space_id, "d.space_id=%s"),
        (tag, "%s=ANY(d.tags)"),
        (status, "d.status=%s"),
        (media_type, "d.media_type=%s"),
        (owner_user_id, "d.owner_user_id=%s"),
        (updated_since, "d.updated_at>=%s"),
    ]:
        if value:
            where.append(clause)
            params.append(f"%{q}%" if clause == "d.title ILIKE %s" else value)
    if lifecycle != "all":
        where.append("d.lifecycle=%s")
        params.append(lifecycle)
    clause = " AND ".join(where)
    order = {
        "updated": "d.updated_at DESC,d.id",
        "title": "d.title,d.id",
        "oldest": "d.updated_at,d.id",
    }[sort]
    async with transaction(identity.tenant_id) as conn:
        count = await (
            await conn.execute(f"SELECT count(*) n FROM atlas.documents d WHERE {clause}", params)
        ).fetchone()
        rows = await (
            await conn.execute(
                f"SELECT d.id,d.title,d.space_id,d.media_type,d.status,d.lifecycle,d.tags,d.owner_user_id,d.review_due_at,d.restricted,d.current_version_id,d.pending_version_id,d.created_at,d.updated_at,length(d.content) characters,v.status pending_status FROM atlas.documents d LEFT JOIN atlas.document_versions v ON v.tenant_id=d.tenant_id AND v.id=d.pending_version_id WHERE {clause} ORDER BY {order} LIMIT %s OFFSET %s",
                [*params, page_size, (page - 1) * page_size],
            )
        ).fetchall()
    assert count
    return {"items": rows, "total": count["n"], "page": page, "page_size": page_size}


async def store_document(
    identity, title, filename, extracted, data, space_id=None, replace_id=None
):
    require(identity, "write")
    tenant, version_id = identity.tenant_id, uuid4()
    storage_key = str(uuid4()) if data is not None else None
    content_hash = hashlib.sha256(extracted.content.encode()).hexdigest()
    # Store only opaque names, never user supplied paths. A failed transaction removes its blob.
    path = UPLOAD_ROOT / storage_key if storage_key else None
    if path:

        def persist():
            UPLOAD_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
            with path.open("xb") as output:
                output.write(data)
            path.chmod(0o600)

        await asyncio.to_thread(persist)
    try:
        async with transaction(tenant) as conn:
            if space_id is None and replace_id is None:
                general = await (
                    await conn.execute(
                        "SELECT id FROM atlas.spaces WHERE tenant_id=%s AND name='General'",
                        (tenant,),
                    )
                ).fetchone()
                if not general and getattr(identity, "role", None) in {"owner", "admin"}:
                    general = await (
                        await conn.execute(
                            "INSERT INTO atlas.spaces(tenant_id,id,name,owner_user_id) VALUES(%s,%s,'General',%s) ON CONFLICT(tenant_id,name) DO UPDATE SET name=excluded.name RETURNING id",
                            (tenant, uuid4(), owner(identity)),
                        )
                    ).fetchone()
                if general:
                    space_id = general["id"]
                else:
                    raise HTTPException(404, "Choose a knowledge space you are permitted to edit")
            if space_id:
                await accessible(conn, "space", space_id, True)
            if replace_id:
                await accessible(conn, "document", replace_id, True)
                doc = await (
                    await conn.execute(
                        "SELECT * FROM atlas.documents WHERE tenant_id=%s AND id=%s FOR UPDATE",
                        (tenant, replace_id),
                    )
                ).fetchone()
                if not doc:
                    raise HTTPException(404, "Document not found")
                if doc["lifecycle"] == "trashed":
                    raise HTTPException(409, "Restore the document before replacing it")
                if doc["pending_version_id"]:
                    pending = await (
                        await conn.execute(
                            "SELECT status FROM atlas.document_versions WHERE tenant_id=%s AND id=%s",
                            (tenant, doc["pending_version_id"]),
                        )
                    ).fetchone()
                    if pending and pending["status"] in {"pending", "indexing"}:
                        raise HTTPException(409, "Wait for the current replacement to finish")
                if space_id and space_id != doc["space_id"]:
                    raise HTTPException(
                        422, "Move the document explicitly before replacing its file"
                    )
                latest = await (
                    await conn.execute(
                        "SELECT coalesce(max(number),0)+1 n FROM atlas.document_versions WHERE tenant_id=%s AND document_id=%s",
                        (tenant, replace_id),
                    )
                ).fetchone()
                assert latest
                number = latest["n"]
            else:
                duplicate = await (
                    await conn.execute(
                        "SELECT id FROM atlas.documents WHERE tenant_id=%s AND space_id IS NOT DISTINCT FROM %s AND content_hash=%s AND lifecycle!='trashed' LIMIT 1",
                        (tenant, space_id, content_hash),
                    )
                ).fetchone()
                if duplicate:
                    raise HTTPException(
                        409,
                        {
                            "code": "duplicate",
                            "document_id": str(duplicate["id"]),
                            "message": "This content already exists in the destination. Skip it or replace the existing document.",
                        },
                    )
                doc = await (
                    await conn.execute(
                        "INSERT INTO atlas.documents(tenant_id,id,title,source_key,content_hash,content,media_type,space_id,owner_user_id,status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending') RETURNING *",
                        (
                            tenant,
                            uuid4(),
                            title,
                            str(uuid4()),
                            content_hash,
                            extracted.content,
                            extracted.media_type,
                            space_id,
                            owner(identity),
                        ),
                    )
                ).fetchone()
                number = 1
            assert doc
            await conn.execute(
                "INSERT INTO atlas.document_versions(tenant_id,id,document_id,number,title,content,content_hash,media_type,filename,storage_key,byte_size,source_segments,status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')",
                (
                    tenant,
                    version_id,
                    doc["id"],
                    number,
                    title,
                    extracted.content,
                    content_hash,
                    extracted.media_type,
                    filename,
                    storage_key,
                    len(data) if data else len(extracted.content.encode()),
                    Jsonb(extracted.segments),
                ),
            )
            await conn.execute(
                "UPDATE atlas.documents SET pending_version_id=%s,updated_at=now(),status=CASE WHEN current_version_id IS NULL THEN 'pending' ELSE status END WHERE tenant_id=%s AND id=%s",
                (version_id, tenant, doc["id"]),
            )
            job = await enqueue_conn(conn, tenant, doc["id"])
            return {
                "id": doc["id"],
                "version_id": version_id,
                "number": number,
                "status": "queued",
                **job,
            }
    except BaseException:
        if path:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        raise


@router.post("/library/text", status_code=202)
async def upload_text(body: TextBody, identity: Identity = Depends(authenticate)):
    extracted = ExtractedDocument(
        body.content, "text/plain", [{"start": 0, "end": len(body.content), "section": "Document"}]
    )
    return await store_document(
        identity,
        body.title,
        body.title + ".txt",
        extracted,
        body.content.encode(),
        body.space_id,
        body.replace_document_id,
    )


@router.post("/library/upload", status_code=202)
async def upload_file(
    file: UploadFile = File(...),
    space_id: UUID | None = Form(None),
    title: str | None = Form(None),
    replace_document_id: UUID | None = Form(None),
    identity: Identity = Depends(authenticate),
):
    require(identity, "write")
    filename = Path(file.filename or "document.txt").name[:250]
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        extracted = await extract_upload(filename, data)
    except (ExtractionError, TimeoutError) as exc:
        raise HTTPException(422, str(exc) or "Document extraction exceeded the time limit") from exc
    return await store_document(
        identity,
        (title or filename)[:250],
        filename,
        extracted,
        data,
        space_id,
        replace_document_id,
    )


@router.get("/library/{document_id}")
async def document_detail(document_id: UUID, identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        await accessible(conn, "document", document_id)
        return await (
            await conn.execute(
                "SELECT d.*,v.number,v.source_segments,p.status pending_status FROM atlas.documents d LEFT JOIN atlas.document_versions v ON v.tenant_id=d.tenant_id AND v.id=d.current_version_id LEFT JOIN atlas.document_versions p ON p.tenant_id=d.tenant_id AND p.id=d.pending_version_id WHERE d.tenant_id=%s AND d.id=%s",
                (identity.tenant_id, document_id),
            )
        ).fetchone()


@router.patch("/library/{document_id}")
async def edit_document(
    document_id: UUID, body: DocumentPatch, identity: Identity = Depends(authenticate)
):
    require(identity, "write")
    values = body.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(422, "Choose at least one field to update")
    async with transaction(identity.tenant_id) as conn:
        await accessible(conn, "document", document_id, True)
        if "restricted" in values or "space_id" in values:
            require(identity, "admin")
        if values.get("space_id"):
            await accessible(conn, "space", values["space_id"], True)
        if values.get("owner_user_id"):
            member = await (
                await conn.execute(
                    "SELECT 1 FROM atlas.memberships WHERE tenant_id=%s AND user_id=%s AND status='active'",
                    (identity.tenant_id, values["owner_user_id"]),
                )
            ).fetchone()
            if not member:
                raise HTTPException(422, "Document owner must be an active company member")
        if values.get("lifecycle") == "active":
            await conn.execute(
                "UPDATE atlas.documents SET status=CASE WHEN current_version_id IS NULL THEN 'pending' ELSE 'ready' END WHERE tenant_id=%s AND id=%s AND status='deleted'",
                (identity.tenant_id, document_id),
            )
        query = sql.SQL(
            "UPDATE atlas.documents SET {},updated_at=now() WHERE tenant_id=%s AND id=%s RETURNING *"
        ).format(sql.SQL(",").join(sql.SQL("{}=%s").format(sql.Identifier(key)) for key in values))
        row = await (
            await conn.execute(query, [*values.values(), identity.tenant_id, document_id])
        ).fetchone()
        await invalidate(conn, identity.tenant_id)
        return row


@router.get("/library/{document_id}/versions")
async def versions(document_id: UUID, identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        await accessible(conn, "document", document_id)
        return await (
            await conn.execute(
                "SELECT id,number,title,media_type,filename,byte_size,source_segments,status,created_at FROM atlas.document_versions WHERE tenant_id=%s AND document_id=%s ORDER BY number DESC",
                (identity.tenant_id, document_id),
            )
        ).fetchall()


async def get_version(identity, document_id, version_id):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        await accessible(conn, "document", document_id)
        row = await (
            await conn.execute(
                "SELECT * FROM atlas.document_versions WHERE tenant_id=%s AND document_id=%s AND id=%s",
                (identity.tenant_id, document_id, version_id),
            )
        ).fetchone()
        if not row:
            raise HTTPException(404, "Version not found")
        return row


@router.get("/library/{document_id}/versions/{version_id}")
async def version_detail(
    document_id: UUID, version_id: UUID, identity: Identity = Depends(authenticate)
):
    row = await get_version(identity, document_id, version_id)
    row.pop("storage_key", None)
    return row


@router.get("/library/{document_id}/versions/{version_id}/download")
async def download(document_id: UUID, version_id: UUID, identity: Identity = Depends(authenticate)):
    row = await get_version(identity, document_id, version_id)
    if row["storage_key"]:
        path = UPLOAD_ROOT / str(UUID(row["storage_key"]))
        if not await asyncio.to_thread(path.is_file):
            raise HTTPException(410, "The original file is no longer available")
        size = (await asyncio.to_thread(path.stat)).st_size

        async def chunks():
            original = await asyncio.to_thread(path.open, "rb")
            try:
                while True:
                    async with transaction(identity.tenant_id) as conn:
                        permitted = await (
                            await conn.execute(
                                "SELECT atlas.principal_active() AND atlas.can_document(%s) allowed",
                                (document_id,),
                            )
                        ).fetchone()
                    if not permitted or not permitted["allowed"]:
                        # Content-Length makes a revoked partial transfer fail rather than look like a complete file.
                        return
                    block = await asyncio.to_thread(original.read, 65536)
                    if not block:
                        return
                    yield block
            finally:
                await asyncio.to_thread(original.close)

        return StreamingResponse(
            chunks(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(Path(row["filename"]).name),
                "Content-Length": str(size),
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )
    return Response(
        row["content"],
        media_type="text/plain",
        headers={
            "Content-Disposition": 'attachment; filename="document.txt"',
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/library/{document_id}/versions/{version_id}/restore", status_code=202)
async def restore_version(
    document_id: UUID, version_id: UUID, identity: Identity = Depends(authenticate)
):
    row = await get_version(identity, document_id, version_id)
    data = None
    if row["storage_key"]:
        path = UPLOAD_ROOT / str(UUID(row["storage_key"]))
        if not await asyncio.to_thread(path.is_file):
            raise HTTPException(410, "Original file is unavailable")
        data = await asyncio.to_thread(path.read_bytes)
    return await store_document(
        identity,
        row["title"],
        row["filename"],
        ExtractedDocument(row["content"], row["media_type"], row["source_segments"]),
        data,
        replace_id=document_id,
    )


@router.post("/library/{document_id}/retry", status_code=202)
async def retry_document(document_id: UUID, identity: Identity = Depends(authenticate)):
    require(identity, "write")
    async with transaction(identity.tenant_id) as conn:
        await accessible(conn, "document", document_id, True)
        await conn.execute(
            "UPDATE atlas.documents SET status=CASE WHEN current_version_id IS NULL THEN 'pending' ELSE status END WHERE tenant_id=%s AND id=%s",
            (identity.tenant_id, document_id),
        )
        return await enqueue_conn(conn, identity.tenant_id, document_id)


@router.delete("/library/{document_id}")
async def remove_document(
    document_id: UUID, permanent: bool = False, identity: Identity = Depends(authenticate)
):
    require(identity, "write")
    files = []
    async with transaction(identity.tenant_id) as conn:
        await accessible(conn, "document", document_id, True)
        doc = await (
            await conn.execute(
                "SELECT lifecycle FROM atlas.documents WHERE tenant_id=%s AND id=%s FOR UPDATE",
                (identity.tenant_id, document_id),
            )
        ).fetchone()
        if not doc:
            raise HTTPException(404, "Document not found")
        if permanent:
            require(identity, "admin")
            if doc["lifecycle"] != "trashed":
                raise HTTPException(
                    409, "Move the document to trash before permanently deleting it"
                )
            files = await (
                await conn.execute(
                    "SELECT storage_key FROM atlas.document_versions WHERE tenant_id=%s AND document_id=%s AND storage_key IS NOT NULL",
                    (identity.tenant_id, document_id),
                )
            ).fetchall()
            await conn.execute(
                "DELETE FROM atlas.outbox WHERE tenant_id=%s AND job_id IN (SELECT id FROM atlas.ingestion_jobs WHERE tenant_id=%s AND document_id=%s)",
                (identity.tenant_id, identity.tenant_id, document_id),
            )
            await conn.execute(
                "DELETE FROM atlas.ingestion_jobs WHERE tenant_id=%s AND document_id=%s",
                (identity.tenant_id, document_id),
            )
            await conn.execute(
                "DELETE FROM atlas.eval_labels WHERE tenant_id=%s AND document_id=%s",
                (identity.tenant_id, document_id),
            )
            await conn.execute(
                "UPDATE atlas.documents SET current_version_id=NULL,pending_version_id=NULL WHERE tenant_id=%s AND id=%s",
                (identity.tenant_id, document_id),
            )
            await conn.execute(
                "DELETE FROM atlas.documents WHERE tenant_id=%s AND id=%s",
                (identity.tenant_id, document_id),
            )
        else:
            await conn.execute(
                "UPDATE atlas.documents SET lifecycle='trashed',updated_at=now() WHERE tenant_id=%s AND id=%s",
                (identity.tenant_id, document_id),
            )
        await invalidate(conn, identity.tenant_id)
    if permanent:
        from atlas.serving import cache_redis

        try:
            batch = []
            async for key in cache_redis.scan_iter(
                match=f"atlas:answer:{identity.tenant_id}:*", count=100
            ):
                batch.append(key)
                if len(batch) >= 100:
                    await cache_redis.delete(*batch)
                    batch = []
            if batch:
                await cache_redis.delete(*batch)
        except Exception:
            # Revision and live source checks already block reuse. Expiring cache copies are inaccessible.
            pass
    for row in files:
        await asyncio.to_thread(
            (UPLOAD_ROOT / str(UUID(row["storage_key"]))).unlink, missing_ok=True
        )
    return {"deleted": True, "permanent": permanent}


class AccessRequestBody(BaseModel):
    reason: str = Field(default="", max_length=1000)


@router.post("/spaces/{space_id}/access-requests", status_code=202)
async def request_access(
    space_id: UUID, body: AccessRequestBody, identity: Identity = Depends(authenticate)
):
    if not owner(identity):
        raise HTTPException(403, "Sign in with a user account to request access")
    async with transaction(identity.tenant_id) as conn:
        # RLS on requests permits a member to request a known space identifier without disclosing its content.
        request_id = uuid4()
        try:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO atlas.access_requests(tenant_id,id,user_id,space_id,reason) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,user_id,space_id) WHERE status='pending' DO UPDATE SET reason=excluded.reason",
                    (identity.tenant_id, request_id, owner(identity), space_id, body.reason),
                )
        except ForeignKeyViolation as exc:
            raise HTTPException(404, "Space is unavailable in this company") from exc
    return {"requested": True}


@router.get("/access-requests")
async def access_requests(identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        return await (
            await conn.execute(
                "SELECT * FROM atlas.access_requests WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 100",
                (identity.tenant_id,),
            )
        ).fetchall()


class ResolveAccessBody(BaseModel):
    decision: Literal["approved", "denied"]


@router.post("/access-requests/{request_id}/resolve")
async def resolve_access(
    request_id: UUID, body: ResolveAccessBody, identity: Identity = Depends(authenticate)
):
    require(identity, "admin")
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "UPDATE atlas.access_requests SET status=%s,resolved_at=now() WHERE tenant_id=%s AND id=%s AND status='pending' RETURNING *",
                (body.decision, identity.tenant_id, request_id),
            )
        ).fetchone()
        if not row:
            raise HTTPException(404, "Pending request not found")
        if body.decision == "approved":
            await conn.execute(
                "INSERT INTO atlas.resource_grants(tenant_id,id,space_id,subject_type,subject_id,permission) VALUES(%s,%s,%s,'user',%s,'read') ON CONFLICT DO NOTHING",
                (identity.tenant_id, uuid4(), row["space_id"], row["user_id"]),
            )
        await invalidate(conn, identity.tenant_id)
    return {"status": body.decision}


class BulkBody(BaseModel):
    document_ids: list[UUID] = Field(min_length=1, max_length=100)
    action: Literal["tag", "move", "archive", "trash", "restore"]
    tags: list[str] = Field(default_factory=list, max_length=30)
    space_id: UUID | None = None


@router.post("/library/bulk")
async def bulk_edit(body: BulkBody, identity: Identity = Depends(authenticate)):
    require(identity, "write")
    ids = list(set(body.document_ids))
    async with transaction(identity.tenant_id) as conn:
        for document_id in ids:
            await accessible(conn, "document", document_id, True)
        if body.action == "move":
            require(identity, "admin")
            if body.space_id:
                await accessible(conn, "space", body.space_id, True)
            await conn.execute(
                "UPDATE atlas.documents SET space_id=%s,updated_at=now() WHERE tenant_id=%s AND id=ANY(%s)",
                (body.space_id, identity.tenant_id, ids),
            )
        elif body.action == "tag":
            await conn.execute(
                "UPDATE atlas.documents SET tags=ARRAY(SELECT DISTINCT unnest(tags || %s::text[])),updated_at=now() WHERE tenant_id=%s AND id=ANY(%s)",
                (body.tags, identity.tenant_id, ids),
            )
        else:
            lifecycle = {"archive": "archived", "trash": "trashed", "restore": "active"}[
                body.action
            ]
            await conn.execute(
                "UPDATE atlas.documents SET lifecycle=%s,updated_at=now() WHERE tenant_id=%s AND id=ANY(%s)",
                (lifecycle, identity.tenant_id, ids),
            )
        await invalidate(conn, identity.tenant_id)
    return {"updated": len(ids)}
