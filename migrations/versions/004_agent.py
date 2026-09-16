"""Tenant-owned structured inventory for read-only agent and MCP tools."""

from alembic import op

revision = "004"
down_revision = "003"


def upgrade():
    op.execute("""
    CREATE TABLE atlas.entities (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id), id uuid NOT NULL,
      name text NOT NULL, kind text NOT NULL, attributes jsonb NOT NULL,
      revision bigint NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,name,kind)
    );
    ALTER TABLE atlas.entities ENABLE ROW LEVEL SECURITY;
    ALTER TABLE atlas.entities FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_scope ON atlas.entities TO atlas_app
      USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
      WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
    GRANT SELECT,INSERT,UPDATE,DELETE ON atlas.entities TO atlas_app;
    """)


def downgrade():
    raise RuntimeError("Restore a backup")
