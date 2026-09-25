"""Tenant quotas, attributable jobs, upload quarantine, and fair outbox dispatch."""

from alembic import op

revision = "017"
down_revision = "016"


def upgrade():
    op.execute("""
    ALTER TABLE atlas.tenant_limits
      ADD COLUMN storage_bytes bigint NOT NULL DEFAULT 1073741824 CHECK(storage_bytes>=0),
      ADD COLUMN uploads_per_day integer NOT NULL DEFAULT 200 CHECK(uploads_per_day>=0),
      ADD COLUMN upload_bytes_per_day bigint NOT NULL DEFAULT 524288000 CHECK(upload_bytes_per_day>=0),
      ADD COLUMN max_pending_jobs integer NOT NULL DEFAULT 100 CHECK(max_pending_jobs>0),
      ADD COLUMN concurrent_generations integer NOT NULL DEFAULT 1 CHECK(concurrent_generations BETWEEN 1 AND 32),
      ADD COLUMN queued_generations integer NOT NULL DEFAULT 8 CHECK(queued_generations BETWEEN 1 AND 1000),
      ADD COLUMN queries_per_day integer NOT NULL DEFAULT 10000 CHECK(queries_per_day>=0);
    CREATE TABLE atlas.daily_resource_usage (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id) ON DELETE CASCADE,
      day date NOT NULL DEFAULT current_date, uploads bigint NOT NULL DEFAULT 0,
      upload_bytes bigint NOT NULL DEFAULT 0, queries bigint NOT NULL DEFAULT 0,
      PRIMARY KEY(tenant_id,day)
    );
    ALTER TABLE atlas.daily_resource_usage ENABLE ROW LEVEL SECURITY;
    ALTER TABLE atlas.daily_resource_usage FORCE ROW LEVEL SECURITY;
    CREATE POLICY resource_usage_scope ON atlas.daily_resource_usage TO atlas_app
      USING(tenant_id=atlas.context_tenant() AND atlas.can_manage());
    GRANT SELECT ON atlas.daily_resource_usage TO atlas_app;
    CREATE TABLE atlas.upload_checks (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id) ON DELETE CASCADE,
      id uuid NOT NULL, principal_id uuid, filename text NOT NULL, sha256 text NOT NULL,
      byte_size bigint NOT NULL, status text NOT NULL CHECK(status IN ('unsafe','scanner_unavailable')),
      created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,id)
    );
    ALTER TABLE atlas.upload_checks ENABLE ROW LEVEL SECURITY;
    ALTER TABLE atlas.upload_checks FORCE ROW LEVEL SECURITY;
    CREATE POLICY upload_checks_read ON atlas.upload_checks FOR SELECT TO atlas_app
      USING(tenant_id=atlas.context_tenant() AND (atlas.can_manage() OR principal_id=atlas.context_principal()));
    CREATE POLICY upload_checks_insert ON atlas.upload_checks FOR INSERT TO atlas_app
      WITH CHECK(tenant_id=atlas.context_tenant() AND principal_id=atlas.context_principal() AND atlas.principal_can_write());
    GRANT SELECT,INSERT ON atlas.upload_checks TO atlas_app;

    CREATE FUNCTION atlas.enforce_upload_quota() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    DECLARE lim atlas.tenant_limits; used atlas.daily_resource_usage; stored bigint; incoming bigint;
    BEGIN
      IF NEW.tenant_id IS DISTINCT FROM atlas.context_tenant() AND NOT (SELECT rolsuper FROM pg_roles WHERE rolname=session_user) THEN
        RAISE EXCEPTION 'Upload tenant mismatch';
      END IF;
      INSERT INTO atlas.tenant_limits(tenant_id) VALUES(NEW.tenant_id) ON CONFLICT DO NOTHING;
      SELECT * INTO lim FROM atlas.tenant_limits WHERE tenant_id=NEW.tenant_id FOR UPDATE;
      incoming=greatest(NEW.byte_size,octet_length(NEW.content));
      SELECT coalesce(sum(greatest(byte_size,octet_length(content))),0) INTO stored
        FROM atlas.document_versions WHERE tenant_id=NEW.tenant_id;
      INSERT INTO atlas.daily_resource_usage(tenant_id) VALUES(NEW.tenant_id) ON CONFLICT DO NOTHING;
      SELECT * INTO used FROM atlas.daily_resource_usage WHERE tenant_id=NEW.tenant_id AND day=current_date FOR UPDATE;
      IF stored+incoming>lim.storage_bytes OR used.uploads+1>lim.uploads_per_day OR used.upload_bytes+incoming>lim.upload_bytes_per_day THEN
        RAISE EXCEPTION 'Workspace storage or daily upload quota reached' USING ERRCODE='53000';
      END IF;
      UPDATE atlas.daily_resource_usage SET uploads=uploads+1,upload_bytes=upload_bytes+incoming
        WHERE tenant_id=NEW.tenant_id AND day=current_date;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION atlas.enforce_upload_quota() FROM PUBLIC;
    CREATE TRIGGER upload_quota BEFORE INSERT ON atlas.document_versions
      FOR EACH ROW EXECUTE FUNCTION atlas.enforce_upload_quota();
    CREATE FUNCTION atlas.charge_query() RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    DECLARE lim atlas.tenant_limits; used bigint;
    BEGIN
      IF NOT atlas.principal_active() THEN RETURN false; END IF;
      INSERT INTO atlas.tenant_limits(tenant_id) VALUES(atlas.context_tenant()) ON CONFLICT DO NOTHING;
      SELECT * INTO lim FROM atlas.tenant_limits WHERE tenant_id=atlas.context_tenant() FOR UPDATE;
      INSERT INTO atlas.daily_resource_usage(tenant_id) VALUES(atlas.context_tenant()) ON CONFLICT DO NOTHING;
      SELECT queries INTO used FROM atlas.daily_resource_usage WHERE tenant_id=atlas.context_tenant() AND day=current_date FOR UPDATE;
      IF used>=lim.queries_per_day THEN RETURN false; END IF;
      UPDATE atlas.daily_resource_usage SET queries=queries+1 WHERE tenant_id=atlas.context_tenant() AND day=current_date;
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION atlas.charge_query() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION atlas.charge_query() TO atlas_app;

    ALTER TABLE atlas.ingestion_jobs DROP CONSTRAINT ingestion_jobs_status_check;
    ALTER TABLE atlas.ingestion_jobs ADD CONSTRAINT ingestion_jobs_status_check
      CHECK(status IN ('queued','running','retry','completed','dead','cancelled'));
    ALTER TABLE atlas.ingestion_jobs
      ADD COLUMN submitter_kind text NOT NULL DEFAULT 'system',
      ADD COLUMN submitter_id uuid,
      ADD COLUMN submitter_session_id uuid,
      ADD COLUMN submitter_key_id uuid,
      ADD COLUMN version_id uuid;
    CREATE FUNCTION atlas.capture_job_authority() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    BEGIN
      IF TG_OP='INSERT' THEN
        NEW.submitter_kind=coalesce(nullif(current_setting('app.principal_kind',true),''),'system');
        NEW.submitter_id=atlas.context_principal();
        NEW.submitter_session_id=nullif(current_setting('app.session_id',true),'')::uuid;
        NEW.submitter_key_id=nullif(current_setting('app.key_id',true),'')::uuid;
        IF NEW.submitter_kind='system' AND session_user<>'atlas_worker' AND NOT (SELECT rolsuper FROM pg_roles WHERE rolname=session_user) THEN
          RAISE EXCEPTION 'An ingestion job requires an active submitter';
        END IF;
        SELECT coalesce(pending_version_id,current_version_id) INTO NEW.version_id
          FROM atlas.documents WHERE tenant_id=NEW.tenant_id AND id=NEW.document_id;
      ELSIF (NEW.submitter_kind,NEW.submitter_id,NEW.submitter_session_id,NEW.submitter_key_id,NEW.version_id)
        IS DISTINCT FROM (OLD.submitter_kind,OLD.submitter_id,OLD.submitter_session_id,OLD.submitter_key_id,OLD.version_id) THEN
        RAISE EXCEPTION 'Job authority is immutable';
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION atlas.capture_job_authority() FROM PUBLIC;
    CREATE TRIGGER job_authority BEFORE INSERT OR UPDATE ON atlas.ingestion_jobs
      FOR EACH ROW EXECUTE FUNCTION atlas.capture_job_authority();

    CREATE TABLE atlas.ingestion_dispatch_turns (
      tenant_id uuid PRIMARY KEY REFERENCES atlas.tenants(id) ON DELETE CASCADE,
      served_at timestamptz NOT NULL DEFAULT '-infinity'
    );
    ALTER TABLE atlas.ingestion_dispatch_turns ENABLE ROW LEVEL SECURITY;
    ALTER TABLE atlas.ingestion_dispatch_turns FORCE ROW LEVEL SECURITY;
    CREATE POLICY dispatch_turns ON atlas.ingestion_dispatch_turns TO atlas_dispatch USING(true) WITH CHECK(true);
    GRANT SELECT,INSERT,UPDATE ON atlas.ingestion_dispatch_turns TO atlas_dispatch;
    CREATE OR REPLACE FUNCTION atlas.dispatch_outbox() RETURNS TABLE(tenant_id uuid,id uuid,job_id uuid)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    DECLARE chosen atlas.outbox;
    BEGIN
      -- One request per call; a short queue prevents a bulk tenant from pre-filling
      -- an entire stream before another tenant becomes ready.
      SELECT o.* INTO chosen FROM atlas.outbox o
        LEFT JOIN atlas.ingestion_dispatch_turns t ON t.tenant_id=o.tenant_id
        WHERE o.published_at IS NULL AND o.available_at<=now()
        ORDER BY coalesce(t.served_at,'-infinity'::timestamptz),o.available_at,o.id
        LIMIT 1 FOR UPDATE OF o SKIP LOCKED;
      IF NOT FOUND THEN RETURN; END IF;
      INSERT INTO atlas.ingestion_dispatch_turns(tenant_id,served_at) VALUES(chosen.tenant_id,clock_timestamp())
        ON CONFLICT ON CONSTRAINT ingestion_dispatch_turns_pkey DO UPDATE SET served_at=excluded.served_at;
      RETURN QUERY SELECT chosen.tenant_id,chosen.id,chosen.job_id;
    END $$;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup; quota and authority history must be retained")
