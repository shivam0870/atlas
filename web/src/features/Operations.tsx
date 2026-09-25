import { useState } from "react";
import { Link } from "react-router-dom";
import { RefreshCw, ShieldCheck } from "lucide-react";
import { api } from "../api/client";
import { useWorkspace } from "../app/context";
import {
  Badge,
  Button,
  EmptyState,
  ErrorNotice,
  Loading,
  Modal,
  Notice,
} from "../components/ui";
import {
  formatDate,
  useWorkbenchActions,
  useWorkbenchQuery,
} from "./WorkbenchShared";
type Health = { ready: boolean; model?: string };
type Operations = {
  health: { database: Health; queue: Health; generation: Health };
  queue: { queued: number; running: number; retry: number; dead: number };
  alerts: {
    id: string;
    severity: string;
    kind: string;
    message: string;
    created_at: string;
    resolved_at?: string;
    acknowledged_at?: string;
  }[];
  backups: {
    id: string;
    kind: string;
    status: string;
    created_at: string;
    completed_at?: string;
    verified_at?: string;
    error_code?: string;
  }[];
  releases: {
    id: string;
    name: string;
    status: string;
    manifest: Record<string, unknown>;
    created_at: string;
  }[];
  platform_operator: boolean;
};
type Job = {
  id: string;
  document_id: string;
  title: string;
  status: string;
  attempts: number;
  error_code?: string;
  created_at: string;
  updated_at: string;
  lease_until?: string;
};
export function OperationsPanel() {
  const { tenantId, canManage } = useWorkspace();
  const actions = useWorkbenchActions();
  const status = useWorkbenchQuery<Operations>(
    "operations",
    "/operations",
    canManage,
    true,
  );
  const jobs = useWorkbenchQuery<{ items: Job[]; total: number }>(
    "operations-jobs",
    "/operations/jobs",
    canManage,
    true,
  );
  const [cancel, setCancel] = useState<Job | null>(null);
  if (!canManage)
    return (
      <EmptyState
        icon={<ShieldCheck />}
        title="Management access required"
        description="An owner or administrator can inspect operations for this company."
      />
    );
  return (
    <>
      <div className="row between">
        <div>
          <h2>Operations & recovery</h2>
          <p className="muted">
            Service readiness, actionable failures, and recorded recovery
            checks. Updates every five seconds.
          </p>
        </div>
        <Button
          variant="secondary"
          onClick={() => {
            void status.refetch();
            void jobs.refetch();
          }}
        >
          <RefreshCw size={15} />
          Refresh
        </Button>
      </div>
      <ErrorNotice error={status.error || jobs.error || actions.error} />
      {actions.message && <Notice>{actions.message}</Notice>}
      {status.isPending ? (
        <Loading />
      ) : (
        status.data && (
          <>
            <div className="card-grid operations-health">
              {Object.entries(status.data.health).map(([name, value]) => (
                <section className="panel" key={name}>
                  <h3>{name.charAt(0).toUpperCase() + name.slice(1)}</h3>
                  <Badge tone={value.ready ? "success" : "danger"}>
                    {value.ready ? "Ready" : "Unavailable"}
                  </Badge>
                  {value.model && (
                    <p className="muted break-word">{value.model}</p>
                  )}
                </section>
              ))}
            </div>
            <div
              className="workbench-summary"
              aria-label="Ingestion queue depth"
            >
              {Object.entries(status.data.queue).map(([name, count]) => (
                <span key={name}>
                  <strong>{count}</strong> {name}
                </span>
              ))}
            </div>
            <section className="panel">
              <h2>Alerts</h2>
              {status.data.alerts.length === 0 ? (
                <p className="muted">No operational alerts recorded.</p>
              ) : (
                <ul className="relationship-list">
                  {status.data.alerts.map((alert) => (
                    <li key={alert.id}>
                      <div>
                        <Badge
                          tone={
                            alert.severity === "critical" ? "danger" : "warning"
                          }
                        >
                          {alert.severity}
                        </Badge>{" "}
                        <strong>{alert.kind}</strong>
                        <p>{alert.message}</p>
                        <small className="muted">
                          {formatDate(alert.created_at)}
                          {alert.resolved_at
                            ? " · Resolved"
                            : alert.acknowledged_at
                              ? " · Acknowledged"
                              : ""}
                        </small>
                      </div>
                      {!alert.acknowledged_at && !alert.resolved_at && (
                        <Button
                          variant="secondary"
                          busy={actions.busy}
                          onClick={() =>
                            actions.run(async (signal) => {
                              await api(`/operations/alerts/${alert.id}/ack`, {
                                tenantId,
                                method: "POST",
                                signal,
                              });
                            })
                          }
                        >
                          Acknowledge
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section className="panel">
              <h2>Backup & restoration evidence</h2>
              <p className="muted">
                Platform operators run backups and isolated restore drills
                through the recovery tools. Their recorded results appear here.
              </p>
              {status.data.backups.length === 0 ? (
                <p className="muted">
                  No backup or restore evidence recorded yet.
                </p>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Operation</th>
                        <th>Status</th>
                        <th>Started</th>
                        <th>Completed</th>
                        <th>Restore verified</th>
                        <th>Failure</th>
                      </tr>
                    </thead>
                    <tbody>
                      {status.data.backups.map((backup) => (
                        <tr key={backup.id}>
                          <td>{backup.kind}</td>
                          <td>
                            <Badge>{backup.status}</Badge>
                          </td>
                          <td>{formatDate(backup.created_at)}</td>
                          <td>{formatDate(backup.completed_at)}</td>
                          <td>{formatDate(backup.verified_at)}</td>
                          <td>{backup.error_code || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
            <section className="panel">
              <h2>Release history</h2>
              {status.data.releases.length === 0 ? (
                <p className="muted">No release manifests recorded yet.</p>
              ) : (
                status.data.releases.map((release) => (
                  <details className="release-record" key={release.id}>
                    <summary>
                      {release.name} · {release.status} ·{" "}
                      {formatDate(release.created_at)}
                    </summary>
                    <pre className="source-passage">
                      {JSON.stringify(release.manifest, null, 2)}
                    </pre>
                  </details>
                ))
              )}
            </section>
          </>
        )
      )}
      <section className="panel">
        <h2>Ingestion jobs</h2>
        {jobs.isPending ? (
          <Loading />
        ) : jobs.data?.items.length === 0 ? (
          <p className="muted">No ingestion jobs recorded.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Document</th>
                  <th>Status</th>
                  <th>Attempts</th>
                  <th>Last update</th>
                  <th>Failure</th>
                  <th>Recovery</th>
                </tr>
              </thead>
              <tbody>
                {jobs.data?.items.map((job) => (
                  <tr key={job.id}>
                    <td>
                      <Link to={`/o/${tenantId}/library/${job.document_id}`}>
                        {job.title || "Document"}
                      </Link>
                    </td>
                    <td>
                      <Badge>{job.status}</Badge>
                    </td>
                    <td>{job.attempts}</td>
                    <td>{formatDate(job.updated_at)}</td>
                    <td>{job.error_code || "—"}</td>
                    <td>
                      <div className="workbench-actions">
                        {["failed", "dead", "cancelled"].includes(
                          job.status,
                        ) && (
                          <Button
                            variant="secondary"
                            busy={actions.busy}
                            onClick={() =>
                              actions.run(async (signal) => {
                                await api(`/operations/jobs/${job.id}/retry`, {
                                  tenantId,
                                  method: "POST",
                                  signal,
                                });
                                actions.setMessage("Job queued for retry.");
                              })
                            }
                          >
                            Retry
                          </Button>
                        )}
                        {["queued", "retry", "running"].includes(
                          job.status,
                        ) && (
                          <Button
                            variant="ghost"
                            onClick={() => setCancel(job)}
                          >
                            Cancel
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {jobs.data && jobs.data.total > jobs.data.items.length && (
          <p className="muted">
            Showing {jobs.data.items.length} of {jobs.data.total} jobs.
          </p>
        )}
      </section>
      <Modal
        open={!!cancel}
        onOpenChange={(open) => !open && setCancel(null)}
        title="Cancel ingestion job"
        description={`Stop ingestion for ${cancel?.title || "this document"}. A published version remains available while a replacement is cancelled.`}
      >
        <ErrorNotice error={actions.error} />
        <div className="workbench-actions">
          <Button variant="secondary" onClick={() => setCancel(null)}>
            Keep running
          </Button>
          <Button
            variant="danger"
            busy={actions.busy}
            onClick={() =>
              actions.run(async (signal) => {
                await api(`/operations/jobs/${cancel?.id}/cancel`, {
                  tenantId,
                  method: "POST",
                  signal,
                });
                setCancel(null);
                actions.setMessage("Job cancelled.");
              })
            }
          >
            Cancel job
          </Button>
        </div>
      </Modal>
    </>
  );
}
