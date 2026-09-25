"""Revalidate queued job submitters using application privileges at publication."""

from contextvars import ContextVar

from atlas.auth import Identity
from atlas.db import access_context, application_transactions, transaction

ingestion_job: ContextVar[dict | None] = ContextVar("ingestion_job", default=None)


class JobAuthorityRevoked(ValueError):
    pass


class JobCancelled(ValueError):
    pass


async def check_job(conn, tenant, document_id):
    job = ingestion_job.get()
    if not job:
        return
    current = await (
        await conn.execute(
            "SELECT status,owner FROM atlas.ingestion_jobs WHERE tenant_id=%s AND id=%s FOR UPDATE",
            (tenant, job["id"]),
        )
    ).fetchone()
    if not current or current["status"] != "running" or current["owner"] != job["owner"]:
        raise JobCancelled("Indexing was cancelled or its lease changed")
    document = await (
        await conn.execute(
            "SELECT coalesce(pending_version_id,current_version_id) version_id FROM atlas.documents WHERE tenant_id=%s AND id=%s",
            (tenant, document_id),
        )
    ).fetchone()
    if not document or (job.get("version_id") and document["version_id"] != job["version_id"]):
        raise JobAuthorityRevoked("The queued document version changed")
    if job["submitter_kind"] == "system":
        # Only the worker or database operator can create an unattributed job;
        # the database trigger rejects this state for application connections.
        return
    identity = Identity(
        tenant_id=tenant,
        key_id=job["submitter_key_id"],
        name="Background indexing",
        scopes=["write"],
        user_id=job["submitter_id"] if job["submitter_kind"] == "user" else None,
        principal_kind=job["submitter_kind"],
        principal_id=job["submitter_id"],
        session_id=job["submitter_session_id"],
    )
    token = access_context.set(identity)
    try:
        # The worker role intentionally has maintenance access. Never run the
        # submitter authorization decision using that role.
        async with application_transactions(), transaction(tenant) as app_conn:
            row = await (
                await app_conn.execute("SELECT atlas.can_document(%s,true) allowed", (document_id,))
            ).fetchone()
        if not row or not row["allowed"]:
            raise JobAuthorityRevoked("The indexing submitter no longer has document access")
    finally:
        access_context.reset(token)
