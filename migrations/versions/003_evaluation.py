"""Review-gated evaluation, immutable evidence references, and versioned results."""

from alembic import op

revision = "003"
down_revision = "002"


def upgrade():
    op.execute("""
    CREATE TABLE atlas.eval_labels (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id), id uuid NOT NULL,
      question text NOT NULL, document_id uuid NOT NULL, source_hash text NOT NULL,
      start_offset integer NOT NULL, end_offset integer NOT NULL,
      split text NOT NULL CHECK(split IN ('development','held_out')),
      reviewed boolean NOT NULL DEFAULT false, reviewed_by uuid, reviewed_at timestamptz,
      review_hash text, created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id), FOREIGN KEY(tenant_id,document_id) REFERENCES atlas.documents(tenant_id,id),
      UNIQUE(tenant_id,document_id), CHECK(end_offset>start_offset),
      CHECK(NOT reviewed OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL AND review_hash IS NOT NULL))
    );
    CREATE TABLE atlas.eval_runs (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id), id uuid NOT NULL,
      mode text NOT NULL, config jsonb NOT NULL, label_count integer NOT NULL,
      metrics jsonb NOT NULL, results jsonb NOT NULL, manifest jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,id)
    );
    """)
    for table in ["eval_labels", "eval_runs"]:
        op.execute(f"ALTER TABLE atlas.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE atlas.{table} FORCE ROW LEVEL SECURITY")
        condition = "tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid"
        op.execute(
            f"CREATE POLICY tenant_scope ON atlas.{table} TO atlas_app USING({condition}) WITH CHECK({condition})"
        )
        op.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON atlas.{table} TO atlas_app")


def downgrade():
    raise RuntimeError("Preserve evaluation history; restore a backup if required")
