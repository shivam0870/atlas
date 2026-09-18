"""Administration validation plus real HTTP/database permission and credential journeys."""

from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from test_accounts import account_factory as _account_factory
from test_accounts import inbox_token

from atlas.accounts import router as account_router
from atlas.administration import ImportBody, InventoryBody, KeyBody, parse_inventory_csv
from atlas.administration import router as admin_router
from atlas.evaluation import compare_results
from atlas.evaluation import router as eval_router
from atlas.organizations import router as organization_router

account_factory = _account_factory
app = FastAPI()
for router in [account_router, organization_router, admin_router, eval_router]:
    app.include_router(router)


def test_csv_validation_reports_rows_duplicates_and_bad_replicas():
    rows = parse_inventory_csv(
        ImportBody(
            csv="name,replicas,owner\ncheckout,3,Platform\ncheckout,2,Other\nbroken,nope,Platform\n",
            space_id=uuid4(),
        )
    )
    assert [row["valid"] for row in rows] == [True, False, False]
    assert rows[0]["data"]["attributes"]["replicas"] == 3
    with pytest.raises(ValidationError):
        InventoryBody(name="Broken", space_id=uuid4(), attributes={"replicas": True})
    with pytest.raises(ValidationError):
        KeyBody(scopes=["admin"])


def test_comparison_reports_incompatible_basis_and_question_regression():
    label = str(uuid4())
    left = {
        "manifest": {
            "labels_hash": "a",
            "quality_basis": "human-reviewed evidence",
            "embedding_revision": "model",
        },
        "config": {"split": "development"},
        "label_count": 40,
        "metrics": {"recall_at_5": 0.9},
        "results": [{"label_id": label, "question": "What is checkout?", "recall_at_5": 1}],
    }
    right = {
        **left,
        "manifest": {**left["manifest"], "labels_hash": "b"},
        "metrics": {"recall_at_5": 0.8},
        "results": [{"label_id": label, "question": "What is checkout?", "recall_at_5": 0}],
    }
    compared = compare_results(left, right)
    assert not compared["comparable"]
    assert compared["deltas"]["recall_at_5"] == pytest.approx(-0.1)
    assert len(compared["regressions"]) == 1


async def setup_company(account):
    response = await account.client.post(
        "/api/organizations", json={"name": "Administration test", "slug": f"test-{uuid4()}"}
    )
    assert response.status_code == 201, response.text
    org = response.json()["organization"]
    import psycopg

    from atlas.config import settings

    with psycopg.connect(settings.database_admin_url) as conn:
        space = conn.execute(
            "SELECT id FROM atlas.spaces WHERE tenant_id=%s", (org["id"],)
        ).fetchone()[0]
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://test",
        cookies=account.client.cookies,
        headers={"X-Atlas-Tenant": org["id"], "X-Atlas-Client": "console"},
    )
    return org, str(space), client


@pytest.mark.integration
async def test_inventory_crud_import_compare_and_archive(database, account_factory):
    owner = await account_factory()
    org, space, client = await setup_company(owner)
    async with client:
        created = await client.post(
            "/api/inventory",
            json={
                "name": "checkout",
                "space_id": space,
                "attributes": {"replicas": 3, "owner": "Platform"},
            },
        )
        assert created.status_code == 201, created.text
        entity = created.json()["item"]["id"]
        updated = await client.patch(
            f"/api/inventory/{entity}", json={"attributes": {"replicas": 4}}
        )
        assert updated.status_code == 200 and updated.json()["item"]["revision"] == 2
        csv = "name,replicas,environment\ninventory,2,production\ncatalog,1,staging\n"
        preview = await client.post(
            "/api/inventory/import/preview", json={"csv": csv, "space_id": space}
        )
        assert preview.json()["valid_count"] == 2
        imported = await client.post("/api/inventory/import", json={"csv": csv, "space_id": space})
        assert imported.status_code == 200 and imported.json()["imported"] == 2
        listed = (await client.get("/api/inventory")).json()["items"]
        assert len(listed) == 3
        compared = await client.post(
            "/api/inventory/compare", json={"ids": [row["id"] for row in listed[:2]]}
        )
        assert compared.status_code == 200 and "replicas" in compared.json()["fields"]
        assert (await client.delete(f"/api/inventory/{entity}")).status_code == 200
        assert (await client.get("/api/inventory")).json()["total"] == 2
        assert (await client.get("/api/inventory", params={"include_archived": True})).json()[
            "total"
        ] == 3


@pytest.mark.integration
async def test_integration_scopes_rotation_revocation_and_safe_metadata(database, account_factory):
    owner = await account_factory()
    org, space, client = await setup_company(owner)
    async with client:
        created = await client.post(
            "/api/integrations",
            json={
                "name": "Read integration",
                "scopes": ["read"],
                "grants": [{"space_id": space, "permission": "read"}],
                "expires_in_days": 1,
            },
        )
        assert created.status_code == 201, created.text
        service = created.json()["integration"]["id"]
        key = created.json()["key"]
        listing = await client.get("/api/integrations")
        assert key not in listing.text and '"digest"' not in listing.text
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {key}"},
        ) as integration:
            assert (await integration.get("/api/inventory")).status_code == 200
            assert (await integration.get("/api/integrations")).status_code == 403
            assert (
                await integration.post("/api/inventory", json={"name": "Denied", "space_id": space})
            ).status_code == 403
            rotated = await client.post(
                f"/api/integrations/{service}/rotate",
                json={"scopes": ["read"], "expires_in_days": 1},
            )
            assert rotated.status_code == 200
            assert (await integration.get("/api/inventory")).status_code == 401
            integration.headers["Authorization"] = f"Bearer {rotated.json()['key']}"
            assert (await integration.get("/api/inventory")).status_code == 200
            keys = (await client.get("/api/integrations")).json()["items"][0]["keys"]
            active = next(row for row in keys if row["revoked_at"] is None)
            assert (
                await client.delete(f"/api/integrations/{service}/keys/{active['id']}")
            ).status_code == 200
            assert (await integration.get("/api/inventory")).status_code == 401


@pytest.mark.integration
async def test_admin_limits_and_delegated_evaluation_gate(database, account_factory):
    owner = await account_factory()
    member = await account_factory()
    org, space, client = await setup_company(owner)
    async with client:
        invite = await owner.client.post(
            f"/api/organizations/{org['id']}/invitations",
            json={"email": member.email, "role": "editor"},
        )
        assert invite.status_code == 201
        token = await inbox_token(member.email, "invite")
        assert (
            await member.client.post("/api/organizations/invitations/accept", json={"token": token})
        ).status_code == 200
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://test",
            cookies=member.client.cookies,
            headers={"X-Atlas-Tenant": org["id"]},
        ) as editor:
            assert (await editor.get("/api/administration/limits")).status_code == 403
            assert (await editor.get("/api/evaluation/labels")).status_code == 403
            policy = await client.put(
                f"/api/administration/limits/members/{member.user_id}",
                json={"monthly_tokens": 50000, "can_evaluate": True},
            )
            assert policy.status_code == 200, policy.text
            assert (await editor.get("/api/evaluation/labels")).status_code == 200
            assert (await editor.post("/api/evaluation/jobs", json={})).status_code == 409
            assert (
                await editor.post(
                    "/api/inventory", json={"name": "Editor record", "space_id": space}
                )
            ).status_code == 201
        limits = await client.patch(
            "/api/administration/limits", json={"requests_per_minute": 20, "monthly_tokens": 200000}
        )
        assert limits.status_code == 200
        assert (await client.get("/api/administration/limits")).json()["limits"][
            "monthly_tokens"
        ] == 200000
        assert (await client.get("/api/administration/audit")).json()["total"] > 0
        usage = await client.get("/api/administration/usage")
        assert usage.status_code == 200
        assert "question" not in usage.text and "answer" not in usage.text


@pytest.mark.integration
async def test_revoked_completion_is_bounded_and_settles_once(database, account_factory):
    """Real SQL cleanup simulates an already-admitted request losing its session mid-flight."""
    from uuid import UUID

    import psycopg

    from atlas.auth import Identity
    from atlas.config import settings
    from atlas.db import access_context, bind_identity, transaction
    from atlas.serving import limits, reserve

    owner = await account_factory()
    outsider = await account_factory()
    org, _, client = await setup_company(owner)
    await client.aclose()
    tenant = UUID(org["id"])
    session_id = UUID((await owner.client.get("/api/auth/me")).json()["session_id"])
    identity = Identity(
        tenant,
        session_id,
        org["name"],
        ["read", "write", "query", "admin"],
        user_id=owner.user_id,
        principal_kind="user",
        principal_id=owner.user_id,
        session_id=session_id,
        role="owner",
    )
    context_token = bind_identity(identity)
    operation, other_operation, conversation, message = uuid4(), uuid4(), uuid4(), uuid4()
    try:
        await limits(tenant)
        await reserve(tenant, operation, 100)
        async with transaction(tenant) as conn:
            await conn.execute(
                "INSERT INTO atlas.queries(tenant_id,id,question,answer,status,user_id,principal_id,principal_kind) VALUES(%s,%s,'Private question','Partial answer','running',%s,%s,'user')",
                (tenant, operation, owner.user_id, owner.user_id),
            )
            await conn.execute(
                "INSERT INTO atlas.conversations(tenant_id,id,user_id,title) VALUES(%s,%s,%s,'Private test')",
                (tenant, conversation, owner.user_id),
            )
            await conn.execute(
                "INSERT INTO atlas.messages(tenant_id,id,conversation_id,user_id,role,content,status,request_id) VALUES(%s,%s,%s,%s,'assistant','Partial answer','running',%s)",
                (tenant, message, conversation, owner.user_id, operation),
            )
        with psycopg.connect(settings.database_admin_url) as conn:
            conn.execute(
                "INSERT INTO atlas.queries(tenant_id,id,question,answer,status,user_id,principal_id,principal_kind) VALUES(%s,%s,'Another private question','Keep private','running',%s,%s,'user')",
                (tenant, other_operation, outsider.user_id, outsider.user_id),
            )
            conn.execute(
                "UPDATE atlas.user_sessions SET revoked_at=now() WHERE id=%s", (session_id,)
            )
        async with transaction(tenant) as conn:
            assert not (
                await (
                    await conn.execute(
                        "SELECT atlas.finalize_revoked_attempt(%s,20,15,5,100,'test') AS done",
                        (other_operation,),
                    )
                ).fetchone()
            )["done"]
            assert (
                await (
                    await conn.execute(
                        "SELECT atlas.finalize_revoked_attempt(%s,20,15,5,100,'test') AS done",
                        (operation,),
                    )
                ).fetchone()
            )["done"]
            await conn.execute(
                "SELECT atlas.finalize_revoked_attempt(%s,20,15,5,100,'test')", (operation,)
            )
        with psycopg.connect(settings.database_admin_url) as conn:
            assert conn.execute(
                "SELECT answer,status FROM atlas.queries WHERE tenant_id=%s AND id=%s",
                (tenant, operation),
            ).fetchone() == ("", "cancelled")
            assert conn.execute(
                "SELECT answer,status FROM atlas.queries WHERE tenant_id=%s AND id=%s",
                (tenant, other_operation),
            ).fetchone() == ("Keep private", "running")
            assert conn.execute(
                "SELECT content,status FROM atlas.messages WHERE tenant_id=%s AND id=%s",
                (tenant, message),
            ).fetchone() == ("", "unavailable")
            assert conn.execute(
                "SELECT reserved_tokens,used_tokens FROM atlas.budget_periods WHERE tenant_id=%s",
                (tenant,),
            ).fetchone() == (0, 20)
            assert (
                conn.execute(
                    "SELECT count(*) FROM atlas.usage_ledger WHERE tenant_id=%s AND request_id=%s",
                    (tenant, operation),
                ).fetchone()[0]
                == 1
            )
    finally:
        access_context.reset(context_token)


@pytest.mark.integration
async def test_evaluation_hides_judgments_when_non_gold_evidence_is_revoked(
    database, account_factory
):
    """Stored synthetic results exercise real RLS for both new and legacy dependency formats."""
    import hashlib

    import psycopg
    from psycopg.types.json import Jsonb

    from atlas.config import settings

    owner = await account_factory()
    reviewer = await account_factory()
    org, general, client = await setup_company(owner)
    async with client:
        invite = await owner.client.post(
            f"/api/organizations/{org['id']}/invitations",
            json={"email": reviewer.email, "role": "editor"},
        )
        assert invite.status_code == 201
        token = await inbox_token(reviewer.email, "invite")
        await reviewer.client.post("/api/organizations/invitations/accept", json={"token": token})
        assert (
            await client.put(
                f"/api/administration/limits/members/{reviewer.user_id}",
                json={"can_evaluate": True},
            )
        ).status_code == 200
        restricted, gold_doc, extra_doc, gold_version, extra_version, chunk, label = [
            uuid4() for _ in range(7)
        ]
        runs = [uuid4(), uuid4()]
        with psycopg.connect(settings.database_admin_url) as conn:
            conn.execute(
                "INSERT INTO atlas.spaces(tenant_id,id,name,visibility) VALUES(%s,%s,'Restricted comparison','restricted')",
                (org["id"], restricted),
            )
            conn.execute(
                "INSERT INTO atlas.resource_grants(tenant_id,id,space_id,subject_type,subject_id,permission) VALUES(%s,%s,%s,'user',%s,'read')",
                (org["id"], uuid4(), restricted, reviewer.user_id),
            )
            for document, version, space, content in [
                (gold_doc, gold_version, general, "Public gold evidence"),
                (extra_doc, extra_version, restricted, "Sensitive auxiliary evidence"),
            ]:
                content_hash = hashlib.sha256(content.encode()).hexdigest()
                conn.execute(
                    "INSERT INTO atlas.documents(tenant_id,id,title,source_key,content_hash,content,status,space_id) VALUES(%s,%s,%s,%s,%s,%s,'ready',%s)",
                    (org["id"], document, content, str(document), content_hash, content, space),
                )
                conn.execute(
                    "INSERT INTO atlas.document_versions(tenant_id,id,document_id,number,title,content,content_hash,status) VALUES(%s,%s,%s,1,%s,%s,%s,'ready')",
                    (org["id"], version, document, content, content, content_hash),
                )
                conn.execute(
                    "UPDATE atlas.documents SET current_version_id=%s WHERE tenant_id=%s AND id=%s",
                    (version, org["id"], document),
                )
            conn.execute(
                "INSERT INTO atlas.chunks(tenant_id,id,document_id,pipeline_hash,ordinal,start_offset,end_offset,content,content_hash,version_id) VALUES(%s,%s,%s,'fixture',0,0,28,'Sensitive auxiliary evidence','fixture',%s)",
                (org["id"], chunk, extra_doc, extra_version),
            )
            conn.execute(
                "INSERT INTO atlas.eval_labels(tenant_id,id,question,document_id,source_hash,start_offset,end_offset,split) VALUES(%s,%s,'A synthetic question?',%s,%s,0,5,'development')",
                (org["id"], label, gold_doc, hashlib.sha256(b"Public gold evidence").hexdigest()),
            )
            base_result = {
                "label_id": str(label),
                "question": "A synthetic question?",
                "expected": [],
                "retrieved": [str(chunk)],
                "judgment": {"answer": "Sensitive auxiliary evidence", "score": 1},
                "recall_at_5": 1,
            }
            for index, run_id in enumerate(runs):
                result = dict(base_result)
                if index == 0:
                    result["dependencies"] = [
                        {
                            "document_id": str(extra_doc),
                            "version_id": str(extra_version),
                            "kind": "document",
                        },
                        {
                            "document_id": str(gold_doc),
                            "version_id": str(gold_version),
                            "kind": "document",
                        },
                    ]
                conn.execute(
                    "INSERT INTO atlas.eval_runs(tenant_id,id,mode,config,label_count,metrics,results,manifest) VALUES(%s,%s,'hybrid',%s,1,%s,%s,'{}')",
                    (
                        org["id"],
                        run_id,
                        Jsonb({"split": "development"}),
                        Jsonb({"recall_at_5": 1}),
                        Jsonb([result]),
                    ),
                )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://test",
            cookies=reviewer.client.cookies,
            headers={"X-Atlas-Tenant": org["id"]},
        ) as delegated:
            before = await delegated.get("/api/evaluation/runs")
            assert before.status_code == 200 and "Sensitive auxiliary evidence" in before.text
            with psycopg.connect(settings.database_admin_url) as conn:
                conn.execute(
                    "DELETE FROM atlas.resource_grants WHERE tenant_id=%s AND subject_type='user' AND subject_id=%s",
                    (org["id"], reviewer.user_id),
                )
            after = await delegated.get("/api/evaluation/runs")
            assert after.status_code == 200 and "Sensitive auxiliary evidence" not in after.text
            assert all(row["unavailable"] and row["results"] == [] for row in after.json())
            comparison = await delegated.get(
                "/api/evaluation/compare", params={"left": str(runs[0]), "right": str(runs[1])}
            )
            assert comparison.status_code == 403
            with psycopg.connect(settings.database_admin_url) as conn:
                conn.execute(
                    "UPDATE atlas.documents SET lifecycle='archived' WHERE tenant_id=%s AND id=%s",
                    (org["id"], gold_doc),
                )
            assert (await delegated.get("/api/evaluation/labels")).json() == []


@pytest.mark.integration
async def test_durable_evaluation_uses_app_role_and_records_judge_usage(
    database, account_factory, worker_database, monkeypatch
):
    """Real queue, worker, RLS and accounting; retrieval/generation/judge are explicit stubs."""
    import hashlib

    import psycopg

    from atlas import db, evaluation, generation, judge
    from atlas.config import settings
    from atlas.ingestion import pipeline_hash

    owner = await account_factory()
    org, space, client = await setup_company(owner)
    document, version, chunk = uuid4(), uuid4(), uuid4()
    content = "Synthetic evaluation evidence."
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "INSERT INTO atlas.documents(tenant_id,id,title,source_key,content_hash,content,status,space_id) VALUES(%s,%s,'Evaluation fixture',%s,%s,%s,'ready',%s)",
            (org["id"], document, str(document), content_hash, content, space),
        )
        conn.execute(
            "INSERT INTO atlas.document_versions(tenant_id,id,document_id,number,title,content,content_hash,status) VALUES(%s,%s,%s,1,'Evaluation fixture',%s,%s,'ready')",
            (org["id"], version, document, content, content_hash),
        )
        conn.execute(
            "UPDATE atlas.documents SET current_version_id=%s WHERE tenant_id=%s AND id=%s",
            (version, org["id"], document),
        )
        conn.execute(
            "INSERT INTO atlas.chunks(tenant_id,id,document_id,pipeline_hash,ordinal,start_offset,end_offset,content,content_hash,version_id) VALUES(%s,%s,%s,%s,0,0,%s,%s,%s,%s)",
            (
                org["id"],
                chunk,
                document,
                pipeline_hash(1000),
                len(content),
                content,
                content_hash,
                version,
            ),
        )
        for index in range(40):
            label_document = document
            if index:
                label_document = uuid4()
                conn.execute(
                    "INSERT INTO atlas.documents(tenant_id,id,title,source_key,content_hash,content,status,space_id) VALUES(%s,%s,'Held out fixture',%s,%s,%s,'ready',%s)",
                    (org["id"], label_document, str(label_document), content_hash, content, space),
                )
            label = {
                "question": f"What does the synthetic evidence say {index}?",
                "document_id": label_document,
                "source_hash": content_hash,
                "start_offset": 0,
                "end_offset": 9,
                "split": "development" if index == 0 else "held_out",
            }
            conn.execute(
                "INSERT INTO atlas.eval_labels(tenant_id,id,question,document_id,source_hash,start_offset,end_offset,split,reviewed,review_hash,reviewed_by,reviewed_at) VALUES(%s,%s,%s,%s,%s,0,9,%s,true,%s,%s,now())",
                (
                    org["id"],
                    uuid4(),
                    label["question"],
                    label_document,
                    content_hash,
                    label["split"],
                    evaluation.label_hash(label),
                    owner.user_id,
                ),
            )

    async def retrieve_stub(tenant, *args, **kwargs):
        async with db.transaction(tenant) as conn:
            role = await (
                await conn.execute("SELECT session_user,atlas.can_evaluate() AS allowed")
            ).fetchone()
            assert role == {"session_user": "atlas_app", "allowed": True}
        return (
            [
                {
                    "id": str(chunk),
                    "document_id": str(document),
                    "version_id": str(version),
                    "kind": "document",
                    "title": "Evaluation fixture",
                    "content": content,
                }
            ],
            None,
            None,
            None,
        )

    async def generate_stub(*args):
        meter = generation.meter_context.get()
        meter.begin()
        meter.finish(10, 3)
        yield {"type": "delta", "text": "Synthetic answer."}

    async def judge_stub(*args):
        meter = generation.meter_context.get()
        meter.begin()
        meter.finish(5, 2)
        return {"score": 1}

    monkeypatch.setattr(evaluation, "retrieve", retrieve_stub)
    monkeypatch.setattr(generation, "generate", generate_stub)
    monkeypatch.setattr(judge, "judge_answer", judge_stub)
    async with client:
        async with db.application_transactions():
            response = await client.post("/api/evaluation/jobs", json={"with_judge": True})
        assert response.status_code == 202, response.text
        job = response.json()["job"]["id"]
        await evaluation.process_evaluation_job(org["id"], job, "integration-worker")
        async with db.application_transactions():
            result = (await client.get(f"/api/evaluation/jobs/{job}")).json()["job"]
        assert result["status"] == "completed", result
        assert result["progress"] == result["total"] == 1
        with psycopg.connect(settings.database_admin_url) as conn:
            ledger = conn.execute(
                "SELECT input_tokens,output_tokens,actual_api_cost_usd,status,user_id,principal_id,principal_kind,duration_ms FROM atlas.usage_ledger WHERE tenant_id=%s AND operation='evaluation'",
                (org["id"],),
            ).fetchall()
            assert len(ledger) == 1
            assert ledger[0][:7] == (15, 5, 0, "completed", owner.user_id, owner.user_id, "user")
            assert ledger[0][7] >= 0
            assert (
                conn.execute(
                    "SELECT used_tokens FROM atlas.reservations WHERE tenant_id=%s", (org["id"],)
                ).fetchone()[0]
                == 20
            )
        async with db.application_transactions():
            queued = await client.post("/api/evaluation/jobs", json={"with_judge": True})
        assert queued.status_code == 202
        with psycopg.connect(settings.database_admin_url) as conn:
            conn.execute(
                "UPDATE atlas.user_sessions SET revoked_at=now() WHERE user_id=%s", (owner.user_id,)
            )
        await evaluation.process_evaluation_job(
            org["id"], queued.json()["job"]["id"], "integration-worker"
        )
        with psycopg.connect(settings.database_admin_url) as conn:
            assert conn.execute(
                "SELECT status,error_code FROM atlas.evaluation_jobs WHERE id=%s",
                (queued.json()["job"]["id"],),
            ).fetchone() == ("failed", "access_revoked")
            assert (
                conn.execute(
                    "SELECT count(*) FROM atlas.usage_ledger WHERE tenant_id=%s", (org["id"],)
                ).fetchone()[0]
                == 1
            )
