"""Erase persisted follow-up derivations before their source dependency metadata is removed."""

from alembic import op

revision = "012"
down_revision = "011"


def upgrade():
    op.execute("""
    CREATE FUNCTION atlas.erase_dependent_answers() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    BEGIN
      UPDATE atlas.queries q SET answer='',sources='[]',status='unavailable'
        WHERE q.tenant_id=OLD.tenant_id AND q.id IN (
          SELECT m.request_id FROM atlas.messages m WHERE m.tenant_id=OLD.tenant_id
            AND (m.sources::text LIKE '%'||OLD.id::text||'%' OR m.metadata::text LIKE '%'||OLD.id::text||'%'));
      DELETE FROM atlas.bookmarks b WHERE b.tenant_id=OLD.tenant_id AND b.kind='message' AND b.resource_id IN (
        SELECT m.id FROM atlas.messages m WHERE m.tenant_id=OLD.tenant_id
          AND (m.sources::text LIKE '%'||OLD.id::text||'%' OR m.metadata::text LIKE '%'||OLD.id::text||'%'));
      RETURN OLD;
    END $$;
    REVOKE ALL ON FUNCTION atlas.erase_dependent_answers() FROM PUBLIC;
    CREATE TRIGGER erase_00_dependent_answers BEFORE DELETE ON atlas.documents
      FOR EACH ROW EXECUTE FUNCTION atlas.erase_dependent_answers();
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup")
