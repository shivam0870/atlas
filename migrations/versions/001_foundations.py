"""Foundations: runtime roles, immutable tenant ownership, RLS, scoped key resolution."""

from alembic import op
from psycopg import sql

from atlas.config import settings

revision = "001"
down_revision = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA IF NOT EXISTS atlas")
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute("""
    DO $$ BEGIN
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='atlas_app') THEN
        CREATE ROLE atlas_app LOGIN NOSUPERUSER NOBYPASSRLS;
      END IF;
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='atlas_auth') THEN
        CREATE ROLE atlas_auth NOLOGIN NOSUPERUSER NOBYPASSRLS;
      END IF;
    END $$;
    """)
    if not settings.app_db_password:
        raise RuntimeError("APP_DB_PASSWORD is required for migration")
    op.get_bind().exec_driver_sql(
        sql.SQL("ALTER ROLE atlas_app PASSWORD {}")
        .format(sql.Literal(settings.app_db_password))
        .as_string()
    )
    op.execute("ALTER ROLE atlas_app SET search_path = atlas, public")
    op.execute("GRANT USAGE ON SCHEMA atlas TO atlas_app, atlas_auth")
    op.execute("""
    CREATE TABLE atlas.tenants (
      id uuid PRIMARY KEY, slug text UNIQUE NOT NULL, name text NOT NULL,
      status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','suspended')),
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE atlas.api_keys (
      id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES atlas.tenants(id),
      digest text UNIQUE NOT NULL CHECK(length(digest)=64), prefix text NOT NULL,
      label text NOT NULL DEFAULT 'API key', scopes text[] NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz,
      revoked_at timestamptz
    );
    CREATE INDEX ON atlas.api_keys(tenant_id, created_at);
    CREATE TABLE atlas.documents (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id), id uuid NOT NULL,
      title text NOT NULL, source_key text NOT NULL, content_hash text NOT NULL,
      content text NOT NULL, media_type text NOT NULL DEFAULT 'text/plain',
      metadata jsonb NOT NULL DEFAULT '{}', status text NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','indexing','ready','failed','deleted')),
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,source_key,content_hash)
    );
    CREATE INDEX ON atlas.documents(tenant_id,status);
    CREATE TABLE atlas.chunks (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id), id uuid NOT NULL,
      document_id uuid NOT NULL, pipeline_hash text NOT NULL, ordinal integer NOT NULL,
      start_offset integer NOT NULL CHECK(start_offset>=0), end_offset integer NOT NULL,
      content text NOT NULL, content_hash text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,id),
      FOREIGN KEY(tenant_id,document_id) REFERENCES atlas.documents(tenant_id,id) ON DELETE CASCADE,
      UNIQUE(tenant_id,document_id,pipeline_hash,ordinal), CHECK(end_offset>start_offset),
      CHECK(length(content)=end_offset-start_offset)
    );
    CREATE TABLE atlas.embeddings (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id), chunk_id uuid NOT NULL,
      model_revision text NOT NULL, embedding vector(384) NOT NULL,
      PRIMARY KEY(tenant_id,chunk_id,model_revision),
      FOREIGN KEY(tenant_id,chunk_id) REFERENCES atlas.chunks(tenant_id,id) ON DELETE CASCADE
    );
    CREATE TABLE atlas.usage_ledger (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id), id uuid NOT NULL,
      request_id uuid NOT NULL, operation text NOT NULL, backend text NOT NULL,
      model text NOT NULL, input_tokens bigint, output_tokens bigint,
      actual_api_cost_usd numeric(20,10) NOT NULL DEFAULT 0 CHECK(actual_api_cost_usd>=0),
      duration_ms double precision NOT NULL DEFAULT 0,
      status text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id)
    );
    CREATE INDEX ON atlas.usage_ledger(tenant_id,created_at);
    """)
    for table in ["tenants", "api_keys", "documents", "chunks", "embeddings", "usage_ledger"]:
        col = "id" if table == "tenants" else "tenant_id"
        op.execute(f"ALTER TABLE atlas.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE atlas.{table} FORCE ROW LEVEL SECURITY")
        condition = f"{col} = nullif(current_setting('app.tenant_id',true),'')::uuid"
        op.execute(
            f"CREATE POLICY tenant_scope ON atlas.{table} TO atlas_app USING ({condition}) WITH CHECK ({condition})"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON atlas.{table} TO atlas_app")
    # Authentication must resolve a tenant before a tenant-scoped transaction exists.
    # Its non-login owner can only read the two authentication tables, through explicit policies.
    op.execute("GRANT SELECT ON atlas.tenants, atlas.api_keys TO atlas_auth")
    op.execute("CREATE POLICY auth_lookup ON atlas.tenants FOR SELECT TO atlas_auth USING (true)")
    op.execute("CREATE POLICY auth_lookup ON atlas.api_keys FOR SELECT TO atlas_auth USING (true)")
    op.execute("""
    CREATE FUNCTION atlas.resolve_key(key_digest text)
    RETURNS TABLE(tenant_id uuid, key_id uuid, name text, scopes text[])
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, atlas AS $$
      SELECT k.tenant_id,k.id,t.name,k.scopes
      FROM atlas.api_keys k JOIN atlas.tenants t ON t.id=k.tenant_id
      WHERE k.digest=key_digest AND k.revoked_at IS NULL
      AND (k.expires_at IS NULL OR k.expires_at>now()) AND t.status='active'
    $$;
    ALTER FUNCTION atlas.resolve_key(text) OWNER TO atlas_auth;
    REVOKE ALL ON FUNCTION atlas.resolve_key(text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION atlas.resolve_key(text) TO atlas_app;
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade is intentionally disabled; restore a backup")
