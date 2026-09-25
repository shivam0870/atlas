"""Keep tenant job capacity enforcement independent of document visibility."""

from alembic import op

revision = "023"
down_revision = "022"


def upgrade():
    op.execute("""
    CREATE FUNCTION atlas.pending_job_count() RETURNS bigint
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    BEGIN
      IF NOT atlas.principal_can_write() THEN RAISE EXCEPTION 'Indexing access required' USING ERRCODE='42501'; END IF;
      RETURN (SELECT count(*) FROM atlas.ingestion_jobs WHERE tenant_id=atlas.context_tenant() AND status IN ('queued','running','retry'));
    END $$;
    REVOKE ALL ON FUNCTION atlas.pending_job_count() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION atlas.pending_job_count() TO atlas_app;
    CREATE FUNCTION atlas.enforce_job_capacity() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    DECLARE capacity integer; pending bigint;
    BEGIN
      IF NEW.status NOT IN ('queued','running','retry') OR
        (TG_OP='UPDATE' AND OLD.status IN ('queued','running','retry')) THEN RETURN NEW; END IF;
      -- Serialize admissions even if two editors cannot see each other's jobs.
      PERFORM id FROM atlas.tenants WHERE id=NEW.tenant_id FOR UPDATE;
      SELECT max_pending_jobs INTO capacity FROM atlas.tenant_limits WHERE tenant_id=NEW.tenant_id;
      SELECT count(*) INTO pending FROM atlas.ingestion_jobs WHERE tenant_id=NEW.tenant_id
        AND status IN ('queued','running','retry') AND id<>NEW.id;
      IF pending>=coalesce(capacity,100) THEN
        RAISE EXCEPTION 'Workspace indexing capacity reached' USING ERRCODE='53000';
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION atlas.enforce_job_capacity() FROM PUBLIC;
    CREATE TRIGGER job_capacity BEFORE INSERT OR UPDATE OF status ON atlas.ingestion_jobs
      FOR EACH ROW EXECUTE FUNCTION atlas.enforce_job_capacity();
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup")
