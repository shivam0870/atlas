"""Deliver access-request notices without granting access to another user's inbox."""

from alembic import op

revision = "011"
down_revision = "010"


def upgrade():
    op.execute("""
    CREATE FUNCTION atlas.access_request_notification() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    BEGIN
      IF NEW.status='pending' THEN
        INSERT INTO atlas.notifications(tenant_id,id,user_id,kind,title,body,link,dedupe_key)
          SELECT NEW.tenant_id,gen_random_uuid(),m.user_id,'access_request','Knowledge access requested',
            'A company member requested access to a restricted space.','/library','access:'||NEW.id::text
          FROM atlas.memberships m WHERE m.tenant_id=NEW.tenant_id AND m.status='active' AND m.role IN ('owner','admin')
          ON CONFLICT(tenant_id,user_id,dedupe_key) DO NOTHING;
      ELSE
        INSERT INTO atlas.notifications(tenant_id,id,user_id,kind,title,body,link,dedupe_key)
          VALUES(NEW.tenant_id,gen_random_uuid(),NEW.user_id,'access_request','Access request '||NEW.status,'',
            '/library','access-resolution:'||NEW.id::text) ON CONFLICT(tenant_id,user_id,dedupe_key) DO NOTHING;
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION atlas.access_request_notification() FROM PUBLIC;
    CREATE TRIGGER access_request_notification AFTER INSERT OR UPDATE ON atlas.access_requests
      FOR EACH ROW EXECUTE FUNCTION atlas.access_request_notification();
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup")
