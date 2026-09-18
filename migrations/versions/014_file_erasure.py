"""Keep file erasure durable even if a request stops after committing document deletion."""

from alembic import op

revision = "014"
down_revision = "013"


def upgrade():
    op.execute("""
    ALTER TABLE atlas.purge_jobs DROP CONSTRAINT purge_jobs_kind_check;
    ALTER TABLE atlas.purge_jobs ADD CHECK(kind IN ('tenant','user','document'));
    CREATE FUNCTION atlas.schedule_document_file_erasure() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    BEGIN
      INSERT INTO atlas.purge_jobs(kind,target_id,status,due_at,storage_keys)
        SELECT 'document',OLD.id,'files',now(),jsonb_agg(v.storage_key)
        FROM atlas.document_versions v WHERE v.tenant_id=OLD.tenant_id AND v.document_id=OLD.id AND v.storage_key IS NOT NULL
        HAVING count(*)>0 ON CONFLICT(kind,target_id) DO NOTHING;
      RETURN OLD;
    END $$;
    REVOKE ALL ON FUNCTION atlas.schedule_document_file_erasure() FROM PUBLIC;
    CREATE TRIGGER erase_02_document_files BEFORE DELETE ON atlas.documents
      FOR EACH ROW EXECUTE FUNCTION atlas.schedule_document_file_erasure();
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup")
