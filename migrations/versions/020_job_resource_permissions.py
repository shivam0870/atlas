"""Apply document permissions to durable jobs and their dispatch records."""

from alembic import op

revision = "020"
down_revision = "019"


def upgrade():
    for table in ("ingestion_jobs", "outbox"):
        if table == "ingestion_jobs":
            read = "atlas.can_document(document_id)"
            write = "atlas.can_document(document_id,true)"
        else:
            read = "EXISTS(SELECT 1 FROM atlas.ingestion_jobs j WHERE j.tenant_id=outbox.tenant_id AND j.id=outbox.job_id AND atlas.can_document(j.document_id))"
            write = "EXISTS(SELECT 1 FROM atlas.ingestion_jobs j WHERE j.tenant_id=outbox.tenant_id AND j.id=outbox.job_id AND atlas.can_document(j.document_id,true))"
        for operation, clause in (
            ("SELECT", f"USING({read})"),
            ("INSERT", f"WITH CHECK({write})"),
            ("UPDATE", f"USING({write}) WITH CHECK({write})"),
            ("DELETE", f"USING({write})"),
        ):
            op.execute(
                f"CREATE POLICY job_resource_{operation.lower()} ON atlas.{table} "
                f"AS RESTRICTIVE FOR {operation} TO atlas_app {clause}"
            )
    op.execute("""
    CREATE FUNCTION atlas.record_scanner_health(scanner_ready boolean)
    RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,atlas AS $$
    BEGIN
      IF scanner_ready THEN
        UPDATE atlas.operational_alerts SET resolved_at=now()
          WHERE kind='scanner_unavailable' AND resolved_at IS NULL;
      ELSE
        INSERT INTO atlas.operational_alerts(tenant_id,kind,severity,message)
          SELECT id,'scanner_unavailable','critical','Upload scanning is unavailable or signatures are stale; uploads are blocked.'
          FROM atlas.tenants WHERE status='active'
          ON CONFLICT(tenant_id,kind) DO UPDATE SET last_seen_at=now(),resolved_at=NULL,
            acknowledged_at=CASE WHEN operational_alerts.resolved_at IS NOT NULL THEN NULL ELSE operational_alerts.acknowledged_at END,
            acknowledged_by=CASE WHEN operational_alerts.resolved_at IS NOT NULL THEN NULL ELSE operational_alerts.acknowledged_by END,
            created_at=CASE WHEN operational_alerts.resolved_at IS NOT NULL THEN now() ELSE operational_alerts.created_at END;
      END IF;
    END $$;
    REVOKE ALL ON FUNCTION atlas.record_scanner_health(boolean) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION atlas.record_scanner_health(boolean) TO atlas_worker;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup; job permission enforcement must not be removed")
