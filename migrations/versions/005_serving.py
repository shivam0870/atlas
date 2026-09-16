"""Durable ingestion jobs, explicit quota reservations, and safe dispatch privileges."""

from alembic import op
from psycopg import sql

from atlas.config import settings

revision = "005"
down_revision = "004"


def upgrade():
    op.execute("""
    CREATE TABLE atlas.tenant_limits (
      tenant_id uuid PRIMARY KEY REFERENCES atlas.tenants(id),
      requests_per_minute integer NOT NULL DEFAULT 60 CHECK(requests_per_minute>0),
      monthly_tokens bigint NOT NULL DEFAULT 1000000 CHECK(monthly_tokens>=0),
      monthly_usd numeric(20,10) NOT NULL DEFAULT 0 CHECK(monthly_usd>=0)
    );
    CREATE TABLE atlas.budget_periods (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id), period date NOT NULL,
      reserved_tokens bigint NOT NULL DEFAULT 0 CHECK(reserved_tokens>=0),
      used_tokens bigint NOT NULL DEFAULT 0 CHECK(used_tokens>=0),
      spent_usd numeric(20,10) NOT NULL DEFAULT 0, PRIMARY KEY(tenant_id,period)
    );
    CREATE TABLE atlas.reservations (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id), id uuid NOT NULL,
      period date NOT NULL, tokens bigint NOT NULL CHECK(tokens>=0),
      status text NOT NULL DEFAULT 'reserved' CHECK(status IN ('reserved','settled','uncertain')),
      created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,id),
      FOREIGN KEY(tenant_id,period) REFERENCES atlas.budget_periods(tenant_id,period)
    );
    CREATE TABLE atlas.ingestion_jobs (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id),id uuid NOT NULL, document_id uuid NOT NULL,
      idempotency_key text NOT NULL,status text NOT NULL DEFAULT 'queued'
      CHECK(status IN ('queued','running','retry','completed','dead')),
      attempts integer NOT NULL DEFAULT 0, lease_until timestamptz, owner text,
      error_code text, result jsonb, traceparent text,
      created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,idempotency_key),
      FOREIGN KEY(tenant_id,document_id) REFERENCES atlas.documents(tenant_id,id)
    );
    CREATE TABLE atlas.outbox (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id),id uuid NOT NULL,job_id uuid NOT NULL,
      available_at timestamptz NOT NULL DEFAULT now(),published_at timestamptz,
      PRIMARY KEY(tenant_id,id),FOREIGN KEY(tenant_id,job_id) REFERENCES atlas.ingestion_jobs(tenant_id,id)
    );
    CREATE INDEX ON atlas.outbox(available_at) WHERE published_at IS NULL;
    CREATE INDEX ON atlas.ingestion_jobs(status,lease_until);
    """)
    for table in ["tenant_limits", "budget_periods", "reservations", "ingestion_jobs", "outbox"]:
        op.execute(f"ALTER TABLE atlas.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE atlas.{table} FORCE ROW LEVEL SECURITY")
        condition = "tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid"
        op.execute(
            f"CREATE POLICY tenant_scope ON atlas.{table} TO atlas_app USING({condition}) WITH CHECK({condition})"
        )
        op.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON atlas.{table} TO atlas_app")
    op.execute("""
    DO $$ BEGIN
      IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='atlas_worker') THEN
        CREATE ROLE atlas_worker LOGIN NOSUPERUSER NOBYPASSRLS IN ROLE atlas_app;
      END IF;
      IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='atlas_dispatch') THEN
        CREATE ROLE atlas_dispatch NOLOGIN NOSUPERUSER NOBYPASSRLS;
      END IF;
    END $$;
    ALTER ROLE atlas_worker SET search_path=atlas,public;
    GRANT USAGE ON SCHEMA atlas TO atlas_dispatch;
    GRANT SELECT,UPDATE ON atlas.outbox TO atlas_dispatch;
    CREATE POLICY dispatch ON atlas.outbox TO atlas_dispatch USING(true) WITH CHECK(true);
    CREATE FUNCTION atlas.dispatch_outbox() RETURNS TABLE(tenant_id uuid,id uuid,job_id uuid)
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      SELECT o.tenant_id,o.id,o.job_id FROM atlas.outbox o
      WHERE o.published_at IS NULL AND o.available_at<=now()
      ORDER BY o.available_at LIMIT 20 FOR UPDATE SKIP LOCKED
    $$;
    ALTER FUNCTION atlas.dispatch_outbox() OWNER TO atlas_dispatch;
    REVOKE ALL ON FUNCTION atlas.dispatch_outbox() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION atlas.dispatch_outbox() TO atlas_worker;
    """)
    if not settings.worker_db_password:
        raise RuntimeError("Generate WORKER_DB_PASSWORD before applying this migration")
    op.get_bind().exec_driver_sql(
        sql.SQL("ALTER ROLE atlas_worker PASSWORD {}")
        .format(sql.Literal(settings.worker_db_password))
        .as_string()
    )


def downgrade():
    raise RuntimeError("Restore a backup")
