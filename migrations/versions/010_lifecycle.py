"""Durable retention cleanup, source erasure and integration last-use metadata."""

from alembic import op

revision = "010"
down_revision = "009"


def upgrade():
    op.execute("""
    CREATE OR REPLACE FUNCTION atlas.resolve_api_identity(key_digest text)
    RETURNS TABLE(tenant_id uuid,key_id uuid,name text,scopes text[],service_account_id uuid,auth_revision bigint)
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      WITH valid AS (
        SELECT k.id FROM atlas.api_keys k JOIN atlas.tenants t ON t.id=k.tenant_id
        JOIN atlas.service_accounts s ON s.tenant_id=k.tenant_id AND s.id=k.service_account_id
        WHERE k.digest=key_digest AND k.revoked_at IS NULL AND (k.expires_at IS NULL OR k.expires_at>now())
          AND t.status='active' AND s.active AND (NOT s.legacy OR t.claimed_at IS NULL)
      ), touched AS (
        UPDATE atlas.api_keys SET last_used_at=now() WHERE id IN (SELECT id FROM valid)
        RETURNING atlas.api_keys.*
      ) SELECT k.tenant_id,k.id,t.name,k.scopes,k.service_account_id,t.auth_revision
        FROM touched k JOIN atlas.tenants t ON t.id=k.tenant_id
    $$;
    DROP TRIGGER audit_change ON atlas.api_keys;
    CREATE TRIGGER audit_change AFTER UPDATE ON atlas.api_keys
      FOR EACH ROW WHEN ((to_jsonb(OLD)-'last_used_at') IS DISTINCT FROM (to_jsonb(NEW)-'last_used_at'))
      EXECUTE FUNCTION atlas.audit_change();
    CREATE TRIGGER audit_key_creation AFTER INSERT OR DELETE ON atlas.api_keys
      FOR EACH ROW EXECUTE FUNCTION atlas.audit_change();
    DROP POLICY resource_scope ON atlas.spaces;
    CREATE POLICY resource_scope ON atlas.spaces AS RESTRICTIVE FOR SELECT TO atlas_app
      USING(atlas.can_manage() OR atlas.can_space(id));
    CREATE OR REPLACE FUNCTION atlas.can_document_row(space uuid,document uuid,is_restricted boolean,write_access boolean DEFAULT false) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      SELECT atlas.can_manage() OR (atlas.can_space(space,write_access) AND (NOT is_restricted OR atlas.has_grant(NULL,document,write_access)))
    $$;
    CREATE FUNCTION atlas.erase_document_derivatives() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    BEGIN
      DELETE FROM atlas.embedding_cache e WHERE e.tenant_id=OLD.tenant_id
        AND e.content_hash IN(SELECT c.content_hash FROM atlas.chunks c WHERE c.tenant_id=OLD.tenant_id AND c.document_id=OLD.id)
        AND NOT EXISTS(SELECT 1 FROM atlas.chunks c WHERE c.tenant_id=OLD.tenant_id AND c.document_id<>OLD.id AND c.content_hash=e.content_hash);
      UPDATE atlas.messages SET content='',sources='[]',status='unavailable',metadata=jsonb_build_object('source_deleted',true)
        WHERE tenant_id=OLD.tenant_id AND (sources::text LIKE '%'||OLD.id::text||'%' OR metadata::text LIKE '%'||OLD.id::text||'%');
      UPDATE atlas.queries SET answer='',sources='[]',status='unavailable'
        WHERE tenant_id=OLD.tenant_id AND sources::text LIKE '%'||OLD.id::text||'%';
      UPDATE atlas.feedback SET sources='[]',question='',correction=''
        WHERE tenant_id=OLD.tenant_id AND sources::text LIKE '%'||OLD.id::text||'%';
      DELETE FROM atlas.bookmarks WHERE tenant_id=OLD.tenant_id AND kind='document' AND resource_id=OLD.id;
      DELETE FROM atlas.eval_runs WHERE tenant_id=OLD.tenant_id AND (results::text LIKE '%'||OLD.id::text||'%' OR manifest::text LIKE '%'||OLD.id::text||'%');
      UPDATE atlas.entities SET document_ids=array_remove(document_ids,OLD.id) WHERE tenant_id=OLD.tenant_id AND OLD.id=ANY(document_ids);
      RETURN OLD;
    END $$;
    REVOKE ALL ON FUNCTION atlas.erase_document_derivatives() FROM PUBLIC;
    CREATE TRIGGER erase_derivatives BEFORE DELETE ON atlas.documents FOR EACH ROW EXECUTE FUNCTION atlas.erase_document_derivatives();
    CREATE TABLE atlas.purge_jobs (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),kind text NOT NULL CHECK(kind IN ('tenant','user')),
      target_id uuid NOT NULL,due_at timestamptz NOT NULL DEFAULT now()+interval '30 days',
      status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','files','completed')),
      storage_keys jsonb NOT NULL DEFAULT '[]',created_at timestamptz NOT NULL DEFAULT now(),completed_at timestamptz,
      UNIQUE(kind,target_id)
    );
    REVOKE ALL ON atlas.purge_jobs FROM PUBLIC,atlas_app,atlas_identity;
    CREATE FUNCTION atlas.schedule_retention() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    BEGIN
      IF TG_TABLE_NAME='tenants' THEN
        IF NEW.status='suspended' AND OLD.status IS DISTINCT FROM NEW.status THEN
          INSERT INTO atlas.purge_jobs(kind,target_id) VALUES('tenant',NEW.id) ON CONFLICT DO NOTHING;
        END IF;
      ELSIF NEW.disabled_at IS NOT NULL AND OLD.disabled_at IS NULL THEN
        INSERT INTO atlas.purge_jobs(kind,target_id) VALUES('user',NEW.id) ON CONFLICT DO NOTHING;
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION atlas.schedule_retention() FROM PUBLIC;
    CREATE TRIGGER schedule_retention AFTER UPDATE ON atlas.tenants FOR EACH ROW EXECUTE FUNCTION atlas.schedule_retention();
    CREATE TRIGGER schedule_retention AFTER UPDATE ON atlas.users FOR EACH ROW EXECUTE FUNCTION atlas.schedule_retention();
    CREATE FUNCTION atlas.process_retention() RETURNS TABLE(job_id uuid,kind text,target_id uuid,storage_keys jsonb)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    DECLARE j atlas.purge_jobs; tab text;
    BEGIN
      SELECT * INTO j FROM atlas.purge_jobs p WHERE (p.status='pending' AND p.due_at<=now()) OR p.status='files'
        ORDER BY due_at LIMIT 1 FOR UPDATE SKIP LOCKED;
      IF NOT FOUND THEN RETURN; END IF;
      IF j.status='pending' AND j.kind='tenant' THEN
        IF EXISTS(SELECT 1 FROM atlas.tenants t WHERE t.id=j.target_id AND t.status<>'suspended') THEN RETURN; END IF;
        SELECT coalesce(jsonb_agg(v.storage_key) FILTER(WHERE v.storage_key IS NOT NULL),'[]') INTO j.storage_keys
          FROM atlas.document_versions v WHERE v.tenant_id=j.target_id;
        UPDATE atlas.documents SET current_version_id=NULL,pending_version_id=NULL WHERE tenant_id=j.target_id;
        FOREACH tab IN ARRAY ARRAY['outbox','ingestion_jobs','evaluation_jobs','eval_runs','eval_labels','bookmarks','messages','conversations',
          'queries','feedback','notifications','access_requests','resource_grants','entities','chunk_terms','embeddings','chunks','document_versions',
          'embedding_cache','documents','collections','reservations','budget_periods','tenant_limits','usage_ledger','team_members','memberships',
          'invitations','teams','api_keys','service_accounts','spaces','audit_events'] LOOP
          EXECUTE format('DELETE FROM atlas.%I WHERE tenant_id=$1',tab) USING j.target_id;
        END LOOP;
        DELETE FROM atlas.tenants WHERE id=j.target_id;
        UPDATE atlas.purge_jobs p SET status='files',storage_keys=j.storage_keys WHERE p.id=j.id;
      ELSIF j.status='pending' AND j.kind='user' THEN
        IF NOT EXISTS(SELECT 1 FROM atlas.users u WHERE u.id=j.target_id AND u.disabled_at IS NOT NULL) THEN RETURN; END IF;
        DELETE FROM atlas.queries WHERE user_id=j.target_id;
        DELETE FROM atlas.conversations WHERE user_id=j.target_id;
        DELETE FROM atlas.bookmarks WHERE user_id=j.target_id;
        DELETE FROM atlas.notifications WHERE user_id=j.target_id;
        DELETE FROM atlas.account_tokens WHERE user_id=j.target_id;
        DELETE FROM atlas.recovery_codes WHERE user_id=j.target_id;
        UPDATE atlas.evaluation_jobs SET session_id=NULL WHERE user_id=j.target_id;
        DELETE FROM atlas.user_sessions WHERE user_id=j.target_id;
        UPDATE atlas.users SET email='deleted-'||id::text||'@deleted.invalid',name='Deleted member',password_hash='!',mfa_secret=NULL,
          mfa_pending_secret=NULL,email_verified_at=NULL WHERE id=j.target_id;
        UPDATE atlas.purge_jobs p SET status='files' WHERE p.id=j.id;
      END IF;
      RETURN QUERY SELECT j.id,j.kind,j.target_id,j.storage_keys;
    END $$;
    CREATE FUNCTION atlas.finish_retention(job uuid) RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      UPDATE atlas.purge_jobs SET status='completed',storage_keys='[]',completed_at=now() WHERE id=job AND status='files'
    $$;
    REVOKE ALL ON FUNCTION atlas.process_retention(),atlas.finish_retention(uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION atlas.process_retention(),atlas.finish_retention(uuid) TO atlas_worker;
    CREATE FUNCTION atlas.deliver_maintenance_notifications() RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      INSERT INTO atlas.notifications(tenant_id,id,user_id,kind,title,body,link,dedupe_key)
        SELECT d.tenant_id,gen_random_uuid(),d.owner_user_id,'review_due','Knowledge review due',
          'A document you own is ready for review.','/library/'||d.id::text,'review:'||d.id::text||':'||d.review_due_at::text
        FROM atlas.documents d JOIN atlas.tenants t ON t.id=d.tenant_id
        JOIN atlas.memberships m ON m.tenant_id=d.tenant_id AND m.user_id=d.owner_user_id AND m.status='active'
        WHERE t.status='active' AND d.lifecycle='active' AND d.review_due_at<=now()
        ON CONFLICT(tenant_id,user_id,dedupe_key) DO NOTHING;
      INSERT INTO atlas.notifications(tenant_id,id,user_id,kind,title,body,link,dedupe_key)
        SELECT d.tenant_id,gen_random_uuid(),d.owner_user_id,'indexing',
          CASE WHEN j.status='completed' THEN 'Document ready' ELSE 'Document needs attention' END,
          CASE WHEN j.status='completed' THEN 'Your document has finished indexing.' ELSE 'Indexing failed. Open the document to inspect and retry.' END,
          '/library/'||d.id::text,'index:'||j.id::text||':'||j.status||':'||j.updated_at::text
        FROM atlas.ingestion_jobs j JOIN atlas.documents d ON d.tenant_id=j.tenant_id AND d.id=j.document_id
        JOIN atlas.tenants t ON t.id=d.tenant_id
        JOIN atlas.memberships m ON m.tenant_id=d.tenant_id AND m.user_id=d.owner_user_id AND m.status='active'
        WHERE t.status='active' AND j.status IN ('completed','dead') AND d.lifecycle='active'
        ON CONFLICT(tenant_id,user_id,dedupe_key) DO NOTHING
    $$;
    REVOKE ALL ON FUNCTION atlas.deliver_maintenance_notifications() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION atlas.deliver_maintenance_notifications() TO atlas_worker;
    """)


def downgrade():
    raise RuntimeError("Restore the verified backup; lifecycle erasure is irreversible")
