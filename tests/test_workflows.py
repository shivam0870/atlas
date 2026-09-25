"""Workflow evidence, tenant isolation, publication and scheduling regressions."""

import json
from uuid import uuid4

import pytest
from fastapi import HTTPException

from atlas.db import transaction
from atlas.knowledge import PublicationBody, publish_version
from atlas.workflows import (
    BriefingBody,
    CompareBody,
    PlaybookBody,
    RunBody,
    SourceBody,
    StepBody,
    StepProgress,
    compare_documents,
    compare_passages,
    create_briefing,
    create_playbook,
    create_source,
    knowledge_map,
    playbook_detail,
    run_briefing,
    source_files,
    source_location,
    start_playbook,
    update_step,
)


async def ready_document(tenant, title, content):
    async with transaction(tenant) as conn:
        space = await (await conn.execute("SELECT id FROM atlas.spaces LIMIT 1")).fetchone()
        document_id, version_id = uuid4(), uuid4()
        await conn.execute(
            "INSERT INTO atlas.documents(tenant_id,id,title,source_key,content_hash,content,space_id,status) VALUES(%s,%s,%s,%s,%s,%s,%s,'ready')",
            (tenant, document_id, title, str(uuid4()), str(uuid4()), content, space["id"]),
        )
        await conn.execute(
            "INSERT INTO atlas.document_versions(tenant_id,id,document_id,number,title,content,content_hash,status) VALUES(%s,%s,%s,1,%s,%s,%s,'ready')",
            (tenant, version_id, document_id, title, content, str(uuid4())),
        )
        await conn.execute(
            "UPDATE atlas.documents SET current_version_id=%s WHERE id=%s",
            (version_id, document_id),
        )
    return document_id, version_id, space["id"]


def test_exact_comparison_and_connector_validation():
    result = compare_passages(
        "Owner: A\nApproval: one", "Owner: A\nApproval: two\nRollback required"
    )
    assert result["summary"] == {"added_lines": 2, "removed_lines": 1, "changed_sections": 1}
    assert result["changes"][0]["left_text"] == "Approval: one"
    assert "Rollback required" in result["changes"][0]["right_text"]
    assert source_location("github", "https://github.com/example/docs.git") == "example/docs"
    for kind, path in (("github", "http://127.0.0.1/private"), ("folder", "../../etc")):
        with pytest.raises(HTTPException):
            source_location(kind, path)


@pytest.mark.integration
async def test_compare_publication_and_cross_tenant_denial(database, tenants):
    tenant = tenants.ids[0]
    identity = tenants.identities[tenant]
    left, version, _ = await ready_document(tenant, "Policy", "Approval: one")
    right, _, _ = await ready_document(tenant, "New policy", "Approval: two")
    result = await compare_documents(
        CompareBody(left_document_id=left, right_document_id=right), identity
    )
    assert result["changes"][0]["right_text"] == "Approval: two"
    await publish_version(left, version, PublicationBody(publication_status="draft"), identity)
    async with transaction(tenant) as conn:
        row = await (
            await conn.execute(
                "SELECT current_version_id FROM atlas.documents WHERE id=%s", (left,)
            )
        ).fetchone()
        assert row["current_version_id"] is None
    await publish_version(left, version, PublicationBody(publication_status="published"), identity)
    tenants.use(tenants.ids[1])
    with pytest.raises(HTTPException) as error:
        await compare_documents(
            CompareBody(left_document_id=left, right_document_id=right),
            tenants.identities[tenants.ids[1]],
        )
    assert error.value.status_code == 404


@pytest.mark.integration
async def test_playbook_progress_stale_evidence_and_briefings(database, tenants):
    tenant = tenants.ids[0]
    identity = tenants.identities[tenant]
    document, version, space = await ready_document(
        tenant, "Release policy", "Request approval before release."
    )
    book = await create_playbook(
        PlaybookBody(
            name="Release",
            space_id=space,
            status="published",
            steps=[
                StepBody(
                    title="Get approval",
                    document_id=document,
                    version_id=version,
                    requires_approval=True,
                )
            ],
        ),
        identity,
    )
    run = await start_playbook(book["id"], RunBody(), identity)
    step = run["steps"][0]
    partial = await update_step(run["id"], step["id"], StepProgress(completed=True), identity)
    assert partial["status"] == "active"
    finished = await update_step(run["id"], step["id"], StepProgress(approved=True), identity)
    assert finished["status"] == "completed"
    subscription = await create_briefing(
        BriefingBody(name="Release changes", document_ids=[document]), identity
    )
    briefing = await run_briefing(identity, subscription["id"])
    assert briefing["status"] == "completed"
    assert briefing["items"][0]["version_id"] == str(version)
    empty = await run_briefing(identity, subscription["id"])
    assert not empty["items"]
    await publish_version(
        document, version, PublicationBody(publication_status="archived"), identity
    )
    assert (await playbook_detail(book["id"], identity))["needs_review"]
    with pytest.raises(HTTPException) as error:
        await start_playbook(book["id"], RunBody(), identity)
    assert error.value.status_code == 409
    graph = await knowledge_map(identity)
    assert f"document:{document}" in {node["id"] for node in graph["nodes"]}


@pytest.mark.integration
async def test_workflow_rls_rechecks_source_permissions(database, tenants):
    tenant = tenants.ids[0]
    identity = tenants.identities[tenant]
    document, version, space = await ready_document(
        tenant, "Confidential", "Sensitive instructions"
    )
    book = await create_playbook(
        PlaybookBody(
            name="Private instructions",
            space_id=space,
            steps=[
                StepBody(
                    title="Follow source",
                    instructions="Sensitive instructions",
                    document_id=document,
                    version_id=version,
                )
            ],
        ),
        identity,
    )
    # A separate tenant cannot read either raw JSON evidence or metadata via the application role.
    tenants.use(tenants.ids[1])
    async with transaction(tenants.ids[1]) as conn:
        assert not await (
            await conn.execute("SELECT * FROM atlas.playbooks WHERE id=%s", (book["id"],))
        ).fetchall()
    tenants.use(tenant)
    async with transaction(tenant) as conn:
        await conn.execute(
            "UPDATE atlas.documents SET lifecycle='trashed' WHERE id=%s", (document,)
        )
        assert not await (
            await conn.execute("SELECT * FROM atlas.playbooks WHERE id=%s", (book["id"],))
        ).fetchall()


@pytest.mark.integration
async def test_source_folder_alias_is_tenant_bound_and_skips_symlinks(
    database, tenants, tmp_path, monkeypatch
):
    tenant = tenants.ids[0]
    identity = tenants.identities[tenant]
    _, _, space = await ready_document(tenant, "Folder test", "Reference")
    (tmp_path / "guide.md").write_text("Approved guide")
    (tmp_path / "private.md").symlink_to("/etc/passwd")
    monkeypatch.setenv("ATLAS_SOURCE_FOLDERS", json.dumps({f"{tenant}:docs": str(tmp_path)}))
    source = await create_source(
        SourceBody(name="Docs", kind="folder", location="docs", space_id=space), identity
    )
    files = await source_files(source)
    assert files == [("guide.md", b"Approved guide")]
    with pytest.raises(HTTPException):
        await source_files({**source, "tenant_id": tenants.ids[1]})


@pytest.mark.integration
async def test_source_sync_is_idempotent_and_archives_removed_files(
    database, tenants, tmp_path, monkeypatch
):
    import atlas.knowledge
    from atlas.workflows import sync_source

    tenant = tenants.ids[0]
    identity = tenants.identities[tenant]
    _, _, space = await ready_document(tenant, "Source sync", "Reference document")
    folder = tmp_path / "source"
    folder.mkdir()
    guide = folder / "guide.md"
    guide.write_text("A unique synchronized release guide")
    monkeypatch.setattr(atlas.knowledge, "UPLOAD_ROOT", tmp_path / "uploads")
    monkeypatch.setenv("ATLAS_SOURCE_FOLDERS", json.dumps({f"{tenant}:docs": str(folder)}))
    source = await create_source(
        SourceBody(name="Docs", kind="folder", location="docs", space_id=space), identity
    )
    first = await sync_source(identity, source["id"])
    assert first["status"] == "completed"
    assert first["imported"] == 1
    second = await sync_source(identity, source["id"])
    assert second["unchanged"] == 1
    assert second["imported"] == 0
    guide.unlink()
    removed = await sync_source(identity, source["id"])
    assert removed["archived"] == 1
    async with transaction(tenant) as conn:
        row = await (
            await conn.execute(
                "SELECT d.lifecycle FROM atlas.documents d JOIN atlas.source_items s ON d.tenant_id=s.tenant_id AND d.id=s.document_id WHERE s.source_id=%s",
                (source["id"],),
            )
        ).fetchone()
        assert row["lifecycle"] == "archived"


def test_comparison_insights_keep_exact_evidence_and_require_review():
    from atlas.workflows import comparison_insights

    changes = compare_passages(
        "Owner: Payments. Approval required within 2 days.",
        "Owner: Platform. Approval optional within 5 days.",
    )["changes"]
    insights = comparison_insights(changes)
    assert {"owner", "date", "quantity", "requirement", "possible_conflict"} <= {
        item["kind"] for item in insights
    }
    assert all(item["requires_review"] for item in insights)
    assert insights[0]["left_passage"] == changes[0]["left_text"]
    assert insights[0]["right_passage"] == changes[0]["right_text"]
