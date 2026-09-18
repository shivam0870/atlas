"""Database-enforced principal/resource permissions and private conversation ownership."""

from alembic import op

revision = "008"
down_revision = "007"


def upgrade():
    op.execute("""
    CREATE FUNCTION atlas.context_tenant() RETURNS uuid LANGUAGE sql STABLE AS $$
      SELECT nullif(current_setting('app.tenant_id',true),'')::uuid
    $$;
    CREATE FUNCTION atlas.context_principal() RETURNS uuid LANGUAGE sql STABLE AS $$
      SELECT nullif(current_setting('app.principal_id',true),'')::uuid
    $$;
    CREATE FUNCTION atlas.principal_active() RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      SELECT EXISTS(SELECT 1 FROM atlas.tenants WHERE id=atlas.context_tenant() AND status='active') AND (
        session_user='atlas_worker' OR
        (current_setting('app.principal_kind',true)='user' AND EXISTS(
          SELECT 1 FROM atlas.memberships m JOIN atlas.users u ON u.id=m.user_id
          JOIN atlas.user_sessions s ON s.user_id=u.id
          WHERE m.tenant_id=atlas.context_tenant() AND m.user_id=atlas.context_principal()
            AND m.status='active' AND u.disabled_at IS NULL AND u.email_verified_at IS NOT NULL
            AND s.id=nullif(current_setting('app.session_id',true),'')::uuid
            AND s.revoked_at IS NULL AND s.expires_at>now()
            AND s.last_seen_at>now()-make_interval(secs=>COALESCE(nullif(current_setting('app.session_idle_seconds',true),''),'86400')::double precision)
        )) OR
        (current_setting('app.principal_kind',true)='service' AND EXISTS(
          SELECT 1 FROM atlas.service_accounts s JOIN atlas.api_keys k ON k.tenant_id=s.tenant_id AND k.service_account_id=s.id
          JOIN atlas.tenants t ON t.id=s.tenant_id
          WHERE s.tenant_id=atlas.context_tenant() AND s.id=atlas.context_principal() AND s.active
            AND k.id=nullif(current_setting('app.key_id',true),'')::uuid AND k.revoked_at IS NULL
            AND (k.expires_at IS NULL OR k.expires_at>now()) AND (NOT s.legacy OR t.claimed_at IS NULL)
        ))
      )
    $$;
    CREATE FUNCTION atlas.can_manage() RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      SELECT atlas.principal_active() AND (session_user='atlas_worker' OR
        (current_setting('app.principal_kind',true)='user' AND EXISTS(
          SELECT 1 FROM atlas.memberships WHERE tenant_id=atlas.context_tenant()
          AND user_id=atlas.context_principal() AND status='active' AND role IN ('owner','admin'))))
    $$;
    CREATE FUNCTION atlas.can_evaluate() RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      SELECT atlas.can_manage() OR (atlas.principal_active() AND current_setting('app.principal_kind',true)='user'
        AND EXISTS(SELECT 1 FROM atlas.memberships WHERE tenant_id=atlas.context_tenant()
          AND user_id=atlas.context_principal() AND status='active' AND can_evaluate))
    $$;
    CREATE FUNCTION atlas.principal_can_write() RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      SELECT atlas.principal_active() AND (session_user='atlas_worker' OR
        (current_setting('app.principal_kind',true)='user' AND EXISTS(SELECT 1 FROM atlas.memberships
          WHERE tenant_id=atlas.context_tenant() AND user_id=atlas.context_principal() AND role IN ('owner','admin','editor') AND status='active')) OR
        (current_setting('app.principal_kind',true)='service' AND EXISTS(SELECT 1 FROM atlas.api_keys
          WHERE tenant_id=atlas.context_tenant() AND id=nullif(current_setting('app.key_id',true),'')::uuid AND 'write'=ANY(scopes))))
    $$;
    CREATE FUNCTION atlas.has_grant(space uuid,document uuid,write_access boolean DEFAULT false) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      SELECT EXISTS(SELECT 1 FROM atlas.resource_grants g WHERE g.tenant_id=atlas.context_tenant()
        AND ((space IS NOT NULL AND g.space_id=space) OR (document IS NOT NULL AND g.document_id=document))
        AND (NOT write_access OR g.permission='write') AND (
          (g.subject_type=current_setting('app.principal_kind',true) AND g.subject_id=atlas.context_principal()) OR
          (g.subject_type='team' AND current_setting('app.principal_kind',true)='user' AND EXISTS(
            SELECT 1 FROM atlas.team_members tm WHERE tm.tenant_id=g.tenant_id AND tm.team_id=g.subject_id AND tm.user_id=atlas.context_principal()))))
    $$;
    CREATE FUNCTION atlas.can_space(space uuid,write_access boolean DEFAULT false) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      SELECT EXISTS(SELECT 1 FROM atlas.spaces WHERE tenant_id=atlas.context_tenant() AND id=space)
        AND atlas.principal_active() AND (NOT write_access OR atlas.principal_can_write()) AND (
        atlas.can_manage() OR EXISTS(SELECT 1 FROM atlas.spaces s WHERE s.tenant_id=atlas.context_tenant() AND s.id=space AND (
          (s.visibility='company' AND current_setting('app.principal_kind',true)='user') OR atlas.has_grant(space,NULL,write_access))) OR
        EXISTS(SELECT 1 FROM atlas.service_accounts s JOIN atlas.tenants t ON t.id=s.tenant_id
          WHERE current_setting('app.principal_kind',true)='service' AND s.tenant_id=atlas.context_tenant()
          AND s.id=atlas.context_principal() AND s.legacy AND t.claimed_at IS NULL))
    $$;
    CREATE FUNCTION atlas.can_document_row(space uuid,document uuid,is_restricted boolean,write_access boolean DEFAULT false) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      SELECT atlas.can_space(space,write_access) AND (NOT is_restricted OR atlas.can_manage() OR atlas.has_grant(NULL,document,write_access))
    $$;
    CREATE FUNCTION atlas.can_document(document uuid,write_access boolean DEFAULT false) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      SELECT EXISTS(SELECT 1 FROM atlas.documents d WHERE d.tenant_id=atlas.context_tenant() AND d.id=document
        AND atlas.can_document_row(d.space_id,d.id,d.restricted,write_access))
    $$;
    CREATE FUNCTION atlas.can_entity(entity uuid,write_access boolean DEFAULT false) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      SELECT EXISTS(SELECT 1 FROM atlas.entities e WHERE e.tenant_id=atlas.context_tenant() AND e.id=entity
        AND atlas.can_space(e.space_id,write_access))
    $$;
    CREATE FUNCTION atlas.owns_content(owner_id uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      SELECT atlas.principal_active() AND current_setting('app.principal_kind',true)='user' AND owner_id=atlas.context_principal()
    $$;
    CREATE FUNCTION atlas.document_default_space() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    BEGIN
      IF NEW.space_id IS NULL THEN
        SELECT id INTO NEW.space_id FROM atlas.spaces WHERE tenant_id=NEW.tenant_id AND name='General';
        IF NEW.space_id IS NULL THEN
          INSERT INTO atlas.spaces(tenant_id,id,name) VALUES(NEW.tenant_id,gen_random_uuid(),'General')
            ON CONFLICT(tenant_id,name) DO UPDATE SET name=excluded.name RETURNING id INTO NEW.space_id;
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER document_default_space BEFORE INSERT ON atlas.documents FOR EACH ROW EXECUTE FUNCTION atlas.document_default_space();
    CREATE FUNCTION atlas.immutable_version() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,atlas AS $$
    BEGIN
      IF (to_jsonb(OLD)-'status') IS DISTINCT FROM (to_jsonb(NEW)-'status') THEN
        RAISE EXCEPTION 'Document versions are immutable; create a new version';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER immutable_version BEFORE UPDATE ON atlas.document_versions FOR EACH ROW EXECUTE FUNCTION atlas.immutable_version();
    """)
    signatures = [
        "context_tenant()",
        "context_principal()",
        "principal_active()",
        "can_manage()",
        "can_evaluate()",
        "principal_can_write()",
        "has_grant(uuid,uuid,boolean)",
        "can_space(uuid,boolean)",
        "can_document_row(uuid,uuid,boolean,boolean)",
        "can_document(uuid,boolean)",
        "can_entity(uuid,boolean)",
        "owns_content(uuid)",
    ]
    for signature in signatures:
        op.execute(f"REVOKE ALL ON FUNCTION atlas.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION atlas.{signature} TO atlas_app,atlas_identity")
    op.execute("REVOKE ALL ON FUNCTION atlas.document_default_space() FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION atlas.immutable_version() FROM PUBLIC")
    # Existing tenant policies remain an independent conjunct. Restrictive policies never widen them.
    rules = {
        "documents": "atlas.can_document_row(space_id,id,restricted)",
        "chunks": "atlas.can_document(document_id)",
        "embeddings": "EXISTS(SELECT 1 FROM atlas.chunks c WHERE c.tenant_id=embeddings.tenant_id AND c.id=embeddings.chunk_id)",
        "chunk_terms": "EXISTS(SELECT 1 FROM atlas.chunks c WHERE c.tenant_id=chunk_terms.tenant_id AND c.id=chunk_terms.chunk_id)",
        "entities": "atlas.can_space(space_id)",
        "queries": "atlas.owns_content(user_id) OR (user_id IS NULL AND atlas.can_manage()) OR (principal_kind='service' AND principal_id=atlas.context_principal() AND atlas.principal_active())",
        "eval_labels": "atlas.can_evaluate() AND atlas.can_document(document_id)",
        "eval_runs": "atlas.can_evaluate()",
        "api_keys": "atlas.can_manage()",  # pragma: allowlist secret -- policy expression
        "embedding_cache": "session_user='atlas_worker'",
    }
    for table, rule in rules.items():
        op.execute(
            f"CREATE POLICY resource_scope ON atlas.{table} AS RESTRICTIVE TO atlas_app USING({rule}) WITH CHECK({rule})"
        )
    new_rules = {
        "spaces": "atlas.can_space(id)",
        "resource_grants": "atlas.can_manage() OR (space_id IS NOT NULL AND atlas.can_space(space_id)) OR (document_id IS NOT NULL AND atlas.can_document(document_id))",
        "document_versions": "atlas.can_document(document_id)",
        "conversations": "atlas.owns_content(user_id)",
        "messages": "atlas.owns_content(user_id) AND EXISTS(SELECT 1 FROM atlas.conversations c WHERE c.tenant_id=messages.tenant_id AND c.id=messages.conversation_id AND c.user_id=messages.user_id)",
        "bookmarks": "atlas.owns_content(user_id)",
        "feedback": "atlas.owns_content(user_id) OR atlas.can_evaluate()",
        "notifications": "atlas.owns_content(user_id)",
        "access_requests": "atlas.owns_content(user_id) OR atlas.can_manage()",
        "audit_events": "atlas.can_manage()",
        "evaluation_jobs": "atlas.can_evaluate()",
        "memberships": "atlas.principal_active()",
        "teams": "atlas.principal_active()",
        "team_members": "atlas.principal_active()",
        "service_accounts": "atlas.can_manage()",
    }
    tenant = "tenant_id=atlas.context_tenant()"
    for table, rule in new_rules.items():
        op.execute(f"ALTER TABLE atlas.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE atlas.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON atlas.{table} TO atlas_app")
        op.execute(
            f"CREATE POLICY tenant_scope ON atlas.{table} TO atlas_app USING({tenant}) WITH CHECK({tenant})"
        )
        op.execute(
            f"CREATE POLICY resource_scope ON atlas.{table} AS RESTRICTIVE FOR SELECT TO atlas_app USING({rule})"
        )
        write_rule = rule
        if table in {
            "spaces",
            "resource_grants",
            "memberships",
            "teams",
            "team_members",
            "service_accounts",
            "audit_events",
        }:
            write_rule = "atlas.can_manage()"
        elif table == "document_versions":
            write_rule = "atlas.can_document(document_id,true)"
        elif table == "notifications":
            write_rule = "atlas.principal_active() AND EXISTS(SELECT 1 FROM atlas.memberships m WHERE m.tenant_id=notifications.tenant_id AND m.user_id=notifications.user_id AND m.status='active')"
        op.execute(
            f"CREATE POLICY resource_write ON atlas.{table} AS RESTRICTIVE FOR INSERT TO atlas_app WITH CHECK({write_rule})"
        )
        update_rule = "atlas.owns_content(user_id)" if table == "notifications" else write_rule
        op.execute(
            f"CREATE POLICY resource_update ON atlas.{table} AS RESTRICTIVE FOR UPDATE TO atlas_app USING({update_rule}) WITH CHECK({update_rule})"
        )
        op.execute(
            f"CREATE POLICY resource_delete ON atlas.{table} AS RESTRICTIVE FOR DELETE TO atlas_app USING({update_rule})"
        )
        if table in {
            "spaces",
            "notifications",
            "audit_events",
            "memberships",
            "teams",
            "team_members",
            "service_accounts",
        }:
            op.execute(
                f"CREATE POLICY identity_management ON atlas.{table} TO atlas_identity USING(true) WITH CHECK(true)"
            )
    # Read-only scopes never authorize writes simply because a row is visible.
    for table, rule in {
        "documents": "atlas.can_document_row(space_id,id,restricted,true)",
        "entities": "atlas.can_space(space_id,true)",
    }.items():
        op.execute(
            f"CREATE POLICY write_scope ON atlas.{table} AS RESTRICTIVE FOR INSERT TO atlas_app WITH CHECK({rule})"
        )
        op.execute(
            f"CREATE POLICY update_scope ON atlas.{table} AS RESTRICTIVE FOR UPDATE TO atlas_app USING({rule}) WITH CHECK({rule})"
        )
        op.execute(
            f"CREATE POLICY delete_scope ON atlas.{table} AS RESTRICTIVE FOR DELETE TO atlas_app USING({rule})"
        )
    op.execute("""
    GRANT SELECT,DELETE ON atlas.resource_grants TO atlas_identity;
    CREATE POLICY identity_management ON atlas.resource_grants TO atlas_identity USING(true) WITH CHECK(true);
    ALTER TABLE atlas.users ENABLE ROW LEVEL SECURITY;
    ALTER TABLE atlas.users FORCE ROW LEVEL SECURITY;
    CREATE POLICY identity_management ON atlas.users TO atlas_identity USING(true) WITH CHECK(true);
    GRANT SELECT(id,name,email) ON atlas.users TO atlas_app;
    CREATE POLICY user_directory ON atlas.users FOR SELECT TO atlas_app USING(
      atlas.principal_active() AND (id=atlas.context_principal() OR (atlas.can_manage() AND EXISTS(
        SELECT 1 FROM atlas.memberships m WHERE m.tenant_id=atlas.context_tenant() AND m.user_id=users.id))));
    CREATE FUNCTION atlas.audit_change() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    DECLARE rowdata jsonb; tenant uuid; rid uuid;
    BEGIN
      rowdata=COALESCE(to_jsonb(NEW),to_jsonb(OLD));
      tenant=(rowdata->>'tenant_id')::uuid;
      rid=COALESCE(rowdata->>'id',rowdata->>'user_id')::uuid;
      IF EXISTS(SELECT 1 FROM atlas.tenants WHERE id=tenant) THEN
        INSERT INTO atlas.audit_events(tenant_id,actor_id,actor_kind,action,resource_type,resource_id)
          VALUES(tenant,atlas.context_principal(),COALESCE(nullif(current_setting('app.principal_kind',true),''),'system'),lower(TG_OP),TG_TABLE_NAME,rid);
      END IF;
      RETURN COALESCE(NEW,OLD);
    END $$;
    REVOKE ALL ON FUNCTION atlas.audit_change() FROM PUBLIC;
    CREATE FUNCTION atlas.pending_evaluation_jobs() RETURNS TABLE(tenant_id uuid,id uuid)
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      SELECT j.tenant_id,j.id FROM atlas.evaluation_jobs j
      WHERE j.status='queued' OR (j.status='running' AND j.lease_until<now())
      ORDER BY j.created_at LIMIT 1
    $$;
    REVOKE ALL ON FUNCTION atlas.pending_evaluation_jobs() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION atlas.pending_evaluation_jobs() TO atlas_worker;
    """)
    for table in [
        "memberships",
        "teams",
        "team_members",
        "api_keys",
        "resource_grants",
        "documents",
        "service_accounts",
        "entities",
        "eval_labels",
        "feedback",
    ]:
        op.execute(
            f"CREATE TRIGGER audit_change AFTER INSERT OR UPDATE OR DELETE ON atlas.{table} FOR EACH ROW EXECUTE FUNCTION atlas.audit_change()"
        )
    for table in ["chunks", "embeddings", "chunk_terms"]:
        for command in ["INSERT", "UPDATE", "DELETE"]:
            clause = (
                "WITH CHECK(session_user='atlas_worker')"
                if command == "INSERT"
                else "USING(session_user='atlas_worker')"
            )
            op.execute(
                f"CREATE POLICY derived_{command.lower()} ON atlas.{table} AS RESTRICTIVE FOR {command} TO atlas_app {clause}"
            )


def downgrade():
    raise RuntimeError("Restore the verified backup instead of dropping access controls")
