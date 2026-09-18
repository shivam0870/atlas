"""Company membership and invitation operations, serialized on the organization row."""

import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from atlas.accounts import (
    current_account,
    nonempty_name,
    normalize_email,
    require_recent,
    require_verified,
    throttle,
)
from atlas.auth import digest
from atlas.config import settings
from atlas.db import identity_transaction
from atlas.mail import send_mail

router = APIRouter(prefix="/api/organizations", tags=["organizations"])
Role = Literal["owner", "admin", "editor", "viewer"]


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(min_length=3, max_length=60, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(default="", max_length=2000)
    website: str = Field(default="", max_length=300)
    kind: Literal["company", "personal"] = "company"
    _name = field_validator("name")(nonempty_name)

    @field_validator("website")
    @classmethod
    def safe_website(cls, value):
        if value and not re.match(r"^https?://[^\s]+$", value):
            raise ValueError("Use an http or https website URL")
        return value


class OrganizationEdit(BaseModel):
    _name = field_validator("name")(nonempty_name)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    website: str | None = Field(default=None, max_length=300)


class MemberEdit(BaseModel):
    role: Role | None = None
    status: Literal["active", "suspended"] | None = None


class InvitationCreate(BaseModel):
    email: str
    role: Literal["admin", "editor", "viewer"] = "viewer"
    _email = field_validator("email")(normalize_email)


class AcceptInvitation(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class TeamBody(BaseModel):
    _name = field_validator("name")(nonempty_name)
    name: str = Field(min_length=1, max_length=100)


class TransferBody(BaseModel):
    user_id: UUID


async def org_access(conn, tenant: UUID, user: dict, admin=False, owner=False, lock=False):
    require_verified(user)
    # Serialize membership changes BEFORE reading roles; stale privileges must not survive a concurrent demotion.
    if lock:
        await conn.execute("SELECT id FROM atlas.tenants WHERE id=%s FOR UPDATE", (tenant,))
    org = await (
        await conn.execute(
            "SELECT t.*,m.role,m.can_evaluate FROM atlas.tenants t JOIN atlas.memberships m ON m.tenant_id=t.id JOIN atlas.users u ON u.id=m.user_id WHERE t.id=%s AND m.user_id=%s AND m.status='active' AND t.status='active' AND u.disabled_at IS NULL",
            (tenant, user["id"]),
        )
    ).fetchone()
    if not org:
        raise HTTPException(404, "Workspace not found or inaccessible")
    if owner and org["role"] != "owner":
        raise HTTPException(403, "Only an owner can perform this action")
    if admin and org["role"] not in {"owner", "admin"}:
        raise HTTPException(403, "An administrator role is required")
    return org


def public_org(org):
    result = {
        key: org[key]
        for key in ["id", "name", "slug", "description", "website", "kind", "auth_revision", "role"]
    }

    result["can_evaluate"] = org["role"] in {"owner", "admin"} or bool(org.get("can_evaluate"))
    return result


async def protect_last_owner(conn, tenant, member, new_role=None, new_status=None, removing=False):
    was_owner = member["role"] == "owner" and member["status"] == "active"
    staying_owner = (
        not removing
        and (new_role or member["role"]) == "owner"
        and (new_status or member["status"]) == "active"
    )
    if was_owner and not staying_owner:
        count = await (
            await conn.execute(
                "SELECT count(*) AS n FROM atlas.memberships WHERE tenant_id=%s AND role='owner' AND status='active'",
                (tenant,),
            )
        ).fetchone()
        if count["n"] <= 1:
            raise HTTPException(
                409, "Transfer ownership before removing or demoting the last owner"
            )


@router.get("")
async def organizations(user: dict = Depends(current_account)):
    async with identity_transaction() as conn:
        rows = await (
            await conn.execute(
                "SELECT t.*,m.role,m.can_evaluate FROM atlas.tenants t JOIN atlas.memberships m ON m.tenant_id=t.id WHERE m.user_id=%s AND m.status='active' AND t.status='active' ORDER BY t.created_at",
                (user["id"],),
            )
        ).fetchall()
    return {"items": [public_org(row) for row in rows]}


@router.post("", status_code=201)
async def create_organization(
    body: OrganizationCreate, request: Request, user: dict = Depends(current_account)
):
    require_verified(user)
    await throttle(request, "organization", str(user["id"]), 5)
    async with identity_transaction() as conn:
        active_user = await (
            await conn.execute(
                "SELECT id FROM atlas.users WHERE id=%s AND disabled_at IS NULL AND email_verified_at IS NOT NULL FOR UPDATE",
                (user["id"],),
            )
        ).fetchone()
        if not active_user:
            raise HTTPException(401, "Sign in again")
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("org:" + body.slug,)
        )
        existing = await (
            await conn.execute(
                "SELECT t.*,m.role,m.can_evaluate FROM atlas.tenants t LEFT JOIN atlas.memberships m ON m.tenant_id=t.id AND m.user_id=%s WHERE t.slug=%s",
                (user["id"], body.slug),
            )
        ).fetchone()
        if existing:
            if (
                existing["role"] == "owner"
                and existing["name"] == body.name
                and existing["kind"] == body.kind
            ):
                return {"organization": public_org(existing)}
            raise HTTPException(409, "That workspace URL is already in use")
        tenant = uuid4()
        org = await (
            await conn.execute(
                "INSERT INTO atlas.tenants(id,name,slug,description,website,kind,claimed_at) VALUES(%s,%s,%s,%s,%s,%s,now()) RETURNING *",
                (tenant, body.name.strip(), body.slug, body.description, body.website, body.kind),
            )
        ).fetchone()
        await conn.execute(
            "INSERT INTO atlas.memberships(tenant_id,user_id,role) VALUES(%s,%s,'owner')",
            (tenant, user["id"]),
        )
        await conn.execute(
            "INSERT INTO atlas.spaces(tenant_id,id,name,description,visibility,created_by) VALUES(%s,%s,'General','Your workspace knowledge','company',%s)",
            (tenant, uuid4(), user["id"]),
        )
        org["role"] = "owner"
    return {"organization": public_org(org)}


@router.get("/pending-invitations")
async def pending_invitations(user: dict = Depends(current_account)):
    require_verified(user)
    async with identity_transaction() as conn:
        rows = await (
            await conn.execute(
                "SELECT i.id,i.tenant_id,t.name,t.slug,i.role,i.expires_at FROM atlas.invitations i JOIN atlas.tenants t ON t.id=i.tenant_id JOIN atlas.users u ON u.email=i.email WHERE u.id=%s AND u.disabled_at IS NULL AND u.email_verified_at IS NOT NULL AND t.status='active' AND i.accepted_at IS NULL AND i.revoked_at IS NULL AND i.expires_at>now() ORDER BY i.created_at DESC",
                (user["id"],),
            )
        ).fetchall()
    return {"items": rows}


async def accept_matching_invitation(conn, invitation_id: UUID, user: dict):
    # Lock organization before invitation, matching revocation and resend.
    invite = await (
        await conn.execute("SELECT tenant_id FROM atlas.invitations WHERE id=%s", (invitation_id,))
    ).fetchone()
    if not invite:
        raise HTTPException(400, "This invitation is invalid or expired")
    tenant = invite["tenant_id"]
    active = await (
        await conn.execute(
            "SELECT id FROM atlas.tenants WHERE id=%s AND status='active' FOR UPDATE", (tenant,)
        )
    ).fetchone()
    invite = await (
        await conn.execute(
            "SELECT * FROM atlas.invitations WHERE id=%s AND revoked_at IS NULL AND expires_at>now() FOR UPDATE",
            (invitation_id,),
        )
    ).fetchone()
    if not invite or not active:
        raise HTTPException(400, "This invitation is invalid or expired")
    matching_user = await (
        await conn.execute(
            "SELECT id FROM atlas.users WHERE id=%s AND email=%s AND email_verified_at IS NOT NULL AND disabled_at IS NULL",
            (user["id"], invite["email"]),
        )
    ).fetchone()
    if not matching_user:
        raise HTTPException(
            403, "Sign in with the verified email address this invitation was sent to"
        )
    membership = await (
        await conn.execute(
            "SELECT * FROM atlas.memberships WHERE tenant_id=%s AND user_id=%s",
            (tenant, user["id"]),
        )
    ).fetchone()
    if invite["accepted_at"]:
        if not membership or membership["status"] != "active":
            raise HTTPException(400, "This invitation has already been used")
    elif membership and membership["status"] != "active":
        raise HTTPException(403, "Ask your administrator to restore your suspended membership")
    else:
        # Accepting another invite must never escalate an existing member's role.
        await conn.execute(
            "INSERT INTO atlas.memberships(tenant_id,user_id,role) VALUES(%s,%s,%s) ON CONFLICT(tenant_id,user_id) DO NOTHING",
            (tenant, user["id"], invite["role"]),
        )
        await conn.execute(
            "UPDATE atlas.invitations SET accepted_at=now() WHERE id=%s", (invitation_id,)
        )
    return {"organization": public_org(await org_access(conn, tenant, user))}


@router.post("/invitations/accept")
async def accept_invitation(
    body: AcceptInvitation, request: Request, user: dict = Depends(current_account)
):
    require_verified(user)
    await throttle(request, "invite-accept", str(user["id"]), 10)
    async with identity_transaction() as conn:
        invite = await (
            await conn.execute(
                "SELECT id FROM atlas.invitations WHERE digest=%s", (digest(body.token),)
            )
        ).fetchone()
        if not invite:
            raise HTTPException(400, "This invitation is invalid or expired")
        return await accept_matching_invitation(conn, invite["id"], user)


@router.post("/pending-invitations/{invitation_id}/accept")
async def accept_pending_invitation(
    invitation_id: UUID, request: Request, user: dict = Depends(current_account)
):
    require_verified(user)
    await throttle(request, "invite-accept", str(user["id"]), 10)
    async with identity_transaction() as conn:
        return await accept_matching_invitation(conn, invitation_id, user)


@router.patch("/{tenant}")
async def edit_organization(
    tenant: UUID, body: OrganizationEdit, user: dict = Depends(current_account)
):
    if body.website and not re.match(r"^https?://[^\s]+$", body.website):
        raise HTTPException(422, "Use an http or https website URL")
    async with identity_transaction() as conn:
        org = await org_access(conn, tenant, user, admin=True, lock=True)
        await conn.execute(
            "UPDATE atlas.tenants SET name=coalesce(%s,name),description=coalesce(%s,description),website=coalesce(%s,website) WHERE id=%s",
            (body.name, body.description, body.website, tenant),
        )
        org.update(body.model_dump(exclude_none=True))
    return {"organization": public_org(org)}


@router.get("/{tenant}/members")
async def members(tenant: UUID, user: dict = Depends(current_account)):
    async with identity_transaction() as conn:
        await org_access(conn, tenant, user)
        rows = await (
            await conn.execute(
                "SELECT m.user_id,u.name,u.email,m.role,m.status,m.created_at FROM atlas.memberships m JOIN atlas.users u ON u.id=m.user_id WHERE m.tenant_id=%s ORDER BY u.name",
                (tenant,),
            )
        ).fetchall()
    return {"items": rows}


@router.patch("/{tenant}/members/{member_id}")
async def edit_member(
    tenant: UUID, member_id: UUID, body: MemberEdit, user: dict = Depends(current_account)
):
    async with identity_transaction() as conn:
        actor = await org_access(conn, tenant, user, admin=True, lock=True)
        member = await (
            await conn.execute(
                "SELECT * FROM atlas.memberships WHERE tenant_id=%s AND user_id=%s",
                (tenant, member_id),
            )
        ).fetchone()
        if not member:
            raise HTTPException(404, "Member not found")
        if actor["role"] != "owner" and (
            member["role"] in {"owner", "admin"} or body.role in {"owner", "admin"}
        ):
            raise HTTPException(403, "Only owners can change administrator or owner roles")
        if body.role == "owner" or member["role"] == "owner":
            require_recent(user)
        await protect_last_owner(conn, tenant, member, body.role, body.status)
        await conn.execute(
            "UPDATE atlas.memberships SET role=coalesce(%s,role),status=coalesce(%s,status) WHERE tenant_id=%s AND user_id=%s",
            (body.role, body.status, tenant, member_id),
        )
    return {"message": "Membership updated"}


async def remove_member(conn, tenant, member_id, user, leaving=False):
    actor = await org_access(conn, tenant, user, admin=not leaving, lock=True)
    member = await (
        await conn.execute(
            "SELECT * FROM atlas.memberships WHERE tenant_id=%s AND user_id=%s", (tenant, member_id)
        )
    ).fetchone()
    if not member:
        raise HTTPException(404, "Member not found")
    if not leaving and actor["role"] != "owner" and member["role"] in {"owner", "admin"}:
        raise HTTPException(403, "Only owners can remove administrators or owners")
    if member["role"] == "owner":
        require_recent(user)
    await protect_last_owner(conn, tenant, member, removing=True)
    await conn.execute(
        "DELETE FROM atlas.resource_grants WHERE tenant_id=%s AND subject_type='user' AND subject_id=%s",
        (tenant, member_id),
    )
    await conn.execute(
        "DELETE FROM atlas.team_members WHERE tenant_id=%s AND user_id=%s", (tenant, member_id)
    )
    await conn.execute(
        "DELETE FROM atlas.memberships WHERE tenant_id=%s AND user_id=%s", (tenant, member_id)
    )


@router.delete("/{tenant}/members/{member_id}")
async def delete_member(tenant: UUID, member_id: UUID, user: dict = Depends(current_account)):
    async with identity_transaction() as conn:
        await remove_member(conn, tenant, member_id, user)
    return {"message": "Member removed"}


@router.post("/{tenant}/leave")
async def leave(tenant: UUID, user: dict = Depends(current_account)):
    async with identity_transaction() as conn:
        await remove_member(conn, tenant, user["id"], user, leaving=True)
    return {"message": "You have left the workspace"}


@router.post("/{tenant}/transfer-ownership")
async def transfer_ownership(
    tenant: UUID, body: TransferBody, user: dict = Depends(current_account)
):
    require_recent(user)
    if body.user_id == user["id"]:
        raise HTTPException(400, "Choose another active member")
    async with identity_transaction() as conn:
        await org_access(conn, tenant, user, owner=True, lock=True)
        target = await (
            await conn.execute(
                "SELECT user_id FROM atlas.memberships WHERE tenant_id=%s AND user_id=%s AND status='active'",
                (tenant, body.user_id),
            )
        ).fetchone()
        if not target:
            raise HTTPException(404, "Active member not found")
        await conn.execute(
            "UPDATE atlas.memberships SET role='owner' WHERE tenant_id=%s AND user_id=%s",
            (tenant, body.user_id),
        )
        await conn.execute(
            "UPDATE atlas.memberships SET role='admin' WHERE tenant_id=%s AND user_id=%s",
            (tenant, user["id"]),
        )
    return {"message": "Ownership transferred"}


async def deliver_invitation(conn, tenant, invitation, organization_name):
    raw = secrets.token_urlsafe(32)
    await conn.execute(
        "UPDATE atlas.invitations SET digest=%s,expires_at=%s WHERE id=%s AND tenant_id=%s",
        (digest(raw), datetime.now(UTC) + timedelta(days=7), invitation["id"], tenant),
    )
    await send_mail(
        invitation["email"],
        f"Join {organization_name} on Atlas",
        f"You have been invited as {invitation['role']} to {organization_name}.\n\nSign in or register using {invitation['email']}, then open:\n{settings.app_url.rstrip('/')}/invite?token={raw}\n\nThis invitation expires in seven days.",
    )


@router.get("/{tenant}/invitations")
async def invitations(tenant: UUID, user: dict = Depends(current_account)):
    async with identity_transaction() as conn:
        await org_access(conn, tenant, user, admin=True)
        rows = await (
            await conn.execute(
                "SELECT id,email,role,expires_at,accepted_at,revoked_at,created_at FROM atlas.invitations WHERE tenant_id=%s ORDER BY created_at DESC",
                (tenant,),
            )
        ).fetchall()
    return {"items": rows}


@router.post("/{tenant}/invitations", status_code=201)
async def invite(
    tenant: UUID, body: InvitationCreate, request: Request, user: dict = Depends(current_account)
):
    await throttle(request, "invite", str(user["id"]), 10)
    async with identity_transaction() as conn:
        org = await org_access(conn, tenant, user, admin=True, lock=True)
        if org["kind"] == "personal":
            raise HTTPException(409, "Personal workspaces cannot invite other members")
        if body.role == "admin" and org["role"] != "owner":
            raise HTTPException(403, "Only owners can invite administrators")
        await conn.execute(
            "UPDATE atlas.invitations SET revoked_at=now() WHERE tenant_id=%s AND email=%s AND accepted_at IS NULL AND revoked_at IS NULL",
            (tenant, body.email),
        )
        row = await (
            await conn.execute(
                "INSERT INTO atlas.invitations(id,tenant_id,email,role,digest,invited_by,expires_at) VALUES(%s,%s,%s,%s,%s,%s,now()+interval '7 days') RETURNING id,email,role,expires_at,created_at",
                (
                    uuid4(),
                    tenant,
                    body.email,
                    body.role,
                    digest(secrets.token_urlsafe(32)),
                    user["id"],
                ),
            )
        ).fetchone()
        await deliver_invitation(conn, tenant, row, org["name"])
    return {"invitation": row}


@router.post("/{tenant}/invitations/{invitation_id}/resend")
async def resend_invitation(
    tenant: UUID, invitation_id: UUID, request: Request, user: dict = Depends(current_account)
):
    await throttle(request, "invite", str(user["id"]), 10)
    async with identity_transaction() as conn:
        org = await org_access(conn, tenant, user, admin=True, lock=True)
        row = await (
            await conn.execute(
                "SELECT * FROM atlas.invitations WHERE id=%s AND tenant_id=%s AND accepted_at IS NULL AND revoked_at IS NULL",
                (invitation_id, tenant),
            )
        ).fetchone()
        if not row:
            raise HTTPException(404, "Pending invitation not found")
        if row["role"] == "admin" and org["role"] != "owner":
            raise HTTPException(403, "Only owners can resend administrator invitations")
        await deliver_invitation(conn, tenant, row, org["name"])
    return {"message": "Invitation resent"}


@router.delete("/{tenant}/invitations/{invitation_id}")
async def revoke_invitation(
    tenant: UUID, invitation_id: UUID, user: dict = Depends(current_account)
):
    async with identity_transaction() as conn:
        org = await org_access(conn, tenant, user, admin=True, lock=True)
        row = await (
            await conn.execute(
                "SELECT role FROM atlas.invitations WHERE id=%s AND tenant_id=%s",
                (invitation_id, tenant),
            )
        ).fetchone()
        if row and row["role"] == "admin" and org["role"] != "owner":
            raise HTTPException(403, "Only owners can revoke administrator invitations")
        await conn.execute(
            "UPDATE atlas.invitations SET revoked_at=now() WHERE id=%s AND tenant_id=%s AND accepted_at IS NULL",
            (invitation_id, tenant),
        )
    return {"message": "Invitation revoked"}


@router.get("/{tenant}/teams")
async def teams(tenant: UUID, user: dict = Depends(current_account)):
    async with identity_transaction() as conn:
        await org_access(conn, tenant, user)
        rows = await (
            await conn.execute(
                "SELECT t.id,t.name,t.created_at,coalesce(array_agg(m.user_id) FILTER(WHERE m.user_id IS NOT NULL),'{}'::uuid[]) AS members FROM atlas.teams t LEFT JOIN atlas.team_members m ON m.tenant_id=t.tenant_id AND m.team_id=t.id WHERE t.tenant_id=%s GROUP BY t.tenant_id,t.id ORDER BY t.name",
                (tenant,),
            )
        ).fetchall()
    return {"items": rows}


@router.post("/{tenant}/teams", status_code=201)
async def create_team(tenant: UUID, body: TeamBody, user: dict = Depends(current_account)):
    async with identity_transaction() as conn:
        await org_access(conn, tenant, user, admin=True, lock=True)
        existing = await (
            await conn.execute(
                "SELECT id FROM atlas.teams WHERE tenant_id=%s AND name=%s", (tenant, body.name)
            )
        ).fetchone()
        if existing:
            raise HTTPException(409, "A team with that name already exists")
        row = await (
            await conn.execute(
                "INSERT INTO atlas.teams(tenant_id,id,name) VALUES(%s,%s,%s) RETURNING id,name,created_at",
                (tenant, uuid4(), body.name.strip()),
            )
        ).fetchone()
    return {"team": row}


@router.patch("/{tenant}/teams/{team_id}")
async def edit_team(
    tenant: UUID, team_id: UUID, body: TeamBody, user: dict = Depends(current_account)
):
    async with identity_transaction() as conn:
        await org_access(conn, tenant, user, admin=True, lock=True)
        existing = await (
            await conn.execute(
                "SELECT id FROM atlas.teams WHERE tenant_id=%s AND name=%s AND id<>%s",
                (tenant, body.name, team_id),
            )
        ).fetchone()
        if existing:
            raise HTTPException(409, "A team with that name already exists")
        row = await (
            await conn.execute(
                "UPDATE atlas.teams SET name=%s WHERE tenant_id=%s AND id=%s RETURNING id,name,created_at",
                (body.name.strip(), tenant, team_id),
            )
        ).fetchone()
        if not row:
            raise HTTPException(404, "Team not found")
    return {"team": row}


@router.delete("/{tenant}/teams/{team_id}")
async def delete_team(tenant: UUID, team_id: UUID, user: dict = Depends(current_account)):
    async with identity_transaction() as conn:
        await org_access(conn, tenant, user, admin=True, lock=True)
        await conn.execute(
            "DELETE FROM atlas.resource_grants WHERE tenant_id=%s AND subject_type='team' AND subject_id=%s",
            (tenant, team_id),
        )
        await conn.execute(
            "DELETE FROM atlas.teams WHERE tenant_id=%s AND id=%s", (tenant, team_id)
        )
    return {"message": "Team deleted"}


@router.put("/{tenant}/teams/{team_id}/members/{member_id}")
async def add_team_member(
    tenant: UUID, team_id: UUID, member_id: UUID, user: dict = Depends(current_account)
):
    async with identity_transaction() as conn:
        await org_access(conn, tenant, user, admin=True, lock=True)
        member = await (
            await conn.execute(
                "SELECT user_id FROM atlas.memberships WHERE tenant_id=%s AND user_id=%s AND status='active'",
                (tenant, member_id),
            )
        ).fetchone()
        team = await (
            await conn.execute(
                "SELECT id FROM atlas.teams WHERE tenant_id=%s AND id=%s", (tenant, team_id)
            )
        ).fetchone()
        if not member or not team:
            raise HTTPException(404, "Active member or team not found")
        await conn.execute(
            "INSERT INTO atlas.team_members(tenant_id,team_id,user_id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
            (tenant, team_id, member_id),
        )
    return {"message": "Team member added"}


@router.delete("/{tenant}/teams/{team_id}/members/{member_id}")
async def delete_team_member(
    tenant: UUID, team_id: UUID, member_id: UUID, user: dict = Depends(current_account)
):
    async with identity_transaction() as conn:
        await org_access(conn, tenant, user, admin=True, lock=True)
        await conn.execute(
            "DELETE FROM atlas.team_members WHERE tenant_id=%s AND team_id=%s AND user_id=%s",
            (tenant, team_id, member_id),
        )
    return {"message": "Team member removed"}


class DeleteOrganization(BaseModel):
    confirmation: str = Field(min_length=3, max_length=60)


@router.delete("/{tenant}")
async def delete_organization(
    tenant: UUID, body: DeleteOrganization, user: dict = Depends(current_account)
):
    require_recent(user)
    async with identity_transaction() as conn:
        org = await org_access(conn, tenant, user, owner=True, lock=True)
        if body.confirmation != org["slug"]:
            raise HTTPException(400, "Type the workspace URL exactly to confirm deletion")
        await conn.execute(
            "UPDATE atlas.tenants SET status='suspended',auth_revision=auth_revision+1 WHERE id=%s",
            (tenant,),
        )
        await conn.execute(
            "UPDATE atlas.api_keys SET revoked_at=now() WHERE tenant_id=%s", (tenant,)
        )
        await conn.execute("DELETE FROM atlas.team_members WHERE tenant_id=%s", (tenant,))
        await conn.execute("DELETE FROM atlas.memberships WHERE tenant_id=%s", (tenant,))
        await conn.execute(
            "UPDATE atlas.invitations SET revoked_at=now() WHERE tenant_id=%s AND accepted_at IS NULL",
            (tenant,),
        )
    return {
        "message": "Workspace disabled and access revoked. Its retained data is scheduled for deletion after 30 days."
    }
