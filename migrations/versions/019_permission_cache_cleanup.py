"""Durable deletion of permission-sensitive caches after authorization changes."""

from alembic import op

revision = "019"
down_revision = "018"


def upgrade():
    op.execute("""
    CREATE TABLE atlas.permission_cache_invalidations (
      tenant_id uuid PRIMARY KEY REFERENCES atlas.tenants(id) ON DELETE CASCADE,
      revision bigint NOT NULL, changed_at timestamptz NOT NULL DEFAULT now()
    );
    ALTER TABLE atlas.permission_cache_invalidations ENABLE ROW LEVEL SECURITY;
    ALTER TABLE atlas.permission_cache_invalidations FORCE ROW LEVEL SECURITY;
    REVOKE ALL ON atlas.permission_cache_invalidations FROM PUBLIC,atlas_app,atlas_identity;
    CREATE FUNCTION atlas.invalidate_permission_cache() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    BEGIN
      IF NEW.auth_revision IS DISTINCT FROM OLD.auth_revision THEN
        INSERT INTO atlas.permission_cache_invalidations(tenant_id,revision) VALUES(NEW.id,NEW.auth_revision)
          ON CONFLICT(tenant_id) DO UPDATE SET revision=excluded.revision,changed_at=now();
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER permission_cache_invalidation AFTER UPDATE OF auth_revision ON atlas.tenants
      FOR EACH ROW EXECUTE FUNCTION atlas.invalidate_permission_cache();
    CREATE FUNCTION atlas.pending_permission_cache_invalidations()
      RETURNS TABLE(tenant_id uuid,revision bigint)
      LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
        SELECT tenant_id,revision FROM atlas.permission_cache_invalidations ORDER BY changed_at LIMIT 50
      $$;
    CREATE FUNCTION atlas.ack_permission_cache_invalidation(target uuid,seen_revision bigint)
      RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
        DELETE FROM atlas.permission_cache_invalidations WHERE tenant_id=target AND revision=seen_revision
      $$;
    REVOKE ALL ON FUNCTION atlas.invalidate_permission_cache(),atlas.pending_permission_cache_invalidations(),atlas.ack_permission_cache_invalidation(uuid,bigint) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION atlas.pending_permission_cache_invalidations(),atlas.ack_permission_cache_invalidation(uuid,bigint) TO atlas_worker;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup")
