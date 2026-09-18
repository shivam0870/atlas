"""Evaluate read grants once per statement instead of repeatedly for every indexed row."""

from alembic import op

revision = "015"
down_revision = "014"


def upgrade():
    op.execute("""
    CREATE FUNCTION atlas.readable_document_ids() RETURNS uuid[]
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      SELECT coalesce(array_agg(d.id),'{}'::uuid[]) FROM atlas.documents d
        WHERE d.tenant_id=atlas.context_tenant() AND atlas.can_document_row(d.space_id,d.id,d.restricted)
    $$;
    CREATE FUNCTION atlas.readable_chunk_ids() RETURNS uuid[]
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      SELECT coalesce(array_agg(c.id),'{}'::uuid[]) FROM atlas.chunks c
        WHERE c.tenant_id=atlas.context_tenant() AND c.document_id=ANY((SELECT atlas.readable_document_ids())::uuid[])
    $$;
    REVOKE ALL ON FUNCTION atlas.readable_document_ids(),atlas.readable_chunk_ids() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION atlas.readable_document_ids(),atlas.readable_chunk_ids() TO atlas_app;
    DROP POLICY resource_scope ON atlas.chunks;
    CREATE POLICY resource_scope ON atlas.chunks AS RESTRICTIVE TO atlas_app
      USING(document_id=ANY((SELECT atlas.readable_document_ids())::uuid[]))
      WITH CHECK(document_id=ANY((SELECT atlas.readable_document_ids())::uuid[]));
    DROP POLICY resource_scope ON atlas.embeddings;
    CREATE POLICY resource_scope ON atlas.embeddings AS RESTRICTIVE TO atlas_app
      USING(chunk_id=ANY((SELECT atlas.readable_chunk_ids())::uuid[]))
      WITH CHECK(chunk_id=ANY((SELECT atlas.readable_chunk_ids())::uuid[]));
    DROP POLICY resource_scope ON atlas.chunk_terms;
    CREATE POLICY resource_scope ON atlas.chunk_terms AS RESTRICTIVE TO atlas_app
      USING(chunk_id=ANY((SELECT atlas.readable_chunk_ids())::uuid[]))
      WITH CHECK(chunk_id=ANY((SELECT atlas.readable_chunk_ids())::uuid[]));
    DROP POLICY resource_scope ON atlas.document_versions;
    CREATE POLICY resource_scope ON atlas.document_versions AS RESTRICTIVE FOR SELECT TO atlas_app
      USING(document_id=ANY((SELECT atlas.readable_document_ids())::uuid[]));
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup")
