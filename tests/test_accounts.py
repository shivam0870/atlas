"""Real PostgreSQL, Redis and local SMTP tests (no mocked authentication paths)."""

import asyncio
import re
import secrets
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import psycopg
import pyotp
import pytest
import pytest_asyncio
from fastapi import FastAPI

from atlas.accounts import router as account_router
from atlas.auth import digest
from atlas.config import settings
from atlas.db import access_context, identity_pool
from atlas.organizations import router as organization_router

app = FastAPI()
app.include_router(account_router)
app.include_router(organization_router)


class Secret(str):
    def __repr__(self):
        return "<redacted test secret>"


@dataclass
class Account:
    email: str
    password: Secret
    client: httpx.AsyncClient
    user_id: UUID | None = None


async def inbox_token(email: str, purpose: str) -> Secret:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:58025", timeout=10) as client:
        for _ in range(20):
            result = await client.get("/api/v1/search", params={"query": f"to:{email}"})
            result.raise_for_status()
            for message in result.json().get("messages", []):
                full = (await client.get(f"/api/v1/message/{message['ID']}")).json()
                text = full.get("Text", "")
                match = re.search(rf"/{purpose}\?token=([A-Za-z0-9_-]+)", text)
                if match:
                    return Secret(match.group(1))
            await asyncio.sleep(0.1)
    raise AssertionError("Expected local email was not delivered")


@pytest_asyncio.fixture
async def account_factory():
    if identity_pool.closed:
        await identity_pool.open(wait=True)
    accounts = []

    async def create(verified=True, login=True):
        account = Account(
            f"test-{uuid4()}@example.test",
            Secret(secrets.token_urlsafe(24)),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app, client=(str(uuid4()), 123)),
                base_url="http://test",
                headers={"X-Atlas-Client": "console"},
            ),
        )
        accounts.append(account)
        response = await account.client.post(
            "/api/auth/register",
            json={"name": "Test member", "email": account.email, "password": account.password},
        )
        assert response.status_code == 202, response.text
        with psycopg.connect(settings.database_admin_url) as conn:
            account.user_id = conn.execute(
                "SELECT id FROM atlas.users WHERE email=%s", (account.email,)
            ).fetchone()[0]
        if verified:
            token = await inbox_token(account.email, "verify-email")
            assert (
                await account.client.post("/api/auth/verify", json={"token": token})
            ).status_code == 200
        if login and verified:
            response = await account.client.post(
                "/api/auth/login", json={"email": account.email, "password": account.password}
            )
            assert response.status_code == 200, response.text
        return account

    yield create
    access_context.set(None)
    for account in accounts:
        await account.client.aclose()
    ids = [a.user_id for a in accounts if a.user_id]
    if ids:
        with psycopg.connect(settings.database_admin_url) as conn:
            tenants = conn.execute(
                "SELECT DISTINCT tenant_id FROM atlas.memberships WHERE user_id=ANY(%s)", (ids,)
            ).fetchall()
            for (tenant,) in tenants:
                conn.execute(
                    "UPDATE atlas.documents SET current_version_id=NULL,pending_version_id=NULL WHERE tenant_id=%s",
                    (tenant,),
                )
                for table in [
                    "bookmarks",
                    "outbox",
                    "ingestion_jobs",
                    "messages",
                    "conversations",
                    "queries",
                    "evaluation_jobs",
                    "eval_runs",
                    "eval_labels",
                    "feedback",
                    "entities",
                    "embeddings",
                    "chunk_terms",
                    "chunks",
                    "document_versions",
                    "documents",
                    "collections",
                    "embedding_cache",
                    "access_requests",
                    "api_keys",
                    "resource_grants",
                    "service_accounts",
                    "usage_ledger",
                    "reservations",
                    "budget_periods",
                    "tenant_limits",
                    "audit_events",
                    "notifications",
                ]:
                    conn.execute(f"DELETE FROM atlas.{table} WHERE tenant_id=%s", (tenant,))
                conn.execute("DELETE FROM atlas.invitations WHERE tenant_id=%s", (tenant,))
                conn.execute("DELETE FROM atlas.spaces WHERE tenant_id=%s", (tenant,))
                conn.execute("DELETE FROM atlas.tenants WHERE id=%s", (tenant,))
            conn.execute("DELETE FROM atlas.invitations WHERE invited_by=ANY(%s)", (ids,))
            conn.execute("DELETE FROM atlas.users WHERE id=ANY(%s)", (ids,))


@pytest.mark.integration
async def test_registration_verification_single_use_and_duplicate(account_factory):
    account = await account_factory(verified=False, login=False)
    client = account.client
    invalid = await client.post(
        "/api/auth/login", json={"email": account.email, "password": account.password}
    )
    assert invalid.status_code == 403
    token = await inbox_token(account.email, "verify-email")
    assert (await client.post("/api/auth/verify", json={"token": token})).status_code == 200
    assert (await client.post("/api/auth/verify", json={"token": token})).status_code == 400
    duplicate = await client.post(
        "/api/auth/register",
        json={
            "name": "Another name",
            "email": account.email.upper(),
            "password": Secret(secrets.token_urlsafe(24)),
        },
    )
    assert duplicate.status_code == 202
    response = await client.post(
        "/api/auth/login", json={"email": account.email, "password": account.password}
    )
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    with psycopg.connect(settings.database_admin_url) as conn:
        (password_hash,) = conn.execute(
            "SELECT password_hash FROM atlas.users WHERE id=%s", (account.user_id,)
        ).fetchone()
        (session_hash,) = conn.execute(
            "SELECT digest FROM atlas.user_sessions WHERE user_id=%s AND revoked_at IS NULL",
            (account.user_id,),
        ).fetchone()
        assert password_hash.startswith("$argon2id$")
        assert session_hash == digest(client.cookies["atlas_user_session"])


@pytest.mark.integration
async def test_reset_revokes_every_session_and_rejects_replay(account_factory):
    account = await account_factory()
    old_cookie = Secret(account.client.cookies["atlas_user_session"])
    assert (
        await account.client.post("/api/auth/forgot-password", json={"email": account.email})
    ).status_code == 202
    token = await inbox_token(account.email, "reset-password")
    new_password = Secret(secrets.token_urlsafe(24))
    assert (
        await account.client.post(
            "/api/auth/reset-password", json={"token": token, "password": new_password}
        )
    ).status_code == 200
    assert (
        await account.client.post(
            "/api/auth/reset-password", json={"token": token, "password": new_password}
        )
    ).status_code == 400
    account.client.cookies.set("atlas_user_session", old_cookie)
    assert (await account.client.get("/api/auth/me")).status_code == 401
    account.client.cookies.clear()
    assert (
        await account.client.post(
            "/api/auth/login", json={"email": account.email, "password": account.password}
        )
    ).status_code == 401
    assert (
        await account.client.post(
            "/api/auth/login", json={"email": account.email, "password": new_password}
        )
    ).status_code == 200


@pytest.mark.integration
async def test_mfa_encrypted_secret_totp_and_recovery_replay(account_factory):
    account = await account_factory()
    response = await account.client.post("/api/auth/mfa/enroll")
    assert response.status_code == 200
    secret = Secret(response.json()["secret"])
    code = Secret(pyotp.TOTP(secret).now())
    confirmed = await account.client.post("/api/auth/mfa/confirm", json={"code": code})
    assert confirmed.status_code == 200, confirmed.text
    recovery = [Secret(c) for c in confirmed.json()["recovery_codes"]]
    with psycopg.connect(settings.database_admin_url) as conn:
        row = conn.execute(
            "SELECT mfa_secret,mfa_pending_secret FROM atlas.users WHERE id=%s", (account.user_id,)
        ).fetchone()
        assert row[0] != secret and row[1] is None
        stored = conn.execute(
            "SELECT digest FROM atlas.recovery_codes WHERE user_id=%s", (account.user_id,)
        ).fetchall()
        assert digest(recovery[0]) in [r[0] for r in stored]
    await account.client.post("/api/auth/logout")
    challenge = (
        await account.client.post(
            "/api/auth/login", json={"email": account.email, "password": account.password}
        )
    ).json()
    assert challenge["mfa_required"]
    # Enrollment consumed the current TOTP step; replay must fail.
    assert (
        await account.client.post(
            "/api/auth/mfa/login", json={"challenge": challenge["challenge"], "code": code}
        )
    ).status_code == 401
    assert (
        await account.client.post(
            "/api/auth/mfa/login", json={"challenge": challenge["challenge"], "code": recovery[0]}
        )
    ).status_code == 200
    assert (
        await account.client.post(
            "/api/auth/mfa/login", json={"challenge": challenge["challenge"], "code": recovery[1]}
        )
    ).status_code == 400
    await account.client.post("/api/auth/logout")
    challenge = (
        await account.client.post(
            "/api/auth/login", json={"email": account.email, "password": account.password}
        )
    ).json()
    assert (
        await account.client.post(
            "/api/auth/mfa/login", json={"challenge": challenge["challenge"], "code": recovery[0]}
        )
    ).status_code == 401
    assert (
        await account.client.post(
            "/api/auth/mfa/login", json={"challenge": challenge["challenge"], "code": recovery[1]}
        )
    ).status_code == 200


@pytest.mark.integration
async def test_idle_expiry_and_session_revocation(account_factory):
    account = await account_factory()
    response = await account.client.get("/api/auth/sessions")
    assert len(response.json()["items"]) == 1
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "UPDATE atlas.user_sessions SET last_seen_at=now()-interval '2 days' WHERE user_id=%s",
            (account.user_id,),
        )
    assert (await account.client.get("/api/auth/me")).status_code == 401
    await account.client.post(
        "/api/auth/login", json={"email": account.email, "password": account.password}
    )
    assert (await account.client.post("/api/auth/logout-all")).status_code == 200
    assert (await account.client.get("/api/auth/me")).status_code == 401


@pytest.mark.integration
async def test_auth_rate_limit_and_generic_recovery(account_factory):
    account = await account_factory()
    first = await account.client.post("/api/auth/forgot-password", json={"email": account.email})
    unknown = await account.client.post(
        "/api/auth/forgot-password", json={"email": f"missing-{uuid4()}@example.test"}
    )
    assert first.status_code == unknown.status_code == 202
    assert first.json() == unknown.json()
    repeated = await account.client.post("/api/auth/forgot-password", json={"email": account.email})
    assert repeated.status_code == 429 and int(repeated.headers["retry-after"]) > 0


@pytest.mark.integration
async def test_profile_email_change_password_change_and_account_deletion(account_factory):
    account = await account_factory()
    updated = await account.client.patch(
        "/api/auth/profile", json={"name": "Updated name", "theme": "dark"}
    )
    assert updated.status_code == 200
    assert updated.json()["user"]["name"] == "Updated name"
    new_email = f"changed-{uuid4()}@example.test"
    response = await account.client.post(
        "/api/auth/email", json={"email": new_email, "password": account.password}
    )
    assert response.status_code == 202
    token = await inbox_token(new_email, "verify-email-change")
    assert (
        await account.client.post("/api/auth/verify-email-change", json={"token": token})
    ).status_code == 200
    assert (await account.client.get("/api/auth/me")).status_code == 401
    assert (
        await account.client.post(
            "/api/auth/login", json={"email": new_email, "password": account.password}
        )
    ).status_code == 200
    new_password = Secret(secrets.token_urlsafe(24))
    changed = await account.client.post(
        "/api/auth/password",
        json={"current_password": account.password, "new_password": new_password},
    )
    assert changed.status_code == 200
    assert (await account.client.get("/api/auth/me")).status_code == 200
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "UPDATE atlas.user_sessions SET reauthenticated_at=now()-interval '10 minutes' WHERE user_id=%s",
            (account.user_id,),
        )
    assert (
        await account.client.request("DELETE", "/api/auth/account", json={"confirmation": "DELETE"})
    ).status_code == 403
    assert (
        await account.client.post("/api/auth/reauthenticate", json={"password": new_password})
    ).status_code == 200
    assert (
        await account.client.request("DELETE", "/api/auth/account", json={"confirmation": "DELETE"})
    ).status_code == 200
    assert (
        await account.client.post(
            "/api/auth/login", json={"email": new_email, "password": new_password}
        )
    ).status_code == 401


@pytest.mark.integration
async def test_expired_verification_and_unknown_session(account_factory):
    account = await account_factory(verified=False, login=False)
    token = await inbox_token(account.email, "verify-email")
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "UPDATE atlas.account_tokens SET expires_at=now()-interval '1 minute' WHERE user_id=%s",
            (account.user_id,),
        )
    assert (await account.client.post("/api/auth/verify", json={"token": token})).status_code == 400
    account.client.cookies.set("atlas_user_session", Secret(secrets.token_urlsafe(48)))
    assert (await account.client.get("/api/auth/me")).status_code == 401
