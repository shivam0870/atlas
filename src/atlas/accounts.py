"""Personal accounts: opaque sessions, single-use email tokens, and optional TOTP MFA."""

import asyncio
import re
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from atlas.auth import Identity, digest
from atlas.config import settings
from atlas.db import bind_identity, identity_transaction
from atlas.mail import send_mail
from atlas.serving import RATE_SCRIPT, redis

router = APIRouter(prefix="/api/auth", tags=["accounts"])
password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(32))
password_slots = asyncio.Semaphore(4)
COOKIE = "atlas_user_session"
GENERIC_MAIL = {"message": "If this address is eligible, an email has been sent. Check your inbox."}


def normalize_email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("Enter a valid email address")
    return value


class EmailBody(BaseModel):
    email: str

    _email = field_validator("email")(normalize_email)


def nonempty_name(value):
    if value is not None and not value.strip():
        raise ValueError("Name must contain a visible character")
    return value.strip() if value is not None else None


class Register(EmailBody):
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=128)
    _name = field_validator("name")(nonempty_name)


class Login(EmailBody):
    password: str = Field(min_length=1, max_length=128)
    code: str | None = Field(default=None, max_length=64)


class TokenBody(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class Reset(TokenBody):
    password: str = Field(min_length=12, max_length=128)


class Reauthenticate(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    code: str | None = Field(default=None, max_length=64)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)
    code: str | None = Field(default=None, max_length=64)


class EmailChange(EmailBody, Reauthenticate):
    pass


class Profile(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    theme: Literal["light", "dark", "system"] | None = None
    _name = field_validator("name")(nonempty_name)


class Code(BaseModel):
    code: str = Field(min_length=6, max_length=64)


class MFALogin(Code):
    challenge: str = Field(min_length=20, max_length=200)


def public_user(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "email_verified": row["email_verified_at"] is not None,
        "mfa_enabled": bool(row["mfa_secret"]),
        "theme": row["theme"],
    }


async def throttle(request: Request, category: str, address: str = "", maximum: int = 10):
    peer = request.client.host if request.client else "unknown"
    # Both per-address and per-peer limits: random addresses cannot bypass the IP limit.
    for bucket, value, limit in [
        ("peer", peer, maximum * 3),
        ("subject", address or peer, maximum),
    ]:
        try:
            allowed, retry = await redis.eval(
                RATE_SCRIPT,
                1,
                f"atlas:auth:{category}:{bucket}:{digest(value)}",
                limit,
                str(uuid4()),
            )
        except Exception as exc:
            raise HTTPException(503, "Authentication is temporarily unavailable") from exc
        if not allowed:
            raise HTTPException(
                429,
                "Too many attempts. Please try again shortly.",
                headers={"Retry-After": str(retry)},
            )


async def check_password(password: str, hashed: str) -> bool:
    def check():
        try:
            return password_hasher.verify(hashed, password)
        except VerificationError:
            return False

    async with password_slots:
        return await asyncio.to_thread(check)


async def hash_password(password: str) -> str:
    async with password_slots:
        return await asyncio.to_thread(password_hasher.hash, password)


def cipher() -> Fernet:
    if not settings.mfa_encryption_key:
        raise HTTPException(503, "MFA encryption is not configured")
    return Fernet(settings.mfa_encryption_key.encode())


async def consume_mfa(conn, user: dict, code: str | None) -> bool:
    if not user["mfa_secret"]:
        return True
    if not code:
        return False
    secret = cipher().decrypt(user["mfa_secret"].encode()).decode()
    totp = pyotp.TOTP(secret)
    current = int(time.time()) // 30
    for step in [current, current - 1, current + 1]:
        if step > user["mfa_last_step"] and secrets.compare_digest(totp.at(step * 30), code):
            await conn.execute(
                "UPDATE atlas.users SET mfa_last_step=%s WHERE id=%s", (step, user["id"])
            )
            return True
    row = await (
        await conn.execute(
            "UPDATE atlas.recovery_codes SET used_at=now() WHERE user_id=%s AND digest=%s AND used_at IS NULL RETURNING digest",
            (user["id"], digest(code.strip().upper())),
        )
    ).fetchone()
    return bool(row)


async def credentials(conn, user: dict, password: str, code: str | None):
    if not await check_password(password, user["password_hash"]) or not await consume_mfa(
        conn, user, code
    ):
        raise HTTPException(401, "Password or authentication code is incorrect")


async def token_for(conn, user_id: UUID, purpose: str, payload: dict | None = None, minutes=30):
    raw = secrets.token_urlsafe(32)
    await conn.execute(
        "UPDATE atlas.account_tokens SET used_at=now() WHERE user_id=%s AND purpose=%s AND used_at IS NULL",
        (user_id, purpose),
    )
    await conn.execute(
        "INSERT INTO atlas.account_tokens(id,user_id,digest,purpose,payload,expires_at) VALUES(%s,%s,%s,%s,%s,%s)",
        (
            uuid4(),
            user_id,
            digest(raw),
            purpose,
            Jsonb(payload or {}),
            datetime.now(UTC) + timedelta(minutes=minutes),
        ),
    )
    return raw


async def take_token(conn, raw: str, purpose: str):
    # All account token consumers lock the user before token mutation, matching producers.
    candidate = await (
        await conn.execute(
            "SELECT user_id FROM atlas.account_tokens WHERE digest=%s AND purpose=%s",
            (digest(raw), purpose),
        )
    ).fetchone()
    if not candidate:
        raise HTTPException(400, "This link is invalid, expired, or already used")
    active = await (
        await conn.execute(
            "SELECT id FROM atlas.users WHERE id=%s AND disabled_at IS NULL FOR UPDATE",
            (candidate["user_id"],),
        )
    ).fetchone()
    if not active:
        raise HTTPException(400, "This link is invalid, expired, or already used")
    row = await (
        await conn.execute(
            "UPDATE atlas.account_tokens SET used_at=now() WHERE digest=%s AND purpose=%s AND used_at IS NULL AND expires_at>now() RETURNING *",
            (digest(raw), purpose),
        )
    ).fetchone()
    if not row:
        raise HTTPException(400, "This link is invalid, expired, or already used")
    return row


async def email_token(conn, user: dict, purpose: str, destination: str | None = None):
    raw = await token_for(conn, user["id"], purpose, {"email": destination} if destination else {})
    page = {
        "verify": "verify-email",
        "reset": "reset-password",
        "email_change": "verify-email-change",
    }[purpose]
    await send_mail(
        destination or user["email"],
        "Atlas: " + page.replace("-", " "),
        f"Open this link to {page.replace('-', ' ')}:\n{settings.app_url.rstrip('/')}/{page}?token={raw}\n\nThis link expires in 30 minutes and can be used once. If you did not request this, ignore it.",
    )


async def security_event(conn, user_id: UUID, action: str, resource_id: UUID | None = None):
    """Company audit gets event metadata only; account secrets and request payloads stay private."""
    await conn.execute(
        "INSERT INTO atlas.audit_events(tenant_id,id,actor_id,actor_kind,action,resource_type,resource_id) SELECT tenant_id,gen_random_uuid(),%s,'user',%s,'account',%s FROM atlas.memberships WHERE user_id=%s AND status='active'",
        (user_id, action, resource_id or user_id, user_id),
    )


async def new_session(conn, user: dict, request: Request, response: Response):
    raw = secrets.token_urlsafe(48)
    session_id = uuid4()
    expires = datetime.now(UTC) + timedelta(seconds=settings.session_absolute_seconds)
    await conn.execute(
        "INSERT INTO atlas.user_sessions(id,user_id,digest,expires_at,user_agent,reauthenticated_at) VALUES(%s,%s,%s,%s,%s,now())",
        (session_id, user["id"], digest(raw), expires, request.headers.get("user-agent", "")[:300]),
    )
    await security_event(conn, user["id"], "security.session_created", session_id)
    # A successful login rotates any existing browser session.
    existing = request.cookies.get(COOKIE)
    if existing:
        await conn.execute(
            "UPDATE atlas.user_sessions SET revoked_at=now() WHERE digest=%s", (digest(existing),)
        )
    response.set_cookie(
        COOKIE,
        raw,
        httponly=True,
        secure=settings.app_url.startswith("https://"),
        samesite="strict",
        max_age=settings.session_absolute_seconds,
        path="/",
    )
    response.delete_cookie("atlas_session")
    return {"user": public_user(user), "session_id": session_id}


async def current_account(request: Request):
    raw = request.cookies.get(COOKIE, "")
    if not raw or len(raw) > 200:
        raise HTTPException(401, "Sign in to continue")
    async with identity_transaction() as conn:
        row = await (
            await conn.execute(
                """UPDATE atlas.user_sessions s SET last_seen_at=now()
            FROM atlas.users u WHERE s.digest=%s AND s.user_id=u.id AND s.revoked_at IS NULL
            AND s.expires_at>now() AND s.last_seen_at>now()-(%s * interval '1 second')
            AND u.disabled_at IS NULL RETURNING u.*,s.id AS session_id,s.reauthenticated_at""",
                (digest(raw), settings.session_idle_seconds),
            )
        ).fetchone()
    if not row:
        raise HTTPException(401, "Your session has expired. Sign in again.")
    request.state.account = row
    bind_identity(
        Identity(
            UUID(int=0),
            row["session_id"],
            row["name"],
            [],
            user_id=row["id"],
            principal_kind="user",
            principal_id=row["id"],
            session_id=row["session_id"],
        )
    )
    return row


def require_verified(user: dict):
    if not user["email_verified_at"]:
        raise HTTPException(403, "Verify your email before continuing")


def require_recent(user: dict):
    if not user.get("reauthenticated_at") or user["reauthenticated_at"] < datetime.now(
        UTC
    ) - timedelta(minutes=5):
        raise HTTPException(
            403,
            "Please reauthenticate before this security change",
            headers={"X-Atlas-Reauthenticate": "required"},
        )


@router.post("/register", status_code=202)
async def register(body: Register, request: Request):
    await throttle(request, "register", body.email, 3)
    hashed = await hash_password(body.password)
    async with identity_transaction() as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (body.email,))
        user = await (
            await conn.execute(
                "INSERT INTO atlas.users(id,email,name,password_hash) VALUES(%s,%s,%s,%s) ON CONFLICT(email) DO NOTHING RETURNING *",
                (uuid4(), body.email, body.name.strip(), hashed),
            )
        ).fetchone()
        if user:
            await email_token(conn, user, "verify")
    return GENERIC_MAIL


@router.post("/verify")
async def verify(body: TokenBody, request: Request):
    await throttle(request, "verify", maximum=15)
    async with identity_transaction() as conn:
        token = await take_token(conn, body.token, "verify")
        await conn.execute(
            "UPDATE atlas.users SET email_verified_at=now(),updated_at=now() WHERE id=%s AND disabled_at IS NULL",
            (token["user_id"],),
        )
    return {"message": "Email verified. You can now sign in."}


@router.post("/resend-verification", status_code=202)
async def resend(body: EmailBody, request: Request):
    await throttle(request, "email", body.email, 1)
    async with identity_transaction() as conn:
        user = await (
            await conn.execute(
                "SELECT * FROM atlas.users WHERE email=%s AND email_verified_at IS NULL AND disabled_at IS NULL FOR UPDATE",
                (body.email,),
            )
        ).fetchone()
        if user:
            await email_token(conn, user, "verify")
    return GENERIC_MAIL


@router.post("/forgot-password", status_code=202)
async def forgot(body: EmailBody, request: Request):
    await throttle(request, "email", body.email, 1)
    async with identity_transaction() as conn:
        user = await (
            await conn.execute(
                "SELECT * FROM atlas.users WHERE email=%s AND disabled_at IS NULL FOR UPDATE",
                (body.email,),
            )
        ).fetchone()
        if user:
            await email_token(conn, user, "reset")
    return GENERIC_MAIL


@router.post("/reset-password")
async def reset(body: Reset, request: Request, response: Response):
    await throttle(request, "reset", maximum=10)
    hashed = await hash_password(body.password)
    async with identity_transaction() as conn:
        token = await take_token(conn, body.token, "reset")
        await conn.execute(
            "UPDATE atlas.users SET password_hash=%s,updated_at=now() WHERE id=%s",
            (hashed, token["user_id"]),
        )
        await conn.execute(
            "UPDATE atlas.user_sessions SET revoked_at=now() WHERE user_id=%s", (token["user_id"],)
        )
        await conn.execute(
            "UPDATE atlas.account_tokens SET used_at=now() WHERE user_id=%s AND used_at IS NULL",
            (token["user_id"],),
        )
        await security_event(conn, token["user_id"], "security.password_reset")
    response.delete_cookie(COOKIE)
    return {"message": "Password reset. Sign in with your new password."}


@router.post("/login")
async def login(body: Login, request: Request, response: Response):
    await throttle(request, "login", body.email)
    async with identity_transaction() as conn:
        user = await (
            await conn.execute("SELECT * FROM atlas.users WHERE email=%s FOR UPDATE", (body.email,))
        ).fetchone()
        valid = await check_password(body.password, user["password_hash"] if user else DUMMY_HASH)
        if not user or not valid or user["disabled_at"]:
            raise HTTPException(401, "Email or password is incorrect")
        require_verified(user)
        if user["mfa_secret"]:
            if not body.code:
                challenge = await token_for(conn, user["id"], "mfa_login", minutes=5)
                return {"mfa_required": True, "challenge": challenge}
            if not await consume_mfa(conn, user, body.code):
                raise HTTPException(401, "Authentication code is invalid or already used")
        if password_hasher.check_needs_rehash(user["password_hash"]):
            await conn.execute(
                "UPDATE atlas.users SET password_hash=%s WHERE id=%s",
                (await hash_password(body.password), user["id"]),
            )
        return await new_session(conn, user, request, response)


@router.post("/mfa/login")
async def mfa_login(body: MFALogin, request: Request, response: Response):
    await throttle(request, "mfa", digest(body.challenge), 5)
    async with identity_transaction() as conn:
        token = await take_token(conn, body.challenge, "mfa_login")
        user = await (
            await conn.execute(
                "SELECT * FROM atlas.users WHERE id=%s AND disabled_at IS NULL FOR UPDATE",
                (token["user_id"],),
            )
        ).fetchone()
        if not user or not user["mfa_secret"] or not await consume_mfa(conn, user, body.code):
            raise HTTPException(401, "Authentication code is invalid or already used")
        return await new_session(conn, user, request, response)


@router.get("/me")
async def me(user: dict = Depends(current_account)):
    return {"user": public_user(user), "session_id": user["session_id"]}


@router.post("/logout")
async def logout(response: Response, user: dict = Depends(current_account)):
    async with identity_transaction() as conn:
        await conn.execute(
            "UPDATE atlas.user_sessions SET revoked_at=now() WHERE id=%s", (user["session_id"],)
        )
        await security_event(conn, user["id"], "security.session_revoked", user["session_id"])
    response.delete_cookie(COOKIE)
    return {"message": "Signed out"}


@router.post("/logout-all")
async def logout_all(response: Response, user: dict = Depends(current_account)):
    async with identity_transaction() as conn:
        await conn.execute(
            "UPDATE atlas.user_sessions SET revoked_at=now() WHERE user_id=%s", (user["id"],)
        )
        await security_event(conn, user["id"], "security.sessions_revoked")
    response.delete_cookie(COOKIE)
    return {"message": "All sessions signed out"}


@router.get("/sessions")
async def sessions(user: dict = Depends(current_account)):
    async with identity_transaction() as conn:
        rows = await (
            await conn.execute(
                "SELECT id,created_at,last_seen_at,expires_at,user_agent,id=%s AS current FROM atlas.user_sessions WHERE user_id=%s AND revoked_at IS NULL AND expires_at>now() AND last_seen_at>now()-(%s * interval '1 second') ORDER BY last_seen_at DESC",
                (user["session_id"], user["id"], settings.session_idle_seconds),
            )
        ).fetchall()
    return {"items": rows}


@router.delete("/sessions/{session_id}")
async def revoke_session(
    session_id: UUID, response: Response, user: dict = Depends(current_account)
):
    async with identity_transaction() as conn:
        revoked = await (
            await conn.execute(
                "UPDATE atlas.user_sessions SET revoked_at=now() WHERE id=%s AND user_id=%s AND revoked_at IS NULL RETURNING id",
                (session_id, user["id"]),
            )
        ).fetchone()
        if revoked:
            await security_event(conn, user["id"], "security.session_revoked", session_id)
    if session_id == user["session_id"]:
        response.delete_cookie(COOKIE)
    return {"message": "Session revoked"}


@router.patch("/profile")
async def profile(body: Profile, user: dict = Depends(current_account)):
    async with identity_transaction() as conn:
        row = await (
            await conn.execute(
                "UPDATE atlas.users SET name=coalesce(%s,name),theme=coalesce(%s,theme),updated_at=now() WHERE id=%s RETURNING *",
                (body.name.strip() if body.name else None, body.theme, user["id"]),
            )
        ).fetchone()
    return {"user": public_user(row)}


@router.post("/reauthenticate")
async def reauthenticate(
    body: Reauthenticate, request: Request, user: dict = Depends(current_account)
):
    await throttle(request, "reauth", str(user["id"]), 5)
    async with identity_transaction() as conn:
        locked = await (
            await conn.execute("SELECT * FROM atlas.users WHERE id=%s FOR UPDATE", (user["id"],))
        ).fetchone()
        await credentials(conn, locked, body.password, body.code)
        await conn.execute(
            "UPDATE atlas.user_sessions SET reauthenticated_at=now() WHERE id=%s",
            (user["session_id"],),
        )
        await security_event(conn, user["id"], "security.reauthenticated", user["session_id"])
    return {"message": "Identity confirmed for five minutes"}


@router.post("/password")
async def change_password(
    body: PasswordChange,
    request: Request,
    response: Response,
    user: dict = Depends(current_account),
):
    await throttle(request, "reauth", str(user["id"]), 5)
    hashed = await hash_password(body.new_password)
    async with identity_transaction() as conn:
        locked = await (
            await conn.execute("SELECT * FROM atlas.users WHERE id=%s FOR UPDATE", (user["id"],))
        ).fetchone()
        await credentials(conn, locked, body.current_password, body.code)
        await conn.execute(
            "UPDATE atlas.users SET password_hash=%s,updated_at=now() WHERE id=%s",
            (hashed, user["id"]),
        )
        await conn.execute(
            "UPDATE atlas.user_sessions SET revoked_at=now() WHERE user_id=%s", (user["id"],)
        )
        await conn.execute(
            "UPDATE atlas.account_tokens SET used_at=now() WHERE user_id=%s AND used_at IS NULL",
            (user["id"],),
        )
        await security_event(conn, user["id"], "security.password_changed")
        return await new_session(conn, locked, request, response)


@router.post("/email", status_code=202)
async def change_email(body: EmailChange, request: Request, user: dict = Depends(current_account)):
    await throttle(request, "email", str(user["id"]), 1)
    async with identity_transaction() as conn:
        locked = await (
            await conn.execute("SELECT * FROM atlas.users WHERE id=%s FOR UPDATE", (user["id"],))
        ).fetchone()
        await credentials(conn, locked, body.password, body.code)
        exists = await (
            await conn.execute("SELECT id FROM atlas.users WHERE email=%s", (body.email,))
        ).fetchone()
        if not exists:
            await email_token(conn, locked, "email_change", body.email)
    return GENERIC_MAIL


@router.post("/verify-email-change")
async def verify_email_change(body: TokenBody, request: Request, response: Response):
    await throttle(request, "verify", maximum=15)
    async with identity_transaction() as conn:
        token = await take_token(conn, body.token, "email_change")
        # Serialize address claims so concurrent links cannot generate a uniqueness error.
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (token["payload"]["email"],)
        )
        exists = await (
            await conn.execute(
                "SELECT id FROM atlas.users WHERE email=%s", (token["payload"]["email"],)
            )
        ).fetchone()
        if exists:
            raise HTTPException(409, "This email address is unavailable")
        await conn.execute(
            "UPDATE atlas.users SET email=%s,email_verified_at=now(),updated_at=now() WHERE id=%s",
            (token["payload"]["email"], token["user_id"]),
        )
        await conn.execute(
            "UPDATE atlas.user_sessions SET revoked_at=now() WHERE user_id=%s", (token["user_id"],)
        )
        await conn.execute(
            "UPDATE atlas.account_tokens SET used_at=now() WHERE user_id=%s AND used_at IS NULL",
            (token["user_id"],),
        )
        await security_event(conn, token["user_id"], "security.email_changed")
    response.delete_cookie(COOKIE)
    return {"message": "Email updated. Sign in with your new email."}


@router.post("/mfa/enroll")
async def enroll(user: dict = Depends(current_account)):
    require_recent(user)
    secret = pyotp.random_base32()
    async with identity_transaction() as conn:
        row = await (
            await conn.execute(
                "UPDATE atlas.users SET mfa_pending_secret=%s WHERE id=%s AND mfa_secret IS NULL RETURNING id",
                (cipher().encrypt(secret.encode()).decode(), user["id"]),
            )
        ).fetchone()
        if not row:
            raise HTTPException(409, "MFA is already enabled")
    return {
        "secret": secret,
        "uri": pyotp.TOTP(secret).provisioning_uri(name=user["email"], issuer_name="Atlas"),
    }


async def create_recovery_codes(conn, user_id):
    codes = [secrets.token_hex(8).upper() for _ in range(10)]
    await conn.execute("DELETE FROM atlas.recovery_codes WHERE user_id=%s", (user_id,))
    for code in codes:
        await conn.execute(
            "INSERT INTO atlas.recovery_codes(user_id,digest) VALUES(%s,%s)",
            (user_id, digest(code)),
        )
    return codes


@router.post("/mfa/confirm")
async def confirm(body: Code, request: Request, user: dict = Depends(current_account)):
    require_recent(user)
    await throttle(request, "mfa-confirm", str(user["id"]), 5)
    async with identity_transaction() as conn:
        locked = await (
            await conn.execute("SELECT * FROM atlas.users WHERE id=%s FOR UPDATE", (user["id"],))
        ).fetchone()
        if not locked["mfa_pending_secret"] or locked["mfa_secret"]:
            raise HTTPException(409, "Start MFA enrollment first")
        secret = cipher().decrypt(locked["mfa_pending_secret"].encode()).decode()
        current = int(time.time()) // 30
        matched_step = next(
            (
                step
                for step in [current, current - 1, current + 1]
                if secrets.compare_digest(pyotp.TOTP(secret).at(step * 30), body.code)
            ),
            None,
        )
        if matched_step is None:
            raise HTTPException(400, "Authentication code is incorrect")
        await conn.execute(
            "UPDATE atlas.users SET mfa_secret=mfa_pending_secret,mfa_pending_secret=NULL,mfa_last_step=%s WHERE id=%s",
            (matched_step, user["id"]),
        )
        codes = await create_recovery_codes(conn, user["id"])
        await conn.execute(
            "UPDATE atlas.account_tokens SET used_at=now() WHERE user_id=%s AND purpose='mfa_login' AND used_at IS NULL",
            (user["id"],),
        )
        await conn.execute(
            "UPDATE atlas.user_sessions SET revoked_at=now() WHERE user_id=%s AND id<>%s",
            (user["id"], user["session_id"]),
        )
        await security_event(conn, user["id"], "security.mfa_enabled")
    return {"recovery_codes": codes}


@router.post("/mfa/disable")
async def disable_mfa(
    body: Reauthenticate, request: Request, user: dict = Depends(current_account)
):
    await throttle(request, "reauth", str(user["id"]), 5)
    async with identity_transaction() as conn:
        locked = await (
            await conn.execute("SELECT * FROM atlas.users WHERE id=%s FOR UPDATE", (user["id"],))
        ).fetchone()
        await credentials(conn, locked, body.password, body.code)
        await conn.execute(
            "UPDATE atlas.users SET mfa_secret=NULL,mfa_pending_secret=NULL,mfa_last_step=-1 WHERE id=%s",
            (user["id"],),
        )
        await conn.execute("DELETE FROM atlas.recovery_codes WHERE user_id=%s", (user["id"],))
        await conn.execute(
            "UPDATE atlas.user_sessions SET revoked_at=now() WHERE user_id=%s AND id<>%s",
            (user["id"], user["session_id"]),
        )
        await conn.execute(
            "UPDATE atlas.account_tokens SET used_at=now() WHERE user_id=%s AND purpose='mfa_login' AND used_at IS NULL",
            (user["id"],),
        )
        await security_event(conn, user["id"], "security.mfa_disabled")
    return {"message": "MFA disabled"}


@router.post("/mfa/recovery-codes")
async def regenerate_codes(
    body: Reauthenticate, request: Request, user: dict = Depends(current_account)
):
    await throttle(request, "reauth", str(user["id"]), 5)
    async with identity_transaction() as conn:
        locked = await (
            await conn.execute("SELECT * FROM atlas.users WHERE id=%s FOR UPDATE", (user["id"],))
        ).fetchone()
        if not locked["mfa_secret"]:
            raise HTTPException(409, "Enable MFA first")
        await credentials(conn, locked, body.password, body.code)
        await security_event(conn, user["id"], "security.recovery_codes_rotated")
        return {"recovery_codes": await create_recovery_codes(conn, user["id"])}


class DeleteAccount(BaseModel):
    confirmation: Literal["DELETE"]


@router.delete("/account")
async def delete_account(
    body: DeleteAccount, response: Response, user: dict = Depends(current_account)
):
    require_recent(user)
    async with identity_transaction() as conn:
        # Lock organizations in stable order, then recheck owners. This also serializes transfer/removal.
        memberships = await (
            await conn.execute(
                "SELECT tenant_id FROM atlas.memberships WHERE user_id=%s ORDER BY tenant_id",
                (user["id"],),
            )
        ).fetchall()
        for membership in memberships:
            tenant = membership["tenant_id"]
            org = await (
                await conn.execute(
                    "SELECT kind FROM atlas.tenants WHERE id=%s FOR UPDATE", (tenant,)
                )
            ).fetchone()
            member = await (
                await conn.execute(
                    "SELECT * FROM atlas.memberships WHERE tenant_id=%s AND user_id=%s",
                    (tenant, user["id"]),
                )
            ).fetchone()
            if member and member["role"] == "owner" and member["status"] == "active":
                owners = await (
                    await conn.execute(
                        "SELECT count(*) AS n FROM atlas.memberships WHERE tenant_id=%s AND role='owner' AND status='active'",
                        (tenant,),
                    )
                ).fetchone()
                if owners["n"] <= 1:
                    if org["kind"] == "personal":
                        await conn.execute(
                            "UPDATE atlas.tenants SET status='suspended',auth_revision=auth_revision+1 WHERE id=%s",
                            (tenant,),
                        )
                    else:
                        raise HTTPException(
                            409,
                            "Transfer ownership or delete your company before deleting your account",
                        )
        await security_event(conn, user["id"], "security.account_disabled")
        await conn.execute(
            "DELETE FROM atlas.resource_grants WHERE subject_type='user' AND subject_id=%s",
            (user["id"],),
        )
        await conn.execute("DELETE FROM atlas.team_members WHERE user_id=%s", (user["id"],))
        await conn.execute("DELETE FROM atlas.memberships WHERE user_id=%s", (user["id"],))
        await conn.execute(
            "UPDATE atlas.users SET disabled_at=now(),mfa_secret=NULL,mfa_pending_secret=NULL,updated_at=now() WHERE id=%s",
            (user["id"],),
        )
        await conn.execute(
            "UPDATE atlas.user_sessions SET revoked_at=now() WHERE user_id=%s", (user["id"],)
        )
        await conn.execute(
            "UPDATE atlas.account_tokens SET used_at=now() WHERE user_id=%s AND used_at IS NULL",
            (user["id"],),
        )
        await conn.execute("DELETE FROM atlas.recovery_codes WHERE user_id=%s", (user["id"],))
    response.delete_cookie(COOKIE)
    return {
        "message": "Account disabled and sessions revoked. Personal data is scheduled for anonymization after 30 days; company-owned records remain with the company."
    }
