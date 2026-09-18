"""Real database checks for batched evidence authorization; no model calls."""

from uuid import uuid4

import psycopg
import pytest
import test_resource_access
from test_conversations import source_document

from atlas.config import settings
from atlas.conversations import sources_available
from atlas.db import bind_identity

resource_workspace = test_resource_access.resource_workspace


@pytest.mark.integration
async def test_batched_sources_reject_partial_versions_and_malformed_references(
    database, resource_workspace
):
    ws = resource_workspace
    viewer = ws.identities[2]
    bind_identity(viewer)
    first = source_document(ws.tenant, "First allowed evidence")
    second = source_document(ws.tenant, "Second allowed evidence")
    assert await sources_available(viewer, [])
    assert await sources_available(viewer, [first, second, first, second])
    assert await sources_available(viewer, [{"document_id": first["document_id"]}, first])
    for invalid in [
        {**first, "version_id": str(uuid4())},
        {**first, "version_id": second["version_id"]},
        {**first, "document_id": str(uuid4())},
        {**first, "document_id": "malformed"},
        {**first, "version_id": ""},
        {**first, "kind": "unknown"},
        {"id": "entity:bad", "kind": "structured"},
        {"id": str(uuid4()), "kind": "unknown"},
        {},
        None,
    ]:
        assert not await sources_available(viewer, [first, second, invalid])
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "UPDATE atlas.document_versions SET status='failed' WHERE tenant_id=%s AND id=%s",
            (ws.tenant, second["version_id"]),
        )
    assert not await sources_available(viewer, [first, second])
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "UPDATE atlas.document_versions SET status='ready' WHERE tenant_id=%s AND id=%s",
            (ws.tenant, second["version_id"]),
        )
        conn.execute(
            "UPDATE atlas.documents SET lifecycle='archived' WHERE tenant_id=%s AND id=%s",
            (ws.tenant, second["document_id"]),
        )
    assert not await sources_available(viewer, [first, second])
    assert await sources_available(viewer, [first])


@pytest.mark.integration
async def test_batched_mixed_entities_recheck_grants_archive_and_empty_session(
    database, resource_workspace
):
    ws = resource_workspace
    viewer = ws.identities[2]
    bind_identity(viewer)
    document = source_document(ws.tenant)
    entity, space, grant = uuid4(), uuid4(), uuid4()
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            "INSERT INTO atlas.spaces(tenant_id,id,name,visibility) VALUES(%s,%s,'Inventory restriction','restricted')",
            (ws.tenant, space),
        )
        conn.execute(
            "INSERT INTO atlas.resource_grants(tenant_id,id,space_id,subject_type,subject_id) VALUES(%s,%s,%s,'user',%s)",
            (ws.tenant, grant, space, viewer.user_id),
        )
        conn.execute(
            "INSERT INTO atlas.entities(tenant_id,id,kind,name,attributes,space_id) VALUES(%s,%s,'service','Mixed fixture','{}',%s)",
            (ws.tenant, entity, space),
        )
    source = {"kind": "structured", "id": f"entity:{entity}"}
    try:
        assert await sources_available(viewer, [document, source, source, document])
        assert not await sources_available(
            viewer, [document, source, {"kind": "structured", "id": f"entity:{uuid4()}"}]
        )
        with psycopg.connect(settings.database_admin_url) as conn:
            conn.execute(
                "UPDATE atlas.entities SET archived=true WHERE tenant_id=%s AND id=%s",
                (ws.tenant, entity),
            )
        assert not await sources_available(viewer, [document, source])
        with psycopg.connect(settings.database_admin_url) as conn:
            conn.execute(
                "UPDATE atlas.entities SET archived=false WHERE tenant_id=%s AND id=%s",
                (ws.tenant, entity),
            )
            conn.execute(
                "DELETE FROM atlas.resource_grants WHERE tenant_id=%s AND id=%s", (ws.tenant, grant)
            )
        assert not await sources_available(viewer, [document, source])
        assert await sources_available(viewer, [document])
        with psycopg.connect(settings.database_admin_url) as conn:
            conn.execute(
                "UPDATE atlas.user_sessions SET revoked_at=now() WHERE id=%s", (viewer.session_id,)
            )
        assert not await sources_available(viewer, [])
        assert not await sources_available(viewer, [document])
    finally:
        with psycopg.connect(settings.database_admin_url) as conn:
            conn.execute(
                "DELETE FROM atlas.entities WHERE tenant_id=%s AND id=%s", (ws.tenant, entity)
            )
