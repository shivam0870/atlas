"""Principal accounting and bounded cleanup after an in-flight access revocation."""

from alembic import op

revision = "009"
down_revision = "008"


def upgrade():
    op.execute("""
    ALTER TABLE atlas.reservations ADD COLUMN used_tokens bigint CHECK(used_tokens>=0);
    ALTER TABLE atlas.usage_ledger ADD COLUMN cached boolean NOT NULL DEFAULT false;
    ALTER TABLE atlas.usage_ledger ADD COLUMN principal_kind text;
    UPDATE atlas.usage_ledger l SET cached=q.cached FROM atlas.queries q
      WHERE q.tenant_id=l.tenant_id AND q.id=l.request_id;
    CREATE FUNCTION atlas.ledger_cache_flag() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    BEGIN
      NEW.cached=COALESCE((SELECT q.cached FROM atlas.queries q WHERE q.tenant_id=NEW.tenant_id AND q.id=NEW.request_id),false);
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION atlas.ledger_cache_flag() FROM PUBLIC;
    CREATE TRIGGER cache_flag BEFORE INSERT ON atlas.usage_ledger
      FOR EACH ROW EXECUTE FUNCTION atlas.ledger_cache_flag();
    CREATE FUNCTION atlas.finalize_revoked_attempt(query_id uuid, observed_tokens bigint,
      input_tokens bigint, output_tokens bigint, duration_ms double precision, model text DEFAULT 'local')
    RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    DECLARE q atlas.queries; r atlas.reservations;
    BEGIN
      IF observed_tokens<0 OR observed_tokens>100000000 OR input_tokens<0 OR input_tokens>100000000
        OR output_tokens<0 OR output_tokens>100000000 OR duration_ms<0 OR duration_ms>'1e12'::float8 THEN
        RAISE EXCEPTION 'Invalid accounting values';
      END IF;
      SELECT * INTO q FROM atlas.queries WHERE tenant_id=atlas.context_tenant() AND id=query_id
        AND principal_id=atlas.context_principal()
        AND principal_kind=current_setting('app.principal_kind',true) FOR UPDATE;
      IF NOT FOUND THEN RETURN false; END IF;
      IF q.status<>'running' AND NOT(q.status='cancelled' AND q.error_code='access_revoked') THEN RETURN false; END IF;
      UPDATE atlas.queries SET answer='',sources='[]',status='cancelled',error_code='access_revoked',
        duration_ms=greatest(0,finalize_revoked_attempt.duration_ms)
        WHERE tenant_id=q.tenant_id AND id=q.id;
      UPDATE atlas.messages SET content='',sources='[]',status='unavailable',metadata=jsonb_build_object('access_revoked',true)
        WHERE tenant_id=q.tenant_id AND request_id=q.id AND user_id=q.principal_id;
      IF NOT EXISTS(SELECT 1 FROM atlas.usage_ledger l WHERE l.tenant_id=q.tenant_id AND l.request_id=q.id) THEN
        INSERT INTO atlas.usage_ledger(tenant_id,id,request_id,operation,backend,model,input_tokens,output_tokens,
          duration_ms,status,user_id,principal_id,principal_kind)
          VALUES(q.tenant_id,gen_random_uuid(),q.id,'query','local',finalize_revoked_attempt.model,
          finalize_revoked_attempt.input_tokens,finalize_revoked_attempt.output_tokens,
          greatest(0,finalize_revoked_attempt.duration_ms),'cancelled',q.user_id,q.principal_id,q.principal_kind);
      END IF;
      SELECT * INTO r FROM atlas.reservations WHERE tenant_id=q.tenant_id AND id=q.id AND status='reserved' FOR UPDATE;
      IF FOUND THEN
        IF observed_tokens IS NULL THEN
          UPDATE atlas.reservations SET status='uncertain' WHERE tenant_id=r.tenant_id AND id=r.id;
        ELSE
          UPDATE atlas.budget_periods SET reserved_tokens=reserved_tokens-r.tokens,
            used_tokens=used_tokens+greatest(0,observed_tokens) WHERE tenant_id=r.tenant_id AND period=r.period;
          UPDATE atlas.reservations SET status='settled',used_tokens=greatest(0,observed_tokens)
            WHERE tenant_id=r.tenant_id AND id=r.id;
        END IF;
      END IF;
      RETURN true;
    END $$;
    REVOKE ALL ON FUNCTION atlas.finalize_revoked_attempt(uuid,bigint,bigint,bigint,double precision,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION atlas.finalize_revoked_attempt(uuid,bigint,bigint,bigint,double precision,text) TO atlas_app;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup; quota accounting must not be discarded")
