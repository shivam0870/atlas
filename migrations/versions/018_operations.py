"""Tenant-scoped operational alerts and operator recovery/release evidence."""

from alembic import op

revision = "018"
down_revision = "017"


def upgrade():
    op.execute("""
    CREATE TABLE atlas.operational_alerts (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id) ON DELETE CASCADE,
      id uuid NOT NULL DEFAULT gen_random_uuid(),kind text NOT NULL,
      severity text NOT NULL CHECK(severity IN ('warning','critical')),
      message text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      last_seen_at timestamptz NOT NULL DEFAULT now(),resolved_at timestamptz,
      acknowledged_at timestamptz,acknowledged_by uuid REFERENCES atlas.users(id) ON DELETE SET NULL,
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,kind)
    );
    CREATE TABLE atlas.recovery_runs (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id) ON DELETE CASCADE,
      id uuid NOT NULL,kind text NOT NULL CHECK(kind IN ('backup','restore')),
      status text NOT NULL CHECK(status IN ('running','completed','failed')),
      created_at timestamptz NOT NULL DEFAULT now(),completed_at timestamptz,verified_at timestamptz,
      error_code text,PRIMARY KEY(tenant_id,id)
    );
    CREATE TABLE atlas.release_records (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id) ON DELETE CASCADE,
      id uuid NOT NULL,name text NOT NULL,status text NOT NULL
        CHECK(status IN ('candidate','accepted','active','rolled_back','blocked')),
      manifest jsonb NOT NULL DEFAULT '{}',created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id)
    );
    """)
    for table in ("operational_alerts", "recovery_runs", "release_records"):
        op.execute(f"ALTER TABLE atlas.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE atlas.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"GRANT SELECT ON atlas.{table} TO atlas_app")
        op.execute(
            f"CREATE POLICY tenant_scope ON atlas.{table} TO atlas_app "
            "USING(tenant_id=atlas.context_tenant())"
        )
        op.execute(
            f"CREATE POLICY manager_scope ON atlas.{table} AS RESTRICTIVE TO atlas_app "
            "USING(atlas.can_manage())"
        )
    op.execute("""
    GRANT UPDATE(acknowledged_at,acknowledged_by) ON atlas.operational_alerts TO atlas_app;
    CREATE FUNCTION atlas.refresh_operational_alerts(queue_ready boolean,generation_ready boolean)
    RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    DECLARE t record; issue record;
    BEGIN
      FOR t IN SELECT id FROM atlas.tenants WHERE status='active' LOOP
        FOR issue IN
          SELECT 'queue_unavailable' kind,'critical' severity,'Queue service is unavailable.' message,NOT queue_ready failing
          UNION ALL SELECT 'generation_unavailable','warning','Generation service is unavailable; authorized search remains available.',NOT generation_ready
          UNION ALL SELECT 'ingestion_failed','warning','One or more ingestion jobs need attention.',
            EXISTS(SELECT 1 FROM atlas.ingestion_jobs WHERE tenant_id=t.id AND status='dead')
          UNION ALL SELECT 'ingestion_stalled','warning','An ingestion worker lease expired; recovery is pending.',
            EXISTS(SELECT 1 FROM atlas.ingestion_jobs WHERE tenant_id=t.id AND status='running' AND lease_until<now()-interval '1 minute')
          UNION ALL SELECT 'backup_overdue','warning','No successful backup has been recorded in the last 48 hours.',
            NOT EXISTS(SELECT 1 FROM atlas.recovery_runs WHERE tenant_id=t.id AND kind='backup' AND status='completed' AND completed_at>now()-interval '48 hours')
        LOOP
          IF issue.failing THEN
            INSERT INTO atlas.operational_alerts(tenant_id,kind,severity,message)
              VALUES(t.id,issue.kind,issue.severity,issue.message)
              ON CONFLICT(tenant_id,kind) DO UPDATE SET last_seen_at=now(),resolved_at=NULL,
                acknowledged_at=CASE WHEN operational_alerts.resolved_at IS NOT NULL THEN NULL ELSE operational_alerts.acknowledged_at END,
                acknowledged_by=CASE WHEN operational_alerts.resolved_at IS NOT NULL THEN NULL ELSE operational_alerts.acknowledged_by END,
                created_at=CASE WHEN operational_alerts.resolved_at IS NOT NULL THEN now() ELSE operational_alerts.created_at END;
          ELSE
            UPDATE atlas.operational_alerts SET resolved_at=coalesce(resolved_at,now())
              WHERE tenant_id=t.id AND kind=issue.kind AND resolved_at IS NULL;
          END IF;
        END LOOP;
      END LOOP;
    END $$;
    REVOKE ALL ON FUNCTION atlas.refresh_operational_alerts(boolean,boolean) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION atlas.refresh_operational_alerts(boolean,boolean) TO atlas_worker;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup; operational evidence must be retained")
