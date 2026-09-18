"""Versioned knowledge, resource grants, private conversations and product workflows."""

from alembic import op

revision = "007"
down_revision = "006"


def upgrade():
    op.execute("""
    CREATE TABLE atlas.spaces (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id) ON DELETE CASCADE,id uuid NOT NULL,
      name text NOT NULL,description text NOT NULL DEFAULT '',
      visibility text NOT NULL DEFAULT 'company' CHECK(visibility IN ('company','restricted')),
      created_by uuid REFERENCES atlas.users(id),owner_user_id uuid REFERENCES atlas.users(id),
      tags text[] NOT NULL DEFAULT '{}',created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,name)
    );
    INSERT INTO atlas.spaces(tenant_id,id,name) SELECT id,gen_random_uuid(),'General' FROM atlas.tenants;
    ALTER TABLE atlas.documents ADD COLUMN space_id uuid,ADD COLUMN owner_user_id uuid REFERENCES atlas.users(id),
      ADD COLUMN tags text[] NOT NULL DEFAULT '{}',ADD COLUMN review_due_at timestamptz,
      ADD COLUMN lifecycle text NOT NULL DEFAULT 'active' CHECK(lifecycle IN ('active','archived','trashed')),
      ADD COLUMN restricted boolean NOT NULL DEFAULT false,
      ADD COLUMN current_version_id uuid,ADD COLUMN pending_version_id uuid,
      ADD FOREIGN KEY(tenant_id,space_id) REFERENCES atlas.spaces(tenant_id,id);
    UPDATE atlas.documents d SET space_id=s.id,lifecycle=CASE WHEN d.status='deleted' THEN 'trashed' ELSE 'active' END
      FROM atlas.spaces s WHERE d.tenant_id=s.tenant_id;
    CREATE TABLE atlas.document_versions (
      tenant_id uuid NOT NULL,id uuid NOT NULL,document_id uuid NOT NULL,number integer NOT NULL CHECK(number>0),
      title text NOT NULL,content text NOT NULL,content_hash text NOT NULL,media_type text NOT NULL DEFAULT 'text/plain',
      filename text NOT NULL DEFAULT '',storage_key text,byte_size bigint NOT NULL DEFAULT 0,
      source_segments jsonb NOT NULL DEFAULT '[]',
      status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','indexing','ready','failed')),
      created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,document_id,number),UNIQUE(tenant_id,id,document_id),
      FOREIGN KEY(tenant_id,document_id) REFERENCES atlas.documents(tenant_id,id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
    );
    INSERT INTO atlas.document_versions(tenant_id,id,document_id,number,title,content,content_hash,media_type,filename,byte_size,status)
      SELECT tenant_id,gen_random_uuid(),id,1,title,content,content_hash,media_type,title,octet_length(content),
        CASE WHEN status='ready' OR EXISTS(SELECT 1 FROM atlas.chunks c WHERE c.tenant_id=documents.tenant_id AND c.document_id=documents.id) THEN 'ready' ELSE 'pending' END FROM atlas.documents;
    UPDATE atlas.document_versions SET source_segments=jsonb_build_array(jsonb_build_object('start',0,'end',length(content),'section','Document'));
    UPDATE atlas.documents d SET current_version_id=CASE WHEN v.status='ready' THEN v.id END,
      pending_version_id=CASE WHEN v.status!='ready' THEN v.id END FROM atlas.document_versions v WHERE v.tenant_id=d.tenant_id AND v.document_id=d.id;
    ALTER TABLE atlas.documents ADD FOREIGN KEY(tenant_id,current_version_id,id) REFERENCES atlas.document_versions(tenant_id,id,document_id) DEFERRABLE INITIALLY DEFERRED,
      ADD FOREIGN KEY(tenant_id,pending_version_id,id) REFERENCES atlas.document_versions(tenant_id,id,document_id) DEFERRABLE INITIALLY DEFERRED;
    ALTER TABLE atlas.chunks ADD COLUMN version_id uuid;
    UPDATE atlas.chunks c SET version_id=COALESCE(d.current_version_id,d.pending_version_id) FROM atlas.documents d WHERE c.tenant_id=d.tenant_id AND c.document_id=d.id;
    ALTER TABLE atlas.chunks ADD FOREIGN KEY(tenant_id,version_id,document_id) REFERENCES atlas.document_versions(tenant_id,id,document_id) ON DELETE CASCADE,
      DROP CONSTRAINT chunks_tenant_id_document_id_pipeline_hash_ordinal_key;
    ALTER TABLE atlas.chunks ADD CONSTRAINT chunk_version_ordinal UNIQUE NULLS NOT DISTINCT(tenant_id,document_id,pipeline_hash,version_id,ordinal);
    CREATE TABLE atlas.resource_grants (
      tenant_id uuid NOT NULL,id uuid NOT NULL,space_id uuid,document_id uuid,
      subject_type text NOT NULL CHECK(subject_type IN ('user','team','service')),subject_id uuid NOT NULL,
      permission text NOT NULL DEFAULT 'read' CHECK(permission IN ('read','write')),
      PRIMARY KEY(tenant_id,id),CHECK((space_id IS NULL)<>(document_id IS NULL)),
      FOREIGN KEY(tenant_id,space_id) REFERENCES atlas.spaces(tenant_id,id) ON DELETE CASCADE,
      FOREIGN KEY(tenant_id,document_id) REFERENCES atlas.documents(tenant_id,id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX grant_unique ON atlas.resource_grants(tenant_id,space_id,document_id,subject_type,subject_id) NULLS NOT DISTINCT;
    ALTER TABLE atlas.entities ADD COLUMN space_id uuid,ADD COLUMN archived boolean NOT NULL DEFAULT false,
      ADD COLUMN document_ids uuid[] NOT NULL DEFAULT '{}',ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now(),
      ADD FOREIGN KEY(tenant_id,space_id) REFERENCES atlas.spaces(tenant_id,id);
    UPDATE atlas.entities e SET space_id=s.id FROM atlas.spaces s WHERE e.tenant_id=s.tenant_id;
    CREATE TABLE atlas.conversations (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id),id uuid NOT NULL,user_id uuid NOT NULL REFERENCES atlas.users(id),
      title text NOT NULL DEFAULT 'New conversation',space_ids uuid[] NOT NULL DEFAULT '{}',document_ids uuid[] NOT NULL DEFAULT '{}',
      pinned boolean NOT NULL DEFAULT false,archived boolean NOT NULL DEFAULT false,
      created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id)
    );
    CREATE TABLE atlas.messages (
      tenant_id uuid NOT NULL,id uuid NOT NULL,conversation_id uuid NOT NULL,user_id uuid NOT NULL REFERENCES atlas.users(id),
      role text NOT NULL CHECK(role IN ('user','assistant')),content text NOT NULL DEFAULT '',
      status text NOT NULL DEFAULT 'completed' CHECK(status IN ('queued','running','completed','cancelled','failed','unavailable')),
      sources jsonb NOT NULL DEFAULT '[]',request_id uuid,idempotency_key text,
      metadata jsonb NOT NULL DEFAULT '{}',created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id),FOREIGN KEY(tenant_id,conversation_id) REFERENCES atlas.conversations(tenant_id,id) ON DELETE CASCADE,
      UNIQUE(tenant_id,conversation_id,idempotency_key,role)
    );
    ALTER TABLE atlas.queries ADD COLUMN user_id uuid REFERENCES atlas.users(id),ADD COLUMN conversation_id uuid,
      ADD COLUMN principal_id uuid,ADD COLUMN principal_kind text;
    ALTER TABLE atlas.usage_ledger ADD COLUMN user_id uuid REFERENCES atlas.users(id),ADD COLUMN principal_id uuid;
    ALTER TABLE atlas.reservations ADD COLUMN user_id uuid REFERENCES atlas.users(id);
    CREATE TABLE atlas.bookmarks (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id),id uuid NOT NULL,user_id uuid NOT NULL REFERENCES atlas.users(id),
      kind text NOT NULL CHECK(kind IN ('document','message')),resource_id uuid NOT NULL,title text NOT NULL DEFAULT '',
      created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,user_id,kind,resource_id)
    );
    CREATE TABLE atlas.feedback (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id),id uuid NOT NULL,user_id uuid NOT NULL REFERENCES atlas.users(id),
      message_id uuid,query_id uuid,rating integer NOT NULL CHECK(rating IN (-1,1)),reason text NOT NULL DEFAULT '',
      correction text NOT NULL DEFAULT '',question text NOT NULL DEFAULT '',sources jsonb NOT NULL DEFAULT '[]',
      status text NOT NULL DEFAULT 'new' CHECK(status IN ('new','reviewed','candidate','closed')),
      created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,id)
    );
    CREATE TABLE atlas.notifications (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id),id uuid NOT NULL,user_id uuid NOT NULL REFERENCES atlas.users(id),
      kind text NOT NULL,title text NOT NULL,body text NOT NULL DEFAULT '',link text NOT NULL DEFAULT '',dedupe_key text NOT NULL,
      read_at timestamptz,created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,user_id,dedupe_key)
    );
    CREATE TABLE atlas.access_requests (
      tenant_id uuid NOT NULL,id uuid NOT NULL,user_id uuid NOT NULL REFERENCES atlas.users(id),space_id uuid NOT NULL,
      status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','denied')),reason text NOT NULL DEFAULT '',
      created_at timestamptz NOT NULL DEFAULT now(),resolved_at timestamptz,PRIMARY KEY(tenant_id,id),
      FOREIGN KEY(tenant_id,space_id) REFERENCES atlas.spaces(tenant_id,id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX pending_access_request ON atlas.access_requests(tenant_id,user_id,space_id) WHERE status='pending';
    CREATE TABLE atlas.audit_events (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id),id uuid NOT NULL DEFAULT gen_random_uuid(),
      actor_id uuid,actor_kind text NOT NULL DEFAULT 'system',action text NOT NULL,resource_type text NOT NULL DEFAULT '',
      resource_id uuid,outcome text NOT NULL DEFAULT 'success',metadata jsonb NOT NULL DEFAULT '{}',
      created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,id)
    );
    CREATE TABLE atlas.evaluation_jobs (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id),id uuid NOT NULL,user_id uuid NOT NULL REFERENCES atlas.users(id),
      config jsonb NOT NULL,status text NOT NULL DEFAULT 'queued',progress integer NOT NULL DEFAULT 0,
      total integer NOT NULL DEFAULT 0,session_id uuid REFERENCES atlas.user_sessions(id),
      auth_revision bigint NOT NULL DEFAULT 0,attempts integer NOT NULL DEFAULT 0,
      result_id uuid,error_code text,cancel_requested boolean NOT NULL DEFAULT false,owner text,lease_until timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,id)
    );
    CREATE INDEX ON atlas.conversations(tenant_id,user_id,updated_at DESC);
    CREATE INDEX ON atlas.messages(tenant_id,conversation_id,created_at);
    CREATE INDEX ON atlas.audit_events(tenant_id,created_at DESC);
    CREATE INDEX ON atlas.documents(tenant_id,space_id,lifecycle,updated_at DESC);
    CREATE INDEX ON atlas.notifications(tenant_id,user_id,created_at DESC);
    """)
    # The broker can administer identity and workspace metadata, never read document bodies.
    for table in ["spaces", "notifications", "audit_events"]:
        op.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON atlas.{table} TO atlas_identity")
    op.execute("GRANT SELECT,UPDATE ON atlas.api_keys TO atlas_identity")
    op.execute(
        "CREATE POLICY identity_key_management ON atlas.api_keys TO atlas_identity USING(true) WITH CHECK(true)"
    )
    for table in ["spaces", "resource_grants"]:
        op.execute(
            f"CREATE TRIGGER auth_revision AFTER INSERT OR UPDATE OR DELETE ON atlas.{table} FOR EACH ROW EXECUTE FUNCTION atlas.bump_auth_revision()"
        )
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")


def downgrade():
    raise RuntimeError("Restore backup; versions and private user data must be preserved")
