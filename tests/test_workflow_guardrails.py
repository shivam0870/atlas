"""Review regressions for source boundaries, workflow erasure, fairness and budgets."""

from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi import HTTPException
from test_workflows import ready_document

from atlas.config import settings
from atlas.db import transaction
from atlas.workflows import (
    BriefingBody,
    PlaybookBody,
    RunBody,
    SourceBody,
    StepBody,
    briefing_terms,
    create_briefing,
    create_playbook,
    create_source,
    edit_playbook,
    github_json,
    read_confined_file,
    run_briefing,
    start_playbook,
)


def test_source_file_rejects_absolute_paths(tmp_path):
    outside = tmp_path / "confidential.md"
    outside.write_text("Private data")
    root = tmp_path / "approved"
    root.mkdir()
    with pytest.raises(HTTPException):
        read_confined_file(root, str(outside))


async def test_github_stream_is_bounded_before_buffering(monkeypatch):
    import atlas.workflows

    monkeypatch.setattr(atlas.workflows, "MAX_GITHUB_RESPONSE_BYTES", 32)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 33))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(HTTPException) as error:
            await github_json(client, "repos/example/docs")
    assert error.value.status_code == 422


def test_briefing_conversational_topic():
    assert briefing_terms(
        "Every Monday, summarize changes to checkout runbooks and deployment policies."
    ) == ["checkout", "runbooks", "deployment", "policies"]
    assert briefing_terms("What changed this week?") == []


@pytest.mark.integration
async def test_briefing_topic_matches_changes_and_consumes_query_quota(database, tenants):
    tenant = tenants.ids[0]
    identity = tenants.identities[tenant]
    document, _, space = await ready_document(
        tenant, "Checkout deployments", "Two approvals are required before checkout deployment."
    )
    await ready_document(tenant, "Holiday calendar", "Annual leave starts in December.")
    subscription = await create_briefing(
        BriefingBody(
            name="Checkout digest",
            space_ids=[space],
            question="Summarize changes to checkout deployment policies.",
        ),
        identity,
    )
    result = await run_briefing(identity, subscription["id"])
    assert [item["document_id"] for item in result["items"]] == [str(document)]
    async with transaction(tenant) as conn:
        used = await (
            await conn.execute(
                "SELECT queries FROM atlas.daily_resource_usage WHERE day=current_date"
            )
        ).fetchone()
        assert used["queries"] == 1
        await conn.execute("UPDATE atlas.tenant_limits SET queries_per_day=1")
    with pytest.raises(HTTPException) as error:
        await run_briefing(identity, subscription["id"])
    assert error.value.status_code == 429


@pytest.mark.integration
async def test_deletion_erases_old_playbook_run_after_definition_replaced(database, tenants):
    tenant = tenants.ids[0]
    identity = tenants.identities[tenant]
    document, version, space = await ready_document(
        tenant, "Secret guide", "Erase this historical passage"
    )
    book = await create_playbook(
        PlaybookBody(
            name="Guide",
            space_id=space,
            status="published",
            steps=[
                StepBody(
                    title="Secret step",
                    instructions="Erase this historical passage",
                    document_id=document,
                    version_id=version,
                )
            ],
        ),
        identity,
    )
    run = await start_playbook(book["id"], RunBody(), identity)
    await edit_playbook(
        book["id"],
        PlaybookBody(name="Guide", space_id=space, steps=[StepBody(title="Unrelated step")]),
        identity,
    )
    async with transaction(tenant) as conn:
        await conn.execute(
            "UPDATE atlas.documents SET current_version_id=NULL,pending_version_id=NULL WHERE id=%s",
            (document,),
        )
        await conn.execute("DELETE FROM atlas.documents WHERE id=%s", (document,))
    # Query as database administrator: an RLS-hidden snapshot is still retained
    # data and would not satisfy permanent source erasure.
    with psycopg.connect(settings.database_admin_url) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM atlas.playbook_runs WHERE tenant_id=%s AND id=%s",
                (tenant, run["id"]),
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM atlas.playbooks WHERE tenant_id=%s AND id=%s",
                (tenant, book["id"]),
            ).fetchone()[0]
            == 1
        )


@pytest.mark.integration
async def test_due_workflows_give_other_tenants_a_turn(database, tenants):
    for index, tenant in enumerate(tenants.ids):
        tenants.use(tenant)
        identity = tenants.identities[tenant]
        _, _, space = await ready_document(tenant, "Reference", "Reference content")
        for number in range(12 if index == 0 else 1):
            await create_briefing(
                BriefingBody(name=f"Digest {number}", space_ids=[space]), identity
            )
    with psycopg.connect(settings.database_admin_url) as conn:
        # Make these records older than pre-existing fixtures so this test is
        # independent of other isolated demo subscriptions.
        conn.execute(
            "UPDATE atlas.briefing_subscriptions SET next_run_at='2000-01-01' WHERE tenant_id=ANY(%s)",
            (tenants.ids,),
        )
        due = conn.execute("SELECT tenant_id FROM atlas.due_workflows()").fetchall()
    assert set(tenant for (tenant,) in due[:2]) == set(tenants.ids)


@pytest.mark.integration
async def test_source_sync_concurrency_limit_cannot_be_bypassed_by_connections(database, tenants):
    tenant = tenants.ids[0]
    identity = tenants.identities[tenant]
    _, _, space = await ready_document(tenant, "Reference", "Reference content")
    sources = [
        await create_source(
            SourceBody(name=f"Source {n}", kind="github", location="example/docs", space_id=space),
            identity,
        )
        for n in range(3)
    ]
    for source in sources[:2]:
        async with transaction(tenant) as conn:
            await conn.execute(
                "INSERT INTO atlas.source_runs(tenant_id,id,source_id) VALUES(%s,%s,%s)",
                (tenant, uuid4(), source["id"]),
            )
    with pytest.raises(psycopg.errors.InsufficientResources):
        async with transaction(tenant) as conn:
            await conn.execute(
                "INSERT INTO atlas.source_runs(tenant_id,id,source_id) VALUES(%s,%s,%s)",
                (tenant, uuid4(), sources[2]["id"]),
            )
