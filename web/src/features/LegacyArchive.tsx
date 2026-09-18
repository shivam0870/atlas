import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useWorkspace, useScopedQueryKey } from "../app/context";
import {
  Badge,
  Button,
  EmptyState,
  ErrorNotice,
  Loading,
  Notice,
} from "../components/ui";
import {
  EvidenceDialog,
  SourceCard,
  type Evidence,
} from "../components/Evidence";
type LegacyQuery = {
  id: string;
  question: string;
  answer: string;
  status: string;
  created_at: string;
  sources: Evidence[];
};
export function LegacyArchive() {
  const { tenantId, canManage } = useWorkspace();
  const [page, setPage] = useState(1);
  const [source, setSource] = useState<Evidence | null>(null);
  const archive = useQuery({
    queryKey: useScopedQueryKey("legacy-queries", page),
    queryFn: ({ signal }) =>
      api<{ items: LegacyQuery[]; notice: string; total: number }>(
        `/legacy-queries?limit=25&offset=${(page - 1) * 25}`,
        { tenantId, signal },
      ),
    enabled: canManage,
    staleTime: 0,
    refetchOnWindowFocus: true,
  });
  if (!canManage) return null;
  return (
    <section className="panel">
      <h2>Legacy workspace queries</h2>
      <p className="muted">
        These records predate individual accounts. Their human author is
        unknown; they are not assigned to any member. Current private
        conversations remain private.
      </p>
      <ErrorNotice error={archive.error} />
      {archive.isPending || archive.isFetching ? (
        <Loading label="Checking archive access…" />
      ) : (
        !archive.error &&
        archive.data && (
          <>
            <Notice>{archive.data.notice}</Notice>
            {!archive.data.items.length ? (
              <EmptyState
                title="No legacy queries"
                description="New conversations belong to individual members and do not appear in this archive."
              />
            ) : (
              <div className="stack">
                {archive.data.items.map((item) => (
                  <details key={item.id} className="panel">
                    <summary>{item.question}</summary>
                    <div className="row">
                      <Badge>{item.status}</Badge>
                      <span className="muted text-small">
                        {new Date(item.created_at).toLocaleString()} · Author
                        unknown
                      </span>
                    </div>
                    <p style={{ whiteSpace: "pre-wrap" }}>
                      {item.answer || "No answer was recorded."}
                    </p>
                    {!!item.sources?.length && (
                      <div className="card-grid">
                        {item.sources.map((evidence, index) => (
                          <SourceCard
                            key={evidence.id}
                            source={evidence}
                            index={index}
                            onClick={() => setSource(evidence)}
                          />
                        ))}
                      </div>
                    )}
                  </details>
                ))}
              </div>
            )}
            <div className="pagination">
              <span>{archive.data.total} records</span>
              <Button
                variant="ghost"
                disabled={page === 1}
                onClick={() => setPage((p) => p - 1)}
              >
                Previous
              </Button>
              <span>Page {page}</span>
              <Button
                variant="ghost"
                disabled={page * 25 >= archive.data.total}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </Button>
            </div>
          </>
        )
      )}
      <EvidenceDialog source={source} onClose={() => setSource(null)} />
    </section>
  );
}
