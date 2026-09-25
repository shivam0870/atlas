"""Keep tenant deletion from racing operational alert creation."""

from alembic import op

revision = "021"
down_revision = "020"


def upgrade():
    op.execute("""
    DO $$ DECLARE definition text;
    BEGIN
      SELECT pg_get_functiondef('atlas.refresh_operational_alerts(boolean,boolean)'::regprocedure) INTO definition;
      EXECUTE replace(definition,'WHERE status=''active'' LOOP','WHERE status=''active'' FOR KEY SHARE LOOP');
      SELECT pg_get_functiondef('atlas.record_scanner_health(boolean)'::regprocedure) INTO definition;
      EXECUTE replace(definition,'FROM atlas.tenants WHERE status=''active''',
        'FROM atlas.tenants WHERE status=''active'' FOR KEY SHARE');
    END $$;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup")
