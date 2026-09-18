"""People, revocable sessions, organization memberships and service identities."""

from alembic import op
from psycopg import sql

from atlas.config import settings

revision = "006"
down_revision = "005"


def upgrade():
    op.execute("""
    DO $$ BEGIN
      IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='atlas_identity') THEN
        CREATE ROLE atlas_identity LOGIN NOSUPERUSER NOBYPASSRLS;
      END IF;
    END $$;
    GRANT USAGE ON SCHEMA atlas TO atlas_identity;
    ALTER ROLE atlas_identity SET search_path=atlas,public;
    ALTER TABLE atlas.tenants ADD COLUMN description text NOT NULL DEFAULT '',
      ADD COLUMN website text NOT NULL DEFAULT '',
      ADD COLUMN kind text NOT NULL DEFAULT 'company' CHECK(kind IN ('company','personal')),
      ADD COLUMN auth_revision bigint NOT NULL DEFAULT 0,
      ADD COLUMN claimed_at timestamptz;
    CREATE TABLE atlas.users (
      id uuid PRIMARY KEY, email text NOT NULL UNIQUE CHECK(email=lower(email)),
      name text NOT NULL,password_hash text NOT NULL,email_verified_at timestamptz,
      disabled_at timestamptz,theme text NOT NULL DEFAULT 'system' CHECK(theme IN ('light','dark','system')),
      mfa_secret text,mfa_pending_secret text,mfa_last_step bigint NOT NULL DEFAULT -1,
      created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE atlas.user_sessions (
      id uuid PRIMARY KEY,user_id uuid NOT NULL REFERENCES atlas.users(id) ON DELETE CASCADE,
      digest text NOT NULL UNIQUE CHECK(length(digest)=64),
      created_at timestamptz NOT NULL DEFAULT now(),last_seen_at timestamptz NOT NULL DEFAULT now(),
      expires_at timestamptz NOT NULL,revoked_at timestamptz,reauthenticated_at timestamptz,
      user_agent text NOT NULL DEFAULT ''
    );
    CREATE INDEX ON atlas.user_sessions(user_id,created_at);
    CREATE TABLE atlas.account_tokens (
      id uuid PRIMARY KEY,user_id uuid NOT NULL REFERENCES atlas.users(id) ON DELETE CASCADE,
      digest text NOT NULL UNIQUE CHECK(length(digest)=64),
      purpose text NOT NULL CHECK(purpose IN ('verify','reset','email_change','mfa_login')),
      payload jsonb NOT NULL DEFAULT '{}',expires_at timestamptz NOT NULL,used_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE atlas.recovery_codes (
      user_id uuid NOT NULL REFERENCES atlas.users(id) ON DELETE CASCADE,
      digest text NOT NULL,used_at timestamptz,PRIMARY KEY(user_id,digest)
    );
    CREATE TABLE atlas.memberships (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id) ON DELETE CASCADE,
      user_id uuid NOT NULL REFERENCES atlas.users(id),
      role text NOT NULL CHECK(role IN ('owner','admin','editor','viewer')),
      status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','suspended')),
      can_evaluate boolean NOT NULL DEFAULT false,monthly_tokens bigint CHECK(monthly_tokens>=0),
      created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,user_id)
    );
    CREATE INDEX ON atlas.memberships(user_id,status);
    CREATE TABLE atlas.invitations (
      id uuid PRIMARY KEY,tenant_id uuid NOT NULL REFERENCES atlas.tenants(id) ON DELETE CASCADE,
      email text NOT NULL,role text NOT NULL CHECK(role IN ('admin','editor','viewer')),
      digest text NOT NULL UNIQUE,invited_by uuid NOT NULL REFERENCES atlas.users(id),
      expires_at timestamptz NOT NULL,accepted_at timestamptz,revoked_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ON atlas.invitations(tenant_id,email);
    CREATE TABLE atlas.teams (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id) ON DELETE CASCADE,id uuid NOT NULL,
      name text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,name)
    );
    CREATE TABLE atlas.team_members (
      tenant_id uuid NOT NULL,team_id uuid NOT NULL,user_id uuid NOT NULL,
      PRIMARY KEY(tenant_id,team_id,user_id),
      FOREIGN KEY(tenant_id,team_id) REFERENCES atlas.teams(tenant_id,id) ON DELETE CASCADE,
      FOREIGN KEY(tenant_id,user_id) REFERENCES atlas.memberships(tenant_id,user_id) ON DELETE CASCADE
    );
    CREATE TABLE atlas.service_accounts (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id) ON DELETE CASCADE,id uuid NOT NULL,
      name text NOT NULL,legacy boolean NOT NULL DEFAULT false,active boolean NOT NULL DEFAULT true,
      created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,id)
    );
    INSERT INTO atlas.service_accounts(tenant_id,id,name,legacy)
      SELECT tenant_id,id,label,true FROM atlas.api_keys;
    ALTER TABLE atlas.api_keys ADD COLUMN service_account_id uuid, ADD COLUMN last_used_at timestamptz;
    UPDATE atlas.api_keys SET service_account_id=id;
    ALTER TABLE atlas.api_keys ADD FOREIGN KEY(tenant_id,service_account_id)
      REFERENCES atlas.service_accounts(tenant_id,id);
    CREATE FUNCTION atlas.legacy_key_principal() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    BEGIN
      IF NEW.service_account_id IS NULL THEN
        IF EXISTS(SELECT 1 FROM atlas.tenants WHERE id=NEW.tenant_id AND claimed_at IS NOT NULL) THEN
          RAISE EXCEPTION 'Claimed organizations require an explicit service identity';
        END IF;
        INSERT INTO atlas.service_accounts(tenant_id,id,name,legacy)
          VALUES(NEW.tenant_id,NEW.id,NEW.label,true);
        NEW.service_account_id=NEW.id;
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION atlas.legacy_key_principal() FROM PUBLIC;
    CREATE TRIGGER legacy_key_principal BEFORE INSERT ON atlas.api_keys
      FOR EACH ROW EXECUTE FUNCTION atlas.legacy_key_principal();
    CREATE FUNCTION atlas.resolve_api_identity(key_digest text)
    RETURNS TABLE(tenant_id uuid,key_id uuid,name text,scopes text[],service_account_id uuid,auth_revision bigint)
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
      SELECT k.tenant_id,k.id,t.name,k.scopes,k.service_account_id,t.auth_revision
      FROM atlas.api_keys k JOIN atlas.tenants t ON t.id=k.tenant_id
      JOIN atlas.service_accounts s ON s.tenant_id=k.tenant_id AND s.id=k.service_account_id
      WHERE k.digest=key_digest AND k.revoked_at IS NULL AND (k.expires_at IS NULL OR k.expires_at>now())
        AND t.status='active' AND s.active AND (NOT s.legacy OR t.claimed_at IS NULL)
    $$;
    REVOKE ALL ON FUNCTION atlas.resolve_api_identity(text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION atlas.resolve_api_identity(text) TO atlas_app,atlas_identity;
    CREATE FUNCTION atlas.bump_auth_revision() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    BEGIN
      UPDATE atlas.tenants SET auth_revision=auth_revision+1 WHERE id=COALESCE(NEW.tenant_id,OLD.tenant_id);
      RETURN COALESCE(NEW,OLD);
    END $$;
    REVOKE ALL ON FUNCTION atlas.bump_auth_revision() FROM PUBLIC;
    """)
    for table in [
        "users",
        "user_sessions",
        "account_tokens",
        "recovery_codes",
        "memberships",
        "invitations",
        "teams",
        "team_members",
        "service_accounts",
    ]:
        op.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON atlas.{table} TO atlas_identity")
    op.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON atlas.tenants TO atlas_identity")
    op.execute(
        "CREATE POLICY identity_management ON atlas.tenants TO atlas_identity USING(true) WITH CHECK(true)"
    )
    for table in ["memberships", "team_members", "service_accounts"]:
        op.execute(
            f"CREATE TRIGGER auth_revision AFTER INSERT OR UPDATE OR DELETE ON atlas.{table} FOR EACH ROW EXECUTE FUNCTION atlas.bump_auth_revision()"
        )
    if not settings.identity_db_password:
        raise RuntimeError("IDENTITY_DB_PASSWORD must be generated before migration")
    op.get_bind().exec_driver_sql(
        sql.SQL("ALTER ROLE atlas_identity PASSWORD {}")
        .format(sql.Literal(settings.identity_db_password))
        .as_string()
    )


def downgrade():
    raise RuntimeError(
        "Restore the verified pre-upgrade backup; identity data must not be discarded"
    )
