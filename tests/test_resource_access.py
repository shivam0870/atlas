"""Real PostgreSQL resource isolation and publication tests (embedding is stubbed explicitly)."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from atlas.auth import Identity
from atlas.config import settings
from atlas.db import access_context, bind_identity, transaction
from atlas.extraction import ExtractedDocument
from atlas.ingestion import index_document
from atlas.knowledge import Grant, GrantBody, SpaceBody, create_space, store_document, update_grants


@pytest.fixture
def resource_workspace():
    tenant, other_tenant = uuid4(), uuid4()
    people = [uuid4() for _ in range(4)]
    sessions = [uuid4() for _ in people]
    roles = ["owner", "editor", "viewer", "viewer"]
    with psycopg.connect(settings.database_admin_url) as conn:
        for tid in [tenant, other_tenant]:
            conn.execute(
                "INSERT INTO atlas.tenants(id,slug,name,claimed_at) VALUES(%s,%s,'Resource test',now())",
                (tid, str(tid)),
            )
        for uid, sid, role in zip(people, sessions, roles, strict=True):
            conn.execute(
                "INSERT INTO atlas.users(id,email,name,password_hash,email_verified_at) VALUES(%s,%s,'Resource tester','unused-test-password',now())",
                (uid, f"{uid}@example.test"),
            )
            conn.execute(
                "INSERT INTO atlas.user_sessions(id,user_id,digest,expires_at) VALUES(%s,%s,%s,now()+interval '1 hour')",
                (sid, uid, uuid4().hex + uuid4().hex),
            )
            conn.execute(
                "INSERT INTO atlas.memberships(tenant_id,user_id,role) VALUES(%s,%s,%s)",
                (tenant, uid, role),
            )
    identities = [
        Identity(
            tenant,
            sid,
            "Resource test",
            ["read", "query"]
            + (["write"] if role != "viewer" else [])
            + (["admin"] if role == "owner" else []),
            uid,
            role,
            "user",
            uid,
            0,
            sid,
        )
        for uid, sid, role in zip(people, sessions, roles, strict=True)
    ]
    yield SimpleNamespace(tenant=tenant, other=other_tenant, identities=identities)
    access_context.set(None)
    with psycopg.connect(settings.database_admin_url) as conn:
        for tid in [tenant, other_tenant]:
            conn.execute(
                "UPDATE atlas.documents SET current_version_id=NULL,pending_version_id=NULL WHERE tenant_id=%s",
                (tid,),
            )
            for table in [
                "bookmarks",
                "feedback",
                "messages",
                "conversations",
                "queries",
                "usage_ledger",
                "reservations",
                "budget_periods",
                "tenant_limits",
                "evaluation_jobs",
                "outbox",
                "ingestion_jobs",
                "eval_labels",
                "eval_runs",
                "chunk_terms",
                "embeddings",
                "chunks",
                "document_versions",
                "resource_grants",
                "access_requests",
                "notifications",
                "embedding_cache",
                "documents",
                "collections",
                "team_members",
                "teams",
                "memberships",
                "api_keys",
                "service_accounts",
                "spaces",
            ]:
                conn.execute(f"DELETE FROM atlas.{table} WHERE tenant_id=%s", (tid,))
            conn.execute("DELETE FROM atlas.audit_events WHERE tenant_id=%s", (tid,))
            conn.execute("DELETE FROM atlas.tenants WHERE id=%s", (tid,))
        conn.execute("DELETE FROM atlas.users WHERE id=ANY(%s)", (people,))


@pytest.mark.integration
async def test_space_and_document_restrictions_intersect_and_revoke(database, resource_workspace):
    ws = resource_workspace
    admin, editor, viewer, outsider = ws.identities
    bind_identity(admin)
    space = await create_space(
        SpaceBody(name="Restricted engineering", visibility="restricted"), admin
    )
    doc_id = uuid4()
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "INSERT INTO atlas.documents(tenant_id,id,title,source_key,content_hash,content,space_id,restricted) VALUES(%s,%s,'Restricted','r','h','secret',%s,true)",
            (ws.tenant, doc_id, space["id"]),
        )
    await update_grants(
        admin,
        "space",
        space["id"],
        GrantBody(
            grants=[
                Grant(subject_type="user", subject_id=editor.user_id),
                Grant(subject_type="user", subject_id=viewer.user_id),
            ]
        ),
    )
    await update_grants(
        admin,
        "document",
        doc_id,
        GrantBody(
            grants=[
                Grant(subject_type="user", subject_id=editor.user_id),
                Grant(subject_type="user", subject_id=outsider.user_id),
            ]
        ),
    )
    for principal, expected in [(admin, True), (editor, True), (viewer, False), (outsider, False)]:
        bind_identity(principal)
        async with transaction(ws.tenant) as conn:
            row = await (
                await conn.execute("SELECT content FROM atlas.documents WHERE id=%s", (doc_id,))
            ).fetchone()
            assert bool(row) is expected
    bind_identity(admin)
    await update_grants(admin, "space", space["id"], GrantBody(grants=[]))
    bind_identity(editor)
    async with transaction(ws.tenant) as conn:
        assert not await (
            await conn.execute("SELECT * FROM atlas.documents WHERE id=%s", (doc_id,))
        ).fetchone()
    access_context.set(None)
    async with transaction(ws.tenant) as conn:
        assert not await (await conn.execute("SELECT * FROM atlas.documents")).fetchall()


@pytest.mark.integration
async def test_replacement_publishes_atomically_and_preserves_old_evidence(
    database, resource_workspace, monkeypatch
):
    from atlas import ingestion

    ws = resource_workspace
    admin = ws.identities[0]
    bind_identity(admin)
    monkeypatch.setattr(
        ingestion,
        "encoder",
        lambda: SimpleNamespace(tokenizer=SimpleNamespace(encode=lambda text: text.split())),
    )
    monkeypatch.setattr(ingestion, "model_revision", lambda: "test-only-local-embedding")

    @asynccontextmanager
    async def worker_transaction(tenant_id=None):
        async with await psycopg.AsyncConnection.connect(
            settings.worker_database_url, row_factory=dict_row
        ) as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.tenant_id',%s,true)",
                    (str(tenant_id) if tenant_id else "",),
                )
                yield connection

    monkeypatch.setattr(ingestion, "transaction", worker_transaction)

    async def embeddings(texts):
        return [[1.0] + [0.0] * 383 for _ in texts]

    monkeypatch.setattr(ingestion, "embed", embeddings)
    first = await store_document(
        admin,
        "Release",
        "release.txt",
        ExtractedDocument(
            "Old release evidence", "text/plain", [{"start": 0, "end": 20, "page": 1}]
        ),
        None,
    )
    await index_document(ws.tenant, first["id"])
    second = await store_document(
        admin,
        "Release",
        "release.txt",
        ExtractedDocument(
            "New release evidence", "text/plain", [{"start": 0, "end": 20, "page": 2}]
        ),
        None,
        replace_id=first["id"],
    )
    async with transaction(ws.tenant) as conn:
        row = await (
            await conn.execute(
                "SELECT status,content,current_version_id,pending_version_id FROM atlas.documents WHERE id=%s",
                (first["id"],),
            )
        ).fetchone()
        assert row["status"] == "ready" and row["content"] == "Old release evidence"
        assert row["current_version_id"] == first["version_id"]
        assert row["pending_version_id"] == second["version_id"]

    async def fail_embedding(texts):
        raise RuntimeError("Explicit model failure fixture")

    monkeypatch.setattr(ingestion, "embed", fail_embedding)
    with pytest.raises(RuntimeError, match="Explicit model failure"):
        await index_document(ws.tenant, first["id"])
    async with transaction(ws.tenant) as conn:
        row = await (
            await conn.execute(
                "SELECT status,content FROM atlas.documents WHERE id=%s", (first["id"],)
            )
        ).fetchone()
        assert row == {"status": "ready", "content": "Old release evidence"}
    monkeypatch.setattr(ingestion, "embed", embeddings)
    await index_document(ws.tenant, first["id"])
    async with transaction(ws.tenant) as conn:
        row = await (
            await conn.execute(
                "SELECT status,content,current_version_id,pending_version_id FROM atlas.documents WHERE id=%s",
                (first["id"],),
            )
        ).fetchone()
        assert row["content"] == "New release evidence" and row["pending_version_id"] is None
        assert row["current_version_id"] == second["version_id"]
        chunks = await (
            await conn.execute(
                "SELECT DISTINCT version_id FROM atlas.chunks WHERE document_id=%s", (first["id"],)
            )
        ).fetchall()
        assert {chunk["version_id"] for chunk in chunks} == {
            first["version_id"],
            second["version_id"],
        }


@pytest.mark.integration
async def test_service_grants_are_explicit_and_key_revocation_is_immediate(
    database, resource_workspace
):
    ws = resource_workspace
    admin = ws.identities[0]
    bind_identity(admin)
    space = await create_space(SpaceBody(name="Company knowledge"), admin)
    service_id, key_id, document_id = uuid4(), uuid4(), uuid4()
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "INSERT INTO atlas.service_accounts(tenant_id,id,name) VALUES(%s,%s,'Read integration')",
            (ws.tenant, service_id),
        )
        conn.execute(
            "INSERT INTO atlas.api_keys(tenant_id,id,service_account_id,digest,prefix,scopes) VALUES(%s,%s,%s,%s,'test-only',ARRAY['read','query'])",
            (ws.tenant, key_id, service_id, uuid4().hex + uuid4().hex),
        )
        conn.execute(
            "INSERT INTO atlas.documents(tenant_id,id,title,source_key,content_hash,content,space_id) VALUES(%s,%s,'Service evidence','service','hash','service evidence',%s)",
            (ws.tenant, document_id, space["id"]),
        )
    service = Identity(ws.tenant, key_id, "Integration", ["read", "query"], principal_id=service_id)
    bind_identity(service)
    async with transaction(ws.tenant) as conn:
        assert not await (
            await conn.execute("SELECT * FROM atlas.documents WHERE id=%s", (document_id,))
        ).fetchone()
    bind_identity(admin)
    await update_grants(
        admin,
        "space",
        space["id"],
        GrantBody(grants=[Grant(subject_type="service", subject_id=service_id)]),
    )
    bind_identity(service)
    async with transaction(ws.tenant) as conn:
        assert await (
            await conn.execute("SELECT * FROM atlas.documents WHERE id=%s", (document_id,))
        ).fetchone()
        permission = await (
            await conn.execute("SELECT atlas.can_document(%s,true) allowed", (document_id,))
        ).fetchone()
        assert not permission["allowed"]
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute("UPDATE atlas.api_keys SET revoked_at=now() WHERE id=%s", (key_id,))
    async with transaction(ws.tenant) as conn:
        assert not await (
            await conn.execute("SELECT * FROM atlas.documents WHERE id=%s", (document_id,))
        ).fetchone()


@pytest.mark.integration
async def test_access_request_approval_grants_access_and_notifies(database, resource_workspace):
    from atlas.knowledge import (
        AccessRequestBody,
        ResolveAccessBody,
        access_requests,
        request_access,
        resolve_access,
    )

    ws = resource_workspace
    admin, _, viewer, _ = ws.identities
    bind_identity(admin)
    space = await create_space(SpaceBody(name="Approval required", visibility="restricted"), admin)
    bind_identity(viewer)
    await request_access(space["id"], AccessRequestBody(reason="Support rotation"), viewer)
    await request_access(space["id"], AccessRequestBody(reason="Updated request"), viewer)
    bind_identity(admin)
    requests = await access_requests(admin)
    assert len(requests) == 1
    assert requests[0]["reason"] == "Updated request"
    async with transaction(ws.tenant) as conn:
        notices = await (
            await conn.execute(
                "SELECT * FROM atlas.notifications WHERE user_id=%s", (admin.user_id,)
            )
        ).fetchall()
        assert len(notices) == 1
    await resolve_access(requests[0]["id"], ResolveAccessBody(decision="approved"), admin)
    bind_identity(viewer)
    async with transaction(ws.tenant) as conn:
        assert (
            await (
                await conn.execute("SELECT atlas.can_space(%s) allowed", (space["id"],))
            ).fetchone()
        )["allowed"]
        notices = await (
            await conn.execute(
                "SELECT * FROM atlas.notifications WHERE user_id=%s", (viewer.user_id,)
            )
        ).fetchall()
        assert len(notices) == 1
        assert notices[0]["title"] == "Access request approved"


@pytest.mark.integration
async def test_mcp_tool_rechecks_revocation_before_return(database, resource_workspace):
    from fastapi import HTTPException

    from atlas.mcp_server import guarded_tool
    from atlas.tools import SearchArguments, ToolResult

    ws = resource_workspace
    admin = ws.identities[0]
    bind_identity(admin)

    async def revoked_during_tool(tenant_id, arguments):
        assert tenant_id == ws.tenant
        with psycopg.connect(settings.database_admin_url) as conn:
            conn.execute(
                "UPDATE atlas.user_sessions SET revoked_at=now() WHERE id=%s", (admin.session_id,)
            )
        return ToolResult(message="Result must not escape after revocation")

    with pytest.raises(HTTPException) as failure:
        await guarded_tool(admin, revoked_during_tool, SearchArguments(question="Who owns Atlas?"))
    assert failure.value.status_code in {401, 403}


@pytest.mark.integration
async def test_effective_access_matches_roles_teams_document_narrowing_and_service_scopes(
    database, resource_workspace
):
    from atlas.knowledge import grants_for

    ws = resource_workspace
    admin, editor, viewer, outsider = ws.identities
    bind_identity(admin)
    space = await create_space(SpaceBody(name="Effective access", visibility="restricted"), admin)
    team_id, doc_id, service_id, key_id = uuid4(), uuid4(), uuid4(), uuid4()
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "INSERT INTO atlas.teams(tenant_id,id,name) VALUES(%s,%s,'Operations')",
            (ws.tenant, team_id),
        )
        for user in [editor, viewer]:
            conn.execute(
                "INSERT INTO atlas.team_members(tenant_id,team_id,user_id) VALUES(%s,%s,%s)",
                (ws.tenant, team_id, user.user_id),
            )
        conn.execute(
            "INSERT INTO atlas.documents(tenant_id,id,title,source_key,content_hash,content,space_id,restricted) VALUES(%s,%s,'Narrow','effective','effective','Evidence',%s,true)",
            (ws.tenant, doc_id, space["id"]),
        )
        conn.execute(
            "INSERT INTO atlas.service_accounts(tenant_id,id,name) VALUES(%s,%s,'Named ingestion integration')",
            (ws.tenant, service_id),
        )
        conn.execute(
            "INSERT INTO atlas.api_keys(tenant_id,id,service_account_id,digest,prefix,scopes) VALUES(%s,%s,%s,%s,'effective',ARRAY['read'])",
            (ws.tenant, key_id, service_id, uuid4().hex + uuid4().hex),
        )
    await update_grants(
        admin,
        "space",
        space["id"],
        GrantBody(
            grants=[
                Grant(subject_type="team", subject_id=team_id, permission="write"),
                Grant(subject_type="service", subject_id=service_id, permission="write"),
            ]
        ),
    )
    await update_grants(
        admin,
        "document",
        doc_id,
        GrantBody(
            grants=[
                Grant(subject_type="user", subject_id=editor.user_id, permission="read"),
                Grant(subject_type="user", subject_id=viewer.user_id, permission="write"),
                Grant(subject_type="user", subject_id=outsider.user_id, permission="write"),
                Grant(subject_type="service", subject_id=service_id, permission="write"),
            ]
        ),
    )
    result = await grants_for(admin, "document", doc_id)
    effective = {row["subject_id"]: row for row in result["effective_access"]}
    for principal in [admin, editor, viewer, outsider]:
        bind_identity(principal)
        async with transaction(ws.tenant) as conn:
            allowed = await (
                await conn.execute(
                    "SELECT atlas.can_document(%s) read,atlas.can_document(%s,true) edit",
                    (doc_id, doc_id),
                )
            ).fetchone()
        assert effective[principal.user_id]["can_read"] == allowed["read"]
        assert effective[principal.user_id]["can_edit"] == allowed["edit"]
    assert effective[admin.user_id]["can_edit"]
    assert not effective[viewer.user_id]["can_edit"]
    assert any("Viewer role" in reason for reason in effective[viewer.user_id]["reasons"])
    assert any("team Operations" in reason for reason in effective[editor.user_id]["reasons"])
    assert not effective[editor.user_id]["can_edit"]
    assert not effective[outsider.user_id]["can_read"]
    assert effective[service_id]["name"] == "Named ingestion integration"
    assert effective[service_id]["can_read"] and not effective[service_id]["can_edit"]
    bind_identity(viewer)
    assert "effective_access" not in await grants_for(viewer, "document", doc_id)
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "UPDATE atlas.api_keys SET scopes=ARRAY['read','write'] WHERE id=%s", (key_id,)
        )
    bind_identity(admin)
    result = await grants_for(admin, "document", doc_id)
    service = next(row for row in result["effective_access"] if row["subject_id"] == service_id)
    assert service["can_edit"]
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute("UPDATE atlas.api_keys SET revoked_at=now() WHERE id=%s", (key_id,))
    result = await grants_for(admin, "document", doc_id)
    service = next(row for row in result["effective_access"] if row["subject_id"] == service_id)
    assert not service["can_read"] and not service["can_edit"]


@pytest.mark.integration
async def test_readable_owner_labels_do_not_expose_company_directory(database, resource_workspace):
    from atlas.knowledge import library_owners, spaces

    ws = resource_workspace
    admin, editor, viewer, outsider = ws.identities
    bind_identity(admin)
    public = await create_space(SpaceBody(name="Visible owner"), admin)
    hidden = await create_space(SpaceBody(name="Hidden owner", visibility="restricted"), admin)
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "UPDATE atlas.spaces SET owner_user_id=%s WHERE id=%s", (outsider.user_id, hidden["id"])
        )
        for space, principal in [(public, editor), (hidden, outsider)]:
            document = uuid4()
            conn.execute(
                "INSERT INTO atlas.documents(tenant_id,id,title,source_key,content_hash,content,space_id,owner_user_id) VALUES(%s,%s,'Owner label',%s,'hash','content',%s,%s)",
                (ws.tenant, document, str(document), space["id"], principal.user_id),
            )
    bind_identity(viewer)
    visible = await spaces(viewer)
    assert [row["id"] for row in visible] == [public["id"]]
    assert visible[0]["owner_name"] == "Resource tester"
    owners = await library_owners(identity=viewer)
    assert owners == {"items": [{"user_id": editor.user_id, "name": "Resource tester"}]}
    assert await library_owners(space_id=hidden["id"], identity=viewer) == {"items": []}
    assert all(set(row) == {"user_id", "name"} for row in owners["items"])
