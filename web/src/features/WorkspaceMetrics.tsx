import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useScopedQueryKey, useWorkspace } from "../app/context";
import { Badge, Button, ErrorNotice, Loading } from "../components/ui";
export type SystemStatus = {
  database: { ready: boolean };
  redis: { ready: boolean };
  cache: { ready: boolean };
  generation: { ready: boolean; model?: string };
  embeddings: { ready: boolean };
  reranker: { ready: boolean };
  paid_apis: boolean;
};
export type Budget = {
  limits: { monthly_tokens: number; requests_per_minute: number };
  usage: {
    period: string;
    used_tokens: number;
    reserved_tokens: number;
  } | null;
};
export function WorkspaceHealth() {
  const { tenantId, canManage } = useWorkspace();
  const health = useQuery({
    queryKey: useScopedQueryKey("system"),
    queryFn: ({ signal }) =>
      api<SystemStatus>("/administration/system", { tenantId, signal }),
    enabled: canManage,
  });
  if (!canManage) return null;
  const components = [
    ["database", "Database"],
    ["redis", "Queue"],
    ["cache", "Answer cache"],
    ["generation", "Local model"],
    ["embeddings", "Embeddings"],
    ["reranker", "Optional reranker"],
  ] as const;
  return (
    <section className="panel" aria-label="Workspace health">
      <div className="row between">
        <h2>Workspace health</h2>
        <div className="row">
          <Button
            variant="ghost"
            busy={health.isFetching}
            onClick={() => void health.refetch()}
          >
            Refresh health
          </Button>
          <Link className="text-link" to={`/o/${tenantId}/insights`}>
            Open insights
          </Link>
        </div>
      </div>
      <ErrorNotice error={health.error} />
      {health.isPending ? (
        <Loading label="Checking workspace health…" />
      ) : (
        health.data &&
        !health.error && (
          <>
            <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
              {components.map(([key, label]) => (
                <Badge
                  key={key}
                  tone={health.data![key].ready ? "success" : "error"}
                >
                  {label}: {health.data![key].ready ? "Ready" : "Unavailable"}
                </Badge>
              ))}
            </div>
            <p className="muted text-small">
              Checked {new Date(health.dataUpdatedAt).toLocaleTimeString()} ·{" "}
              {health.data.generation.model || "No generation model reported"} ·
              Paid model APIs {health.data.paid_apis ? "enabled" : "disabled"}
            </p>
          </>
        )
      )}
    </section>
  );
}
export function BudgetSummary({ budget }: { budget: Budget }) {
  const used = Number(budget.usage?.used_tokens || 0),
    reserved = Number(budget.usage?.reserved_tokens || 0),
    limit = Number(budget.limits.monthly_tokens),
    committed = used + reserved;
  const percentage = limit > 0 ? (committed / limit) * 100 : 0;
  return (
    <>
      <p className="muted">
        Current calendar month. Includes tokens reserved by active operations;
        this does not follow the activity time-range filter.
      </p>
      <div className="row between">
        <strong>
          {limit === 0
            ? "No tokens allowed"
            : `${percentage.toFixed(1)}% allocated`}
        </strong>
        <span>
          {committed.toLocaleString()} / {limit.toLocaleString()} tokens
        </span>
      </div>
      <progress
        aria-label="Monthly token quota utilization"
        max={Math.max(1, limit)}
        value={Math.min(committed, Math.max(1, limit))}
        style={{ width: "100%", accentColor: "var(--accent)" }}
      />
      <dl className="key-values">
        <div>
          <dt>Used</dt>
          <dd>{used.toLocaleString()} tokens</dd>
        </div>
        <div>
          <dt>Reserved</dt>
          <dd>{reserved.toLocaleString()} tokens</dd>
        </div>
        <div>
          <dt>Available</dt>
          <dd>{Math.max(0, limit - committed).toLocaleString()} tokens</dd>
        </div>
        <div>
          <dt>Request limit</dt>
          <dd>{budget.limits.requests_per_minute.toLocaleString()} / minute</dd>
        </div>
      </dl>
    </>
  );
}
export function MonthlyQuota() {
  const { tenantId, canManage } = useWorkspace();
  const budget = useQuery({
    queryKey: useScopedQueryKey("budget"),
    queryFn: ({ signal }) => api<Budget>("/budget", { tenantId, signal }),
    enabled: canManage,
  });
  if (!canManage) return null;
  return (
    <section className="panel">
      <div className="row between">
        <h2>Monthly quota utilization</h2>
        <div className="row">
          <Button
            variant="ghost"
            busy={budget.isFetching}
            onClick={() => void budget.refetch()}
          >
            Refresh quota
          </Button>
          <Link className="text-link" to={`/o/${tenantId}/administration`}>
            Manage limits
          </Link>
        </div>
      </div>
      <ErrorNotice error={budget.error} />
      {budget.isPending ? (
        <Loading />
      ) : (
        budget.data && !budget.error && <BudgetSummary budget={budget.data} />
      )}
    </section>
  );
}
