"""Publication metadata and permission-bound knowledge workflows."""

from alembic import op

revision = "016"
down_revision = "015"


def upgrade():
    op.execute("""
    ALTER TABLE atlas.document_versions
      ADD COLUMN publication_status text NOT NULL DEFAULT 'published' CHECK(publication_status IN ('draft','published','superseded','archived')),
      ADD COLUMN effective_at timestamptz NOT NULL DEFAULT now(),
      ADD COLUMN published_at timestamptz,
      ADD COLUMN approved_by uuid REFERENCES atlas.users(id) ON DELETE SET NULL;
    CREATE OR REPLACE FUNCTION atlas.immutable_version() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,atlas AS $$
    BEGIN
      IF (to_jsonb(OLD)-ARRAY['status','publication_status','effective_at','published_at','approved_by'])
        IS DISTINCT FROM (to_jsonb(NEW)-ARRAY['status','publication_status','effective_at','published_at','approved_by']) THEN
        RAISE EXCEPTION 'Document versions are immutable; create a new version';
      END IF;
      RETURN NEW;
    END $$;
    UPDATE atlas.document_versions v SET publication_status=CASE
      WHEN d.lifecycle<>'active' THEN 'archived'
      WHEN d.current_version_id=v.id OR d.pending_version_id=v.id THEN 'published'
      ELSE 'superseded' END,
      effective_at=v.created_at,published_at=CASE WHEN v.status='ready' THEN v.created_at END
      FROM atlas.documents d WHERE d.tenant_id=v.tenant_id AND d.id=v.document_id;
    CREATE TABLE atlas.source_connections (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id) ON DELETE CASCADE,id uuid NOT NULL,
      name text NOT NULL,kind text NOT NULL CHECK(kind IN ('github','folder')),location text NOT NULL,
      space_id uuid NOT NULL,include_patterns text[] NOT NULL DEFAULT '{**/*.md,**/*.txt}',
      interval_hours integer NOT NULL DEFAULT 24 CHECK(interval_hours BETWEEN 1 AND 168),enabled boolean NOT NULL DEFAULT true,
      user_id uuid NOT NULL REFERENCES atlas.users(id) ON DELETE CASCADE,session_id uuid REFERENCES atlas.user_sessions(id) ON DELETE SET NULL,
      next_run_at timestamptz NOT NULL DEFAULT now(),last_run_at timestamptz,last_error text,
      created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id),FOREIGN KEY(tenant_id,space_id) REFERENCES atlas.spaces(tenant_id,id) ON DELETE CASCADE
    );
    CREATE TABLE atlas.source_runs (
      tenant_id uuid NOT NULL,id uuid NOT NULL,source_id uuid NOT NULL,status text NOT NULL DEFAULT 'running',
      imported integer NOT NULL DEFAULT 0,unchanged integer NOT NULL DEFAULT 0,archived integer NOT NULL DEFAULT 0,
      error_code text,created_at timestamptz NOT NULL DEFAULT now(),completed_at timestamptz,
      PRIMARY KEY(tenant_id,id),FOREIGN KEY(tenant_id,source_id) REFERENCES atlas.source_connections(tenant_id,id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX one_active_source_sync ON atlas.source_runs(tenant_id,source_id) WHERE status='running';
    CREATE TABLE atlas.source_items (
      tenant_id uuid NOT NULL,source_id uuid NOT NULL,path text NOT NULL,content_hash text NOT NULL,document_id uuid NOT NULL,
      last_seen_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,source_id,path),
      FOREIGN KEY(tenant_id,source_id) REFERENCES atlas.source_connections(tenant_id,id) ON DELETE CASCADE,
      FOREIGN KEY(tenant_id,document_id) REFERENCES atlas.documents(tenant_id,id) ON DELETE CASCADE
    );
    CREATE TABLE atlas.knowledge_relationships (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id) ON DELETE CASCADE,id uuid NOT NULL,
      source_kind text NOT NULL CHECK(source_kind IN ('document','entity')),source_id uuid NOT NULL,
      target_kind text NOT NULL CHECK(target_kind IN ('document','entity')),target_id uuid NOT NULL,
      label text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,id),
      UNIQUE(tenant_id,source_kind,source_id,target_kind,target_id,label),CHECK(source_id<>target_id)
    );
    CREATE TABLE atlas.playbooks (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id) ON DELETE CASCADE,id uuid NOT NULL,
      space_id uuid NOT NULL,name text NOT NULL,description text NOT NULL DEFAULT '',
      status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published')),revision integer NOT NULL DEFAULT 1,
      user_id uuid NOT NULL REFERENCES atlas.users(id) ON DELETE CASCADE,steps jsonb NOT NULL DEFAULT '[]',
      created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id),FOREIGN KEY(tenant_id,space_id) REFERENCES atlas.spaces(tenant_id,id) ON DELETE CASCADE
    );
    CREATE TABLE atlas.playbook_runs (
      tenant_id uuid NOT NULL,id uuid NOT NULL,playbook_id uuid NOT NULL,user_id uuid NOT NULL REFERENCES atlas.users(id) ON DELETE CASCADE,
      name text NOT NULL,revision integer NOT NULL,steps jsonb NOT NULL,status text NOT NULL DEFAULT 'active',
      created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id),FOREIGN KEY(tenant_id,playbook_id) REFERENCES atlas.playbooks(tenant_id,id) ON DELETE CASCADE
    );
    CREATE TABLE atlas.briefing_subscriptions (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id) ON DELETE CASCADE,id uuid NOT NULL,
      user_id uuid NOT NULL REFERENCES atlas.users(id) ON DELETE CASCADE,session_id uuid REFERENCES atlas.user_sessions(id) ON DELETE SET NULL,
      name text NOT NULL,space_ids uuid[] NOT NULL DEFAULT '{}',document_ids uuid[] NOT NULL DEFAULT '{}',question text NOT NULL DEFAULT '',
      cadence text NOT NULL DEFAULT 'weekly' CHECK(cadence IN ('daily','weekly')),enabled boolean NOT NULL DEFAULT true,
      next_run_at timestamptz NOT NULL DEFAULT now(),last_run_at timestamptz,last_error text,
      created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,id)
    );
    CREATE TABLE atlas.briefing_runs (
      tenant_id uuid NOT NULL,id uuid NOT NULL,briefing_id uuid NOT NULL,user_id uuid NOT NULL REFERENCES atlas.users(id) ON DELETE CASCADE,
      status text NOT NULL DEFAULT 'running',summary text NOT NULL DEFAULT '',items jsonb NOT NULL DEFAULT '[]',error_code text,
      created_at timestamptz NOT NULL DEFAULT now(),completed_at timestamptz,
      PRIMARY KEY(tenant_id,id),FOREIGN KEY(tenant_id,briefing_id) REFERENCES atlas.briefing_subscriptions(tenant_id,id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX one_active_briefing_run ON atlas.briefing_runs(tenant_id,briefing_id) WHERE status='running';
    CREATE FUNCTION atlas.can_workflow_node(kind text,node uuid,write_access boolean DEFAULT false) RETURNS boolean
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
        SELECT CASE kind WHEN 'document' THEN atlas.can_document(node,write_access) WHEN 'entity' THEN atlas.can_entity(node,write_access) ELSE false END
    $$;
    CREATE FUNCTION atlas.can_workflow_evidence(evidence jsonb) RETURNS boolean
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
        SELECT atlas.principal_active() AND NOT EXISTS(
          SELECT 1 FROM jsonb_array_elements(evidence) item
          WHERE item->>'document_id' IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM atlas.documents d WHERE d.tenant_id=atlas.context_tenant()
              AND d.id=(item->>'document_id')::uuid AND d.lifecycle<>'trashed'
              AND atlas.can_document(d.id)))
    $$;
    REVOKE ALL ON FUNCTION atlas.can_workflow_evidence(jsonb) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION atlas.can_workflow_evidence(jsonb) TO atlas_app;
    REVOKE ALL ON FUNCTION atlas.can_workflow_node(text,uuid,boolean) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION atlas.can_workflow_node(text,uuid,boolean) TO atlas_app;
    """)
    rules = {
        "source_connections": (
            "atlas.can_manage() AND atlas.can_space(space_id)",
            "atlas.can_manage() AND atlas.can_space(space_id,true)",
        ),
        "source_runs": (
            "EXISTS(SELECT 1 FROM atlas.source_connections s WHERE s.tenant_id=source_runs.tenant_id AND s.id=source_runs.source_id)",
            None,
        ),
        "source_items": (
            "atlas.can_document(document_id) AND EXISTS(SELECT 1 FROM atlas.source_connections s WHERE s.tenant_id=source_items.tenant_id AND s.id=source_items.source_id)",
            None,
        ),
        "knowledge_relationships": (
            "atlas.can_workflow_node(source_kind,source_id) AND atlas.can_workflow_node(target_kind,target_id)",
            "atlas.can_workflow_node(source_kind,source_id,true) AND atlas.can_workflow_node(target_kind,target_id)",
        ),
        "playbooks": (
            "atlas.can_space(space_id) AND atlas.can_workflow_evidence(steps)",
            "atlas.can_space(space_id,true) AND atlas.can_workflow_evidence(steps)",
        ),
        "playbook_runs": (
            "(atlas.owns_content(user_id) OR atlas.can_manage() OR (atlas.principal_active() AND steps @> jsonb_build_array(jsonb_build_object('assignee_user_id',atlas.context_principal()::text)))) AND atlas.can_workflow_evidence(steps) AND EXISTS(SELECT 1 FROM atlas.playbooks p WHERE p.tenant_id=playbook_runs.tenant_id AND p.id=playbook_runs.playbook_id)",
            None,
        ),
        "briefing_subscriptions": ("atlas.owns_content(user_id)", None),
        "briefing_runs": (
            "atlas.owns_content(user_id) AND atlas.can_workflow_evidence(items)",
            None,
        ),
    }
    for table, (read, write) in rules.items():
        write = write or read
        op.execute(f"""
          ALTER TABLE atlas.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE atlas.{table} FORCE ROW LEVEL SECURITY;
          GRANT SELECT,INSERT,UPDATE,DELETE ON atlas.{table} TO atlas_app;
          CREATE POLICY tenant_scope ON atlas.{table} TO atlas_app
            USING(tenant_id=atlas.context_tenant()) WITH CHECK(tenant_id=atlas.context_tenant());
          CREATE POLICY workflow_read ON atlas.{table} AS RESTRICTIVE FOR SELECT TO atlas_app USING({read});
          CREATE POLICY workflow_insert ON atlas.{table} AS RESTRICTIVE FOR INSERT TO atlas_app WITH CHECK({write});
          CREATE POLICY workflow_update ON atlas.{table} AS RESTRICTIVE FOR UPDATE TO atlas_app USING({write}) WITH CHECK({write});
          CREATE POLICY workflow_delete ON atlas.{table} AS RESTRICTIVE FOR DELETE TO atlas_app USING({write});
          CREATE TRIGGER audit_change AFTER INSERT OR UPDATE OR DELETE ON atlas.{table} FOR EACH ROW EXECUTE FUNCTION atlas.audit_change();
        """)
    op.execute("""
    CREATE FUNCTION atlas.erase_workflow_document() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    BEGIN
      DELETE FROM atlas.knowledge_relationships WHERE tenant_id=OLD.tenant_id AND
        ((source_kind='document' AND source_id=OLD.id) OR (target_kind='document' AND target_id=OLD.id));
      DELETE FROM atlas.playbooks WHERE tenant_id=OLD.tenant_id AND steps::text LIKE '%'||OLD.id::text||'%';
      UPDATE atlas.briefing_runs SET items='[]',summary='',status='unavailable'
        WHERE tenant_id=OLD.tenant_id AND items::text LIKE '%'||OLD.id::text||'%';
      UPDATE atlas.briefing_subscriptions SET document_ids=array_remove(document_ids,OLD.id)
        WHERE tenant_id=OLD.tenant_id AND OLD.id=ANY(document_ids);
      RETURN OLD;
    END $$;
    REVOKE ALL ON FUNCTION atlas.erase_workflow_document() FROM PUBLIC;
    CREATE TRIGGER erase_workflow_document BEFORE DELETE ON atlas.documents FOR EACH ROW EXECUTE FUNCTION atlas.erase_workflow_document();
    CREATE FUNCTION atlas.due_workflows() RETURNS TABLE(kind text,tenant_id uuid,id uuid,user_id uuid,session_id uuid)
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      SELECT 'source',s.tenant_id,s.id,s.user_id,s.session_id FROM atlas.source_connections s
        JOIN atlas.tenants t ON t.id=s.tenant_id WHERE t.status='active' AND s.enabled AND s.next_run_at<=now()
      UNION ALL
      SELECT 'briefing',s.tenant_id,s.id,s.user_id,s.session_id FROM atlas.briefing_subscriptions s
        JOIN atlas.tenants t ON t.id=s.tenant_id WHERE t.status='active' AND s.enabled AND s.next_run_at<=now()
      ORDER BY tenant_id,id LIMIT 10
    $$;
    CREATE FUNCTION atlas.pause_workflow(workflow_kind text,tenant uuid,workflow uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    BEGIN
      IF workflow_kind='source' THEN
        UPDATE atlas.source_connections SET enabled=false,last_error='authorization_required' WHERE tenant_id=tenant AND id=workflow;
        UPDATE atlas.source_runs SET status='unavailable',error_code='authorization_required',completed_at=now() WHERE tenant_id=tenant AND source_id=workflow AND status='running';
      ELSIF workflow_kind='briefing' THEN
        UPDATE atlas.briefing_subscriptions SET enabled=false,last_error='authorization_required' WHERE tenant_id=tenant AND id=workflow;
        UPDATE atlas.briefing_runs SET status='unavailable',summary='',items='[]',error_code='authorization_required',completed_at=now() WHERE tenant_id=tenant AND briefing_id=workflow AND status='running';
      END IF;
    END $$;
    REVOKE ALL ON FUNCTION atlas.due_workflows(),atlas.pause_workflow(text,uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION atlas.due_workflows(),atlas.pause_workflow(text,uuid,uuid) TO atlas_worker;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup before removing publication and workflow history")
