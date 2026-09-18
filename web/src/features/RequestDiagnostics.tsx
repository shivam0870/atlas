import { useState } from "react";
import { Button, Modal, Badge } from "../components/ui";
import type { Evidence } from "../components/Evidence";
type DiagnosticMessage = {
  id: string;
  status: string;
  sources: Evidence[];
  metadata: Record<string, unknown>;
};
const measured = (value: unknown, unit = "") =>
  typeof value === "number"
    ? `${value.toLocaleString(undefined, { maximumFractionDigits: 3 })}${unit}`
    : "Not recorded";
export function RequestDiagnostics({
  message,
}: {
  message: DiagnosticMessage;
}) {
  const [open, setOpen] = useState(false);
  const metadata = message.metadata;
  const steps = Array.isArray(metadata.steps)
    ? (metadata.steps as {
        tool?: string;
        stage?: string;
        error?: string;
        message?: string;
      }[])
    : [];
  return (
    <>
      <Button variant="ghost" onClick={() => setOpen(true)}>
        Request diagnostics
      </Button>
      <Modal
        open={open}
        onOpenChange={setOpen}
        title="Request diagnostics"
        description="Measurements and retrieval order for your own answer. Source details remain subject to current access."
        side
      >
        <div className="row">
          <Badge>{message.status}</Badge>
          <span className="muted text-small">Message {message.id}</span>
        </div>
        <dl className="key-values">
          <div>
            <dt>Mode</dt>
            <dd>{String(metadata.mode || "Not recorded")}</dd>
          </div>
          <div>
            <dt>Total duration</dt>
            <dd>{measured(metadata.duration_ms, " ms")}</dd>
          </div>
          <div>
            <dt>First token</dt>
            <dd>{measured(metadata.first_token_ms, " ms")}</dd>
          </div>
          <div>
            <dt>Input / output tokens</dt>
            <dd>
              {measured(metadata.input_tokens)} /{" "}
              {measured(metadata.output_tokens)}
            </dd>
          </div>
          <div>
            <dt>Cache reuse</dt>
            <dd>
              {typeof metadata.cache_hit === "boolean"
                ? metadata.cache_hit
                  ? "Exact cache"
                  : "No"
                : "Not recorded"}
            </dd>
          </div>
          <div>
            <dt>Failure stage</dt>
            <dd>
              {typeof metadata.failure_stage === "string" &&
              metadata.failure_stage
                ? metadata.failure_stage
                : ["failed", "cancelled"].includes(message.status)
                  ? "Not recorded for this attempt"
                  : "No failure recorded"}
            </dd>
          </div>
          {typeof metadata.error_code === "string" && (
            <div>
              <dt>Error code</dt>
              <dd>{metadata.error_code}</dd>
            </div>
          )}
        </dl>
        <h3>Retrieval ranks</h3>
        <p className="muted text-small">
          Final rank is the returned evidence order. Lexical and vector ranks
          describe the candidate lists; fusion and reranking scores use
          different scales.
        </p>
        {message.sources.length ? (
          <div
            className="table-wrap"
            role="region"
            tabIndex={0}
            aria-label="Retrieval ranking table"
          >
            <table className="data-table">
              <thead>
                <tr>
                  <th>Final</th>
                  <th>Source</th>
                  <th>Vector rank</th>
                  <th>Lexical rank</th>
                  <th>Fusion score</th>
                  <th>Reranker score</th>
                </tr>
              </thead>
              <tbody>
                {message.sources.map((source, index) => (
                  <tr key={source.id}>
                    <td>{index + 1}</td>
                    <td>
                      {source.title}
                      {source.version_number && (
                        <small>Version {source.version_number}</small>
                      )}
                    </td>
                    <td>{measured(source.vector_rank)}</td>
                    <td>{measured(source.lexical_rank)}</td>
                    <td>{measured(source.score)}</td>
                    <td>{measured(source.rerank_score)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">
            No source ranks were recorded for this attempt.
          </p>
        )}
        <h3>Execution stages</h3>
        {steps.length ? (
          <ol>
            {steps.map((step, index) => (
              <li key={index}>
                <strong>{step.tool || step.stage || "Model decision"}</strong>
                {step.error ? <span> · Failed: {step.error}</span> : null}
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted">
            No tool execution stages were recorded. The request may have used
            direct retrieval or a cached answer.
          </p>
        )}
        <p className="muted text-small">
          These are execution measurements, not hidden model reasoning.
        </p>
      </Modal>
    </>
  );
}
