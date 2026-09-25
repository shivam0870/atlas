import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useScopedQueryKey, useWorkspace } from "../app/context";
import { Badge, ErrorNotice, Loading, Modal, Notice } from "./ui";
export type Evidence = {
  id: string;
  document_id?: string;
  title: string;
  content: string;
  kind?: string;
  source_key?: string;
  start_offset?: number;
  end_offset?: number;
  version_id?: string;
  version_number?: number;
  version_created_at?: string;
  document_updated_at?: string;
  source_segments?: {
    start: number;
    end: number;
    page?: number;
    section?: string;
  }[];
  similarity?: number;
  score?: number;
  rerank_score?: number;
  vector_rank?: number | null;
  lexical_rank?: number | null;
  review_due_at?: string;
  historical?: boolean;
  publication_status?: string;
  effective_at?: string;
  possible_conflict?: boolean;
};
export function EvidenceDialog({
  source,
  onClose,
}: {
  source: Evidence | null;
  onClose: () => void;
}) {
  const { tenantId } = useWorkspace();
  const structured =
    source?.kind === "structured" || source?.id.startsWith("entity:");
  const preview = useQuery({
    queryKey: useScopedQueryKey("evidence", source?.id, source?.version_id),
    queryFn: ({ signal }) =>
      api<Record<string, unknown>>(
        structured
          ? `/inventory/${source!.id.replace("entity:", "")}`
          : `/chunks/${source!.id}`,
        { tenantId, signal },
      ),
    enabled: !!source,
    retry: false,
    staleTime: 0,
  });
  let content = "";
  let before = "";
  let passage = "";
  let after = "";
  if (preview.data && !structured) {
    content = String(
      preview.data.version_content ||
        preview.data.full_version_content ||
        preview.data.content ||
        "",
    );
    const chars = Array.from(content);
    const start = Number(
      preview.data.start_offset ?? source?.start_offset ?? 0,
    );
    const end = Number(
      preview.data.end_offset ?? source?.end_offset ?? chars.length,
    );
    if (preview.data.version_content || preview.data.full_version_content) {
      before = chars.slice(Math.max(0, start - 350), start).join("");
      passage = chars.slice(start, end).join("");
      after = chars.slice(end, end + 350).join("");
    } else passage = content;
  }
  const segments = (preview.data?.source_segments ||
    source?.source_segments ||
    []) as NonNullable<Evidence["source_segments"]>;
  const overlapping = segments.filter(
    (segment) =>
      segment.start <
        Number(preview.data?.end_offset ?? source?.end_offset ?? Infinity) &&
      segment.end >
        Number(preview.data?.start_offset ?? source?.start_offset ?? 0),
  );
  const pages = [
    ...new Set(overlapping.map((segment) => segment.page).filter(Boolean)),
  ];
  const sections = [
    ...new Set(overlapping.map((segment) => segment.section).filter(Boolean)),
  ];
  const versionDate =
    preview.data?.version_created_at || source?.version_created_at;
  const reviewDue = preview.data?.review_due_at ?? source?.review_due_at;
  return (
    <Modal
      open={!!source}
      onOpenChange={(v) => !v && onClose()}
      title={source?.title || "Source evidence"}
      description="Source access is checked again before this preview opens."
      wide
    >
      <ErrorNotice error={preview.error} />
      {preview.isPending || preview.isFetching ? (
        <Loading label="Checking source access…" />
      ) : (
        preview.data &&
        !preview.error && (
          <>
            <div className="row source-metadata">
              {structured ? (
                <Badge>Structured record</Badge>
              ) : (
                <>
                  <Badge>
                    Version{" "}
                    {String(
                      preview.data.version_number ||
                        preview.data.number ||
                        source?.version_number ||
                        "original",
                    )}
                  </Badge>
                  {!!pages.length && (
                    <Badge>
                      {pages.length === 1 ? "Page" : "Pages"} {pages.join(", ")}
                    </Badge>
                  )}
                  {sections.map((section) => (
                    <Badge key={section}>{section}</Badge>
                  ))}
                  {!!versionDate && (
                    <span className="muted text-small">
                      Version saved{" "}
                      {new Date(String(versionDate)).toLocaleString()}
                    </span>
                  )}
                  <span className="muted text-small">
                    Characters{" "}
                    {String(
                      preview.data.start_offset ?? source?.start_offset ?? 0,
                    )}
                    –
                    {String(
                      preview.data.end_offset ?? source?.end_offset ?? "",
                    )}
                  </span>
                </>
              )}
            </div>
            {(preview.data.historical ?? source?.historical) === true && (
              <Notice>
                This passage is from a historical version. Open the document to
                review its current published version before acting on it.
              </Notice>
            )}
            {source?.possible_conflict && (
              <Notice>
                Another retrieved passage gives a different value for a similar
                statement. Review both sources and their effective dates.
              </Notice>
            )}
            {structured ? (
              <pre className="source-text">
                {JSON.stringify(preview.data.item || preview.data, null, 2)}
              </pre>
            ) : (
              <div className="source-preview" data-testid="source-preview">
                <span>{before}</span>
                <mark>{passage}</mark>
                <span>{after}</span>
              </div>
            )}
            {!!reviewDue && new Date(String(reviewDue)) < new Date() && (
              <Notice>
                This document is past its scheduled review date. Confirm
                operational instructions with its owner.
              </Notice>
            )}
            {source?.document_id && (
              <Link
                className="text-link"
                to={`/o/${tenantId}/library/${source.document_id}`}
              >
                Open document and version history
              </Link>
            )}
          </>
        )
      )}
    </Modal>
  );
}
export function SourceCard({
  source,
  index,
  onClick,
}: {
  source: Evidence;
  index: number;
  onClick: () => void;
}) {
  return (
    <button className="evidence-card" onClick={onClick}>
      <div>
        <span className="source-number">{index + 1}</span>
        <strong>{source.title}</strong>
      </div>
      <p>
        {source.content.slice(0, 190)}
        {source.content.length > 190 ? "…" : ""}
      </p>
      <small>
        {source.kind === "structured"
          ? "Service record"
          : source.version_number
            ? `Version ${source.version_number}`
            : "Document passage"}
        {source.review_due_at && new Date(source.review_due_at) < new Date()
          ? " · Review due"
          : ""}
        {source.historical ? " · Historical version" : ""}
        {source.possible_conflict ? " · Possible source conflict" : ""}
      </small>
    </button>
  );
}
