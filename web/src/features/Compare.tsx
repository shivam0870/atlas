import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeftRight } from "lucide-react";
import { api, json } from "../api/client";
import { useWorkspace } from "../app/context";
import {
  Badge,
  Button,
  EmptyState,
  ErrorNotice,
  Loading,
  PageHeader,
} from "../components/ui";
import {
  formatDate,
  SelectField,
  useDocumentOptions,
  useWorkbenchActions,
  VersionSelect,
} from "./WorkbenchShared";
type ComparedSource = {
  document_id: string;
  version_id: string;
  title: string;
  number: number;
  publication_status: string;
  effective_at?: string;
};
export type Comparison = {
  left: ComparedSource;
  right: ComparedSource;
  changes: {
    kind: "insert" | "delete" | "replace";
    left_start: number;
    left_end: number;
    right_start: number;
    right_end: number;
    left_text: string;
    right_text: string;
  }[];
  summary: {
    added_lines: number;
    removed_lines: number;
    changed_sections: number;
  };
  warnings: string[];
  insights?: {
    kind: string;
    text: string;
    left_passage: string;
    right_passage: string;
    left_start: number;
    right_start: number;
    requires_review: boolean;
  }[];
  truncated: boolean;
};
export function ComparisonResult({ result }: { result: Comparison }) {
  const { tenantId } = useWorkspace();
  return (
    <section aria-label="Comparison results">
      <div className="workbench-summary">
        <strong>{result.summary.changed_sections} changed sections</strong>
        <span>{result.summary.added_lines} added lines</span>
        <span>{result.summary.removed_lines} removed lines</span>
      </div>
      {!!result.insights?.length && (
        <section className="panel" aria-label="Change review notes">
          <h2>Changes to review</h2>
          <p className="muted">
            These notes identify wording in the cited passages. Confirm its
            meaning and scope before updating a process.
          </p>
          {result.insights.map((insight, index) => (
            <details key={index}>
              <summary>
                {insight.kind.replaceAll("_", " ")} · {insight.text}
              </summary>
              <div className="workbench-columns">
                {(["left", "right"] as const).map((side) => (
                  <div key={side}>
                    <Link
                      to={`/o/${tenantId}/library/${result[side].document_id}?version=${result[side].version_id}`}
                    >
                      {side === "left" ? "Before" : "After"} ·{" "}
                      {result[side].title} · version {result[side].number} ·
                      line {insight[`${side}_start`]}
                    </Link>
                    <pre className="source-passage">
                      {insight[`${side}_passage`] || "No passage on this side"}
                    </pre>
                  </div>
                ))}
              </div>
            </details>
          ))}
        </section>
      )}
      {result.truncated && (
        <div className="notice" role="status">
          This comparison is truncated. Open the source versions to review the
          full documents.
        </div>
      )}
      {result.warnings.map((warning, index) => (
        <div className="notice" role="status" key={index}>
          {warning}
        </div>
      ))}
      <div className="workbench-columns">
        {([result.left, result.right] as const).map((source, index) => (
          <div className="panel" key={`${index}-${source.version_id}`}>
            <span className="eyebrow">
              {index === 0 ? "BEFORE / LEFT SOURCE" : "AFTER / RIGHT SOURCE"}
            </span>
            <h2>
              <Link
                to={`/o/${tenantId}/library/${source.document_id}?version=${source.version_id}`}
              >
                {source.title}
              </Link>
            </h2>
            <Badge>Version {source.number}</Badge>{" "}
            <Badge>{source.publication_status}</Badge>
            {source.effective_at && (
              <p className="muted">
                Effective {formatDate(source.effective_at)}
              </p>
            )}
          </div>
        ))}
      </div>
      <p className="muted">
        Changes below show exact source passages. Review their context before
        changing an operational process.
      </p>
      {result.changes.length === 0 ? (
        <EmptyState
          icon={<ArrowLeftRight />}
          title="No textual differences"
          description="The selected source versions contain the same text."
        />
      ) : (
        result.changes.map((change, index) => (
          <article className="panel comparison-change" key={index}>
            <h3>
              Change {index + 1} ·{" "}
              {change.kind === "replace"
                ? "Updated passage"
                : change.kind === "insert"
                  ? "Added passage"
                  : "Removed passage"}
            </h3>
            <div className="workbench-columns">
              <div>
                <p className="muted">
                  Before · lines {change.left_start}–
                  {Math.max(change.left_start, change.left_end)}
                </p>
                <pre className="source-passage diff-removed">
                  {change.left_text || "No previous passage"}
                </pre>
              </div>
              <div>
                <p className="muted">
                  After · lines {change.right_start}–
                  {Math.max(change.right_start, change.right_end)}
                </p>
                <pre className="source-passage diff-added">
                  {change.right_text || "Passage removed"}
                </pre>
              </div>
            </div>
          </article>
        ))
      )}
    </section>
  );
}
export function ComparePage() {
  const { tenantId } = useWorkspace();
  const actions = useWorkbenchActions();
  const documents = useDocumentOptions();
  const [left, setLeft] = useState("");
  const [right, setRight] = useState("");
  const [leftVersion, setLeftVersion] = useState("");
  const [rightVersion, setRightVersion] = useState("");
  const [result, setResult] = useState<Comparison | null>(null);
  return (
    <>
      <PageHeader
        title="Compare"
        description="Understand changes between document versions with exact source passages on both sides."
      />
      <ErrorNotice error={actions.error || documents.error} />
      {documents.isPending ? (
        <Loading />
      ) : (
        <form
          className="panel form-stack"
          onSubmit={(event) => {
            event.preventDefault();
            setResult(null);
            void actions.run(async (signal) => {
              const comparison = await api<Comparison>("/workbench/compare", {
                tenantId,
                method: "POST",
                signal,
                body: json({
                  left_document_id: left,
                  right_document_id: right,
                  left_version_id: leftVersion || null,
                  right_version_id: rightVersion || null,
                }),
              });
              if (!signal.aborted) setResult(comparison);
            });
          }}
        >
          <div className="workbench-columns">
            <div className="form-stack">
              <SelectField
                label="Left document"
                value={left}
                onChange={(value) => {
                  setLeft(value);
                  setLeftVersion("");
                  setResult(null);
                }}
                required
              >
                <option value="">Choose a document</option>
                {documents.data?.items.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title}
                  </option>
                ))}
              </SelectField>
              <VersionSelect
                label="Left version"
                documentId={left}
                value={leftVersion}
                onChange={(value) => {
                  setLeftVersion(value);
                  setResult(null);
                }}
              />
            </div>
            <div className="form-stack">
              <SelectField
                label="Right document"
                value={right}
                onChange={(value) => {
                  setRight(value);
                  setRightVersion("");
                  setResult(null);
                }}
                required
              >
                <option value="">Choose a document</option>
                {documents.data?.items.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title}
                  </option>
                ))}
              </SelectField>
              <VersionSelect
                label="Right version"
                documentId={right}
                value={rightVersion}
                onChange={(value) => {
                  setRightVersion(value);
                  setResult(null);
                }}
              />
            </div>
          </div>
          {documents.data &&
            documents.data.total > documents.data.items.length && (
              <p className="muted">
                Showing the 100 most recently updated documents.
              </p>
            )}
          <div>
            <Button busy={actions.busy} disabled={!left || !right}>
              <ArrowLeftRight size={16} />
              Compare sources
            </Button>
          </div>
        </form>
      )}
      {result && <ComparisonResult result={result} />}
      {!result && !actions.busy && !documents.isPending && (
        <EmptyState
          icon={<ArrowLeftRight />}
          title="See what changed"
          description="Choose two documents, or select the same document with two different versions. Atlas checks access to both sources before comparing them."
        />
      )}
    </>
  );
}
