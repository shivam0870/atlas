"""Real organization authorization and concurrent last-owner tests."""

import asyncio
from uuid import uuid4

import psycopg
import pytest
from test_accounts import account_factory as _account_factory
from test_accounts import inbox_token

from atlas.config import settings

account_factory = _account_factory


async def company(account):
    response = await account.client.post(
        "/api/organizations", json={"name": "Test company", "slug": f"test-{uuid4()}"}
    )
    assert response.status_code == 201, response.text
    return response.json()["organization"]


async def invitation(owner, org, member, role="viewer"):
    response = await owner.client.post(
        f"/api/organizations/{org['id']}/invitations", json={"email": member.email, "role": role}
    )
    assert response.status_code == 201, response.text
    token = await inbox_token(member.email, "invite")
    return token


@pytest.mark.integration
async def test_create_is_idempotent_invite_email_role_and_company_isolation(account_factory):
    owner = await account_factory()
    member = await account_factory()
    outsider = await account_factory()
    org = await company(owner)
    duplicate = await owner.client.post(
        "/api/organizations", json={"name": org["name"], "slug": org["slug"]}
    )
    assert duplicate.json()["organization"]["id"] == org["id"]
    token = await invitation(owner, org, member)
    assert (
        await outsider.client.post("/api/organizations/invitations/accept", json={"token": token})
    ).status_code == 403
    accepted = await member.client.post(
        "/api/organizations/invitations/accept", json={"token": token}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["organization"]["role"] == "viewer"
    assert (
        await member.client.post("/api/organizations/invitations/accept", json={"token": token})
    ).status_code == 200
    assert (await outsider.client.get(f"/api/organizations/{org['id']}/members")).status_code == 404
    assert (
        await member.client.patch(
            f"/api/organizations/{org['id']}/members/{member.user_id}", json={"role": "owner"}
        )
    ).status_code == 403
    assert (
        await member.client.post(
            f"/api/organizations/{org['id']}/teams", json={"name": "Forbidden"}
        )
    ).status_code == 403
    assert (
        await owner.client.delete(f"/api/organizations/{org['id']}/members/{owner.user_id}")
    ).status_code == 409
    # Old invitation cannot restore a removed membership.
    assert (
        await owner.client.delete(f"/api/organizations/{org['id']}/members/{member.user_id}")
    ).status_code == 200
    assert (
        await member.client.post("/api/organizations/invitations/accept", json={"token": token})
    ).status_code == 400


@pytest.mark.integration
async def test_pending_invitation_inbox_accepts_only_matching_verified_account(account_factory):
    owner, member, outsider = [await account_factory() for _ in range(3)]
    org = await company(owner)
    await invitation(owner, org, member)
    pending = (await member.client.get("/api/organizations/pending-invitations")).json()["items"]
    assert len(pending) == 1 and pending[0]["tenant_id"] == org["id"]
    assert (await outsider.client.get("/api/organizations/pending-invitations")).json() == {
        "items": []
    }
    accept_url = f"/api/organizations/pending-invitations/{pending[0]['id']}/accept"
    assert (await outsider.client.post(accept_url)).status_code == 403
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute("UPDATE atlas.users SET email_verified_at=NULL WHERE id=%s", (member.user_id,))
    assert (await member.client.get("/api/organizations/pending-invitations")).status_code == 403
    assert (await member.client.post(accept_url)).status_code == 403
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "UPDATE atlas.users SET email_verified_at=now() WHERE id=%s", (member.user_id,)
        )
    accepted = await member.client.post(accept_url)
    assert accepted.status_code == 200 and accepted.json()["organization"]["role"] == "viewer"
    assert (await member.client.post(accept_url)).status_code == 200
    assert (await member.client.get("/api/organizations/pending-invitations")).json() == {
        "items": []
    }
    await owner.client.delete(f"/api/organizations/{org['id']}/members/{member.user_id}")
    assert (await member.client.post(accept_url)).status_code == 400
    for field in ["expires_at", "revoked_at"]:
        invited = await owner.client.post(
            f"/api/organizations/{org['id']}/invitations", json={"email": member.email}
        )
        assert invited.status_code == 201, invited.text
        pending = (await member.client.get("/api/organizations/pending-invitations")).json()[
            "items"
        ]
        invitation_id = pending[0]["id"]
        with psycopg.connect(settings.database_admin_url) as conn:
            if field == "expires_at":
                conn.execute(
                    "UPDATE atlas.invitations SET expires_at=now()-interval '1 second' WHERE id=%s",
                    (invitation_id,),
                )
            else:
                conn.execute(
                    "UPDATE atlas.invitations SET revoked_at=now() WHERE id=%s", (invitation_id,)
                )
        assert (await member.client.get("/api/organizations/pending-invitations")).json() == {
            "items": []
        }
        assert (
            await member.client.post(
                f"/api/organizations/pending-invitations/{invitation_id}/accept"
            )
        ).status_code == 400


@pytest.mark.integration
async def test_concurrent_owner_departures_leave_one_owner(account_factory):
    first = await account_factory()
    second = await account_factory()
    org = await company(first)
    token = await invitation(first, org, second, "editor")
    await second.client.post("/api/organizations/invitations/accept", json={"token": token})
    response = await first.client.patch(
        f"/api/organizations/{org['id']}/members/{second.user_id}", json={"role": "owner"}
    )
    assert response.status_code == 200
    responses = await asyncio.gather(
        first.client.post(f"/api/organizations/{org['id']}/leave"),
        second.client.post(f"/api/organizations/{org['id']}/leave"),
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    with psycopg.connect(settings.database_admin_url) as conn:
        (count,) = conn.execute(
            "SELECT count(*) FROM atlas.memberships WHERE tenant_id=%s AND role='owner' AND status='active'",
            (org["id"],),
        ).fetchone()
        assert count == 1


@pytest.mark.integration
async def test_admin_cannot_escalate_team_revocation_updates_revision(account_factory):
    owner = await account_factory()
    admin = await account_factory()
    org = await company(owner)
    token = await invitation(owner, org, admin, "admin")
    await admin.client.post("/api/organizations/invitations/accept", json={"token": token})
    assert (
        await admin.client.patch(
            f"/api/organizations/{org['id']}/members/{admin.user_id}", json={"role": "owner"}
        )
    ).status_code == 403
    assert (
        await admin.client.post(
            f"/api/organizations/{org['id']}/invitations",
            json={"email": "other@example.test", "role": "admin"},
        )
    ).status_code == 403
    created = await admin.client.post(
        f"/api/organizations/{org['id']}/teams", json={"name": "Platform"}
    )
    assert created.status_code == 201
    team = created.json()["team"]["id"]
    before = (await admin.client.get("/api/organizations")).json()["items"][0]["auth_revision"]
    assert (
        await admin.client.put(
            f"/api/organizations/{org['id']}/teams/{team}/members/{admin.user_id}"
        )
    ).status_code == 200
    after = (await admin.client.get("/api/organizations")).json()["items"][0]["auth_revision"]
    assert after > before
    assert (
        await owner.client.patch(
            f"/api/organizations/{org['id']}/members/{admin.user_id}", json={"status": "suspended"}
        )
    ).status_code == 200
    assert (await admin.client.get("/api/organizations")).json()["items"] == []
    assert (await admin.client.get(f"/api/organizations/{org['id']}/members")).status_code == 404


@pytest.mark.integration
async def test_removed_member_loses_direct_grants_and_audit_is_metadata_only(account_factory):
    owner = await account_factory()
    member = await account_factory()
    org = await company(owner)
    token = await invitation(owner, org, member)
    assert (
        await member.client.post("/api/organizations/invitations/accept", json={"token": token})
    ).status_code == 200
    with psycopg.connect(settings.database_admin_url) as conn:
        space = conn.execute(
            "SELECT id FROM atlas.spaces WHERE tenant_id=%s", (org["id"],)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO atlas.resource_grants(tenant_id,id,space_id,subject_type,subject_id,permission) VALUES(%s,%s,%s,'user',%s,'read')",
            (org["id"], uuid4(), space, member.user_id),
        )
    assert (
        await owner.client.delete(f"/api/organizations/{org['id']}/members/{member.user_id}")
    ).status_code == 200
    with psycopg.connect(settings.database_admin_url) as conn:
        remaining = conn.execute(
            "SELECT count(*) FROM atlas.resource_grants WHERE tenant_id=%s AND subject_type='user' AND subject_id=%s",
            (org["id"], member.user_id),
        ).fetchone()[0]
        assert remaining == 0
    assert (
        await owner.client.post("/api/auth/reauthenticate", json={"password": owner.password})
    ).status_code == 200
    with psycopg.connect(settings.database_admin_url) as conn:
        events = conn.execute(
            "SELECT actor_id,action,metadata FROM atlas.audit_events WHERE tenant_id=%s AND action='security.reauthenticated'",
            (org["id"],),
        ).fetchall()
        assert events and events[0][0] == owner.user_id
        assert all(event[2] == {} for event in events)
        assert owner.password not in str(events)


@pytest.mark.integration
async def test_local_claim_requires_verified_owner_preserves_documents_and_revokes_keys(
    account_factory,
):
    import json
    import secrets
    import sys

    from atlas.auth import digest

    owner = await account_factory()
    unverified = await account_factory(verified=False, login=False)
    other = await account_factory()
    tenant, document_id = uuid4(), uuid4()
    slug = f"legacy-{tenant}"
    legacy_key = "atl_" + secrets.token_urlsafe(32)
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "INSERT INTO atlas.tenants(id,name,slug) VALUES(%s,'Preserved legacy workspace',%s)",
            (tenant, slug),
        )
        conn.execute(
            "INSERT INTO atlas.api_keys(id,tenant_id,digest,prefix,scopes) VALUES(%s,%s,%s,%s,ARRAY['read','query'])",
            (uuid4(), tenant, digest(legacy_key), legacy_key[:10]),
        )
        conn.execute(
            "INSERT INTO atlas.documents(tenant_id,id,title,source_key,content_hash,content) VALUES(%s,%s,'Preserved document','claim-fixture','fixture-hash','Original preserved content')",
            (tenant, document_id),
        )

    async def claim(email):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "scripts/claim_workspace.py",
            "--tenant",
            slug,
            "--email",
            email,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        assert legacy_key.encode() not in stdout + stderr
        return process.returncode, stdout.decode(), stderr.decode()

    try:
        blocked = await owner.client.post(
            "/api/organizations", json={"name": "Attempted claim", "slug": slug}
        )
        assert blocked.status_code == 409
        status, _, message = await claim(unverified.email)
        assert status != 0 and "verified" in message
        status, stdout, _ = await claim(owner.email)
        assert status == 0
        assert json.loads(stdout)["revoked_legacy_keys"] == 1
        status, stdout, _ = await claim(owner.email)
        assert status == 0 and json.loads(stdout)["status"] == "already_claimed"
        status, _, message = await claim(other.email)
        assert status != 0 and "already claimed" in message
        with psycopg.connect(settings.database_admin_url) as conn:
            assert (
                conn.execute(
                    "SELECT content FROM atlas.documents WHERE tenant_id=%s AND id=%s",
                    (tenant, document_id),
                ).fetchone()[0]
                == "Original preserved content"
            )
            assert conn.execute(
                "SELECT user_id,role FROM atlas.memberships WHERE tenant_id=%s", (tenant,)
            ).fetchone() == (owner.user_id, "owner")
            assert (
                conn.execute(
                    "SELECT count(*) FROM atlas.api_keys WHERE tenant_id=%s AND revoked_at IS NULL",
                    (tenant,),
                ).fetchone()[0]
                == 0
            )
    finally:
        with psycopg.connect(settings.database_admin_url) as conn:
            conn.execute("DELETE FROM atlas.documents WHERE tenant_id=%s", (tenant,))
