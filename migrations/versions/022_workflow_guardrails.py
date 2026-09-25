"""Erase historical evidence and bound/fairly schedule background workflows."""

from alembic import op

revision = "022"
down_revision = "021"


def upgrade():
    op.execute("""
    CREATE OR REPLACE FUNCTION atlas.erase_workflow_document() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    BEGIN
      DELETE FROM atlas.knowledge_relationships WHERE tenant_id=OLD.tenant_id AND
        ((source_kind='document' AND source_id=OLD.id) OR (target_kind='document' AND target_id=OLD.id));
      -- Runs are immutable snapshots of older definitions. A current definition
      -- can no longer mention a source while a historical run still contains it.
      DELETE FROM atlas.playbook_runs WHERE tenant_id=OLD.tenant_id AND EXISTS(
        SELECT 1 FROM jsonb_array_elements(steps) step WHERE step->>'document_id'=OLD.id::text);
      DELETE FROM atlas.playbooks WHERE tenant_id=OLD.tenant_id AND EXISTS(
        SELECT 1 FROM jsonb_array_elements(steps) step WHERE step->>'document_id'=OLD.id::text);
      UPDATE atlas.briefing_runs SET items='[]',summary='',status='unavailable'
        WHERE tenant_id=OLD.tenant_id AND EXISTS(
          SELECT 1 FROM jsonb_array_elements(items) item WHERE item->>'document_id'=OLD.id::text);
      UPDATE atlas.briefing_subscriptions SET document_ids=array_remove(document_ids,OLD.id)
        WHERE tenant_id=OLD.tenant_id AND OLD.id=ANY(document_ids);
      RETURN OLD;
    END $$;

    CREATE OR REPLACE FUNCTION atlas.due_workflows()
      RETURNS TABLE(kind text,tenant_id uuid,id uuid,user_id uuid,session_id uuid)
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      WITH due AS (
        SELECT 'source'::text kind,s.tenant_id,s.id,s.user_id,s.session_id,s.next_run_at
          FROM atlas.source_connections s JOIN atlas.tenants t ON t.id=s.tenant_id
          WHERE t.status='active' AND s.enabled AND s.next_run_at<=now()
        UNION ALL
        SELECT 'briefing',s.tenant_id,s.id,s.user_id,s.session_id,s.next_run_at
          FROM atlas.briefing_subscriptions s JOIN atlas.tenants t ON t.id=s.tenant_id
          WHERE t.status='active' AND s.enabled AND s.next_run_at<=now()
      ), ranked AS (
        SELECT *,row_number() OVER(PARTITION BY due.tenant_id ORDER BY next_run_at,id) turn
          FROM due
      )
      SELECT kind,tenant_id,id,user_id,session_id FROM ranked
        ORDER BY turn,next_run_at,tenant_id,id LIMIT 10
    $$;

    CREATE FUNCTION atlas.defer_workflow(workflow_kind text,tenant uuid,workflow uuid,code text)
      RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    BEGIN
      IF code NOT IN ('quota_exceeded','workflow_failed','already_running') THEN
        RAISE EXCEPTION 'Invalid workflow error code';
      END IF;
      IF workflow_kind='source' THEN
        UPDATE atlas.source_connections SET last_error=code,next_run_at=now()+interval '5 minutes'
          WHERE tenant_id=tenant AND id=workflow;
        IF code<>'already_running' THEN
          UPDATE atlas.source_runs SET status='failed',error_code=code,completed_at=now()
            WHERE tenant_id=tenant AND source_id=workflow AND status='running';
        END IF;
      ELSIF workflow_kind='briefing' THEN
        UPDATE atlas.briefing_subscriptions SET last_error=code,next_run_at=now()+interval '5 minutes'
          WHERE tenant_id=tenant AND id=workflow;
        IF code<>'already_running' THEN
          UPDATE atlas.briefing_runs SET status='failed',summary='',items='[]',error_code=code,completed_at=now()
            WHERE tenant_id=tenant AND briefing_id=workflow AND status='running';
        END IF;
      END IF;
    END $$;
    REVOKE ALL ON FUNCTION atlas.defer_workflow(text,uuid,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION atlas.defer_workflow(text,uuid,uuid,text) TO atlas_worker;

    CREATE FUNCTION atlas.bound_source_sync() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    BEGIN
      INSERT INTO atlas.tenant_limits(tenant_id) VALUES(NEW.tenant_id) ON CONFLICT DO NOTHING;
      PERFORM 1 FROM atlas.tenant_limits WHERE tenant_id=NEW.tenant_id FOR UPDATE;
      IF (SELECT count(*) FROM atlas.source_runs WHERE tenant_id=NEW.tenant_id
          AND status='running' AND created_at>now()-interval '10 minutes')>=2 THEN
        RAISE EXCEPTION 'Workspace concurrent source sync limit reached' USING ERRCODE='53000';
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION atlas.bound_source_sync() FROM PUBLIC;
    CREATE TRIGGER source_sync_limit BEFORE INSERT ON atlas.source_runs
      FOR EACH ROW EXECUTE FUNCTION atlas.bound_source_sync();
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup; evidence erasure must remain enforced")
