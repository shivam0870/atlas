"""Erase historical evaluation derivations that referenced chunk IDs rather than document IDs."""

from alembic import op

revision = "013"
down_revision = "012"


def upgrade():
    op.execute("""
    CREATE FUNCTION atlas.erase_historical_evaluations() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    BEGIN
      DELETE FROM atlas.eval_runs r WHERE r.tenant_id=OLD.tenant_id AND EXISTS(
        SELECT 1 FROM atlas.chunks c WHERE c.tenant_id=OLD.tenant_id AND c.document_id=OLD.id
          AND r.results::text LIKE '%'||c.id::text||'%');
      RETURN OLD;
    END $$;
    REVOKE ALL ON FUNCTION atlas.erase_historical_evaluations() FROM PUBLIC;
    CREATE TRIGGER erase_01_historical_evaluations BEFORE DELETE ON atlas.documents
      FOR EACH ROW EXECUTE FUNCTION atlas.erase_historical_evaluations();
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup")
