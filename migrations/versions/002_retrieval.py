"""Local embeddings, collection-isolated BM25, citations, and query history."""

from alembic import op

revision = "002"
down_revision = "001"


def upgrade():
    op.execute("""
    CREATE TABLE atlas.collections (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id), id uuid NOT NULL,
      name text NOT NULL DEFAULT 'default', pipeline_hash text NOT NULL,
      revision bigint NOT NULL DEFAULT 0, created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,name,pipeline_hash)
    );
    ALTER TABLE atlas.chunks ADD COLUMN collection_id uuid;
    ALTER TABLE atlas.chunks ADD COLUMN token_count integer NOT NULL DEFAULT 0;
    ALTER TABLE atlas.chunks ADD COLUMN search_vector tsvector GENERATED ALWAYS AS (to_tsvector('english',content)) STORED;
    ALTER TABLE atlas.chunks ADD FOREIGN KEY(tenant_id,collection_id) REFERENCES atlas.collections(tenant_id,id);
    CREATE INDEX ON atlas.chunks USING gin(search_vector);
    CREATE INDEX ON atlas.chunks(tenant_id,collection_id);
    CREATE INDEX ON atlas.embeddings USING hnsw(embedding vector_cosine_ops);
    CREATE TABLE atlas.chunk_terms (
      tenant_id uuid NOT NULL, chunk_id uuid NOT NULL, term text NOT NULL,
      frequency integer NOT NULL CHECK(frequency>0), PRIMARY KEY(tenant_id,chunk_id,term),
      FOREIGN KEY(tenant_id,chunk_id) REFERENCES atlas.chunks(tenant_id,id) ON DELETE CASCADE
    );
    CREATE INDEX ON atlas.chunk_terms(tenant_id,term,chunk_id);
    CREATE TABLE atlas.embedding_cache (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id), content_hash text NOT NULL,
      model_revision text NOT NULL, embedding vector(384) NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,content_hash,model_revision)
    );
    CREATE TABLE atlas.queries (
      tenant_id uuid NOT NULL REFERENCES atlas.tenants(id), id uuid NOT NULL,
      question text NOT NULL, answer text NOT NULL DEFAULT '', status text NOT NULL DEFAULT 'running',
      sources jsonb NOT NULL DEFAULT '[]', mode text NOT NULL DEFAULT 'hybrid',
      cached boolean NOT NULL DEFAULT false, duration_ms double precision NOT NULL DEFAULT 0,
      first_token_ms double precision, error_code text, created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id)
    );
    CREATE INDEX ON atlas.queries(tenant_id,created_at DESC);
    """)
    for table in ["collections", "chunk_terms", "embedding_cache", "queries"]:
        op.execute(f"ALTER TABLE atlas.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE atlas.{table} FORCE ROW LEVEL SECURITY")
        condition = "tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid"
        op.execute(
            f"CREATE POLICY tenant_scope ON atlas.{table} TO atlas_app USING({condition}) WITH CHECK({condition})"
        )
        op.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON atlas.{table} TO atlas_app")


def downgrade():
    raise RuntimeError("Restore a backup instead of destroying indexed documents")
