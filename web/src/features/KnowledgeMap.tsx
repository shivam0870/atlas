import { useState } from "react";
import { Link } from "react-router-dom";
import { Network, Plus } from "lucide-react";
import { api, json } from "../api/client";
import { useWorkspace } from "../app/context";
import {
  Badge,
  Button,
  EmptyState,
  ErrorNotice,
  Field,
  Loading,
  Modal,
  Notice,
  PageHeader,
} from "../components/ui";
import {
  SelectField,
  useWorkbenchActions,
  useWorkbenchQuery,
} from "./WorkbenchShared";
type Node = {
  id: string;
  resource_id: string;
  kind: string;
  label: string;
  space_id?: string;
};
type Edge = {
  id: string;
  source: string;
  target: string;
  label: string;
  editable: boolean;
};
type KnowledgeGraph = { nodes: Node[]; edges: Edge[] };
export function KnowledgeMapPage() {
  const { tenantId, canEdit } = useWorkspace();
  const graph = useWorkbenchQuery<KnowledgeGraph>(
    "knowledge-map",
    "/workbench/knowledge-map",
  );
  const actions = useWorkbenchActions();
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [selected, setSelected] = useState("");
  const [open, setOpen] = useState(false);
  const [source, setSource] = useState("");
  const [target, setTarget] = useState("");
  const [label, setLabel] = useState("");
  const [remove, setRemove] = useState<Edge | null>(null);
  const nodes = graph.data?.nodes || [];
  const visible = nodes.filter(
    (node) =>
      (!kind || node.kind === kind) &&
      node.label.toLowerCase().includes(query.toLowerCase()),
  );
  const drawn = visible.slice(0, 24);
  const positions = new Map(
    drawn.map((node, index) => {
      const angle = (2 * Math.PI * index) / drawn.length - Math.PI / 2;
      return [
        node.id,
        { x: 400 + 310 * Math.cos(angle), y: 225 + 170 * Math.sin(angle) },
      ];
    }),
  );
  const selectedNode = nodes.find((node) => node.id === selected);
  const related =
    graph.data?.edges.filter(
      (edge) => edge.source === selected || edge.target === selected,
    ) || [];
  const linkable = nodes.filter((node) =>
    ["document", "entity"].includes(node.kind),
  );
  const visibleIds = new Set(visible.map((node) => node.id));
  const filteredEdges =
    graph.data?.edges.filter(
      (edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target),
    ) || [];
  const findLabel = (id: string) =>
    nodes.find((node) => node.id === id)?.label || "Unavailable";
  function nodeUrl(node: Node) {
    if (node.kind === "document")
      return `/o/${tenantId}/library/${node.resource_id}`;
    if (node.kind === "entity")
      return `/o/${tenantId}/inventory?record=${node.resource_id}`;
    if (node.kind === "space")
      return `/o/${tenantId}/library?space=${node.resource_id}`;
    return `/o/${tenantId}/people`;
  }
  return (
    <>
      <PageHeader
        title="Knowledge Map"
        description="Explore the documented relationships between services, teams, knowledge spaces, and source documents."
        action={
          canEdit && (
            <Button
              onClick={() => {
                setOpen(true);
                actions.setError(null);
              }}
            >
              <Plus size={16} />
              Add relationship
            </Button>
          )
        }
      />
      <ErrorNotice error={graph.error || actions.error} />
      {actions.message && <Notice>{actions.message}</Notice>}
      {graph.isPending ? (
        <Loading />
      ) : nodes.length === 0 ? (
        <EmptyState
          icon={<Network />}
          title="Your knowledge, connected"
          description="Add documents to Library and services to Inventory to begin exploring their relationships."
        />
      ) : (
        <>
          <div className="workbench-filters">
            <Field
              label="Find a node"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search by name"
            />
            <SelectField label="Node type" value={kind} onChange={setKind}>
              <option value="">All types</option>
              {Array.from(new Set(nodes.map((node) => node.kind)))
                .sort()
                .map((value) => (
                  <option key={value}>{value}</option>
                ))}
            </SelectField>
          </div>
          <section className="panel">
            <div className="row between">
              <h2>Relationship map</h2>
              <span className="muted">
                {visible.length} nodes · {filteredEdges.length} connections
              </span>
            </div>
            {drawn.length === 0 ? (
              <p className="muted">
                No matching nodes. Try another name or type.
              </p>
            ) : (
              <svg
                className="knowledge-graph"
                viewBox="0 0 800 450"
                role="group"
                aria-label="Knowledge relationship graph"
              >
                {graph.data?.edges.map((edge) => {
                  const a = positions.get(edge.source);
                  const b = positions.get(edge.target);
                  return a && b ? (
                    <line
                      key={edge.id}
                      x1={a.x}
                      y1={a.y}
                      x2={b.x}
                      y2={b.y}
                      className={
                        selected === edge.source || selected === edge.target
                          ? "selected"
                          : ""
                      }
                    >
                      <title>
                        {findLabel(edge.source)} {edge.label}{" "}
                        {findLabel(edge.target)}
                      </title>
                    </line>
                  ) : null;
                })}
                {drawn.map((node) => {
                  const point = positions.get(node.id)!;
                  return (
                    <g
                      key={node.id}
                      tabIndex={0}
                      role="button"
                      aria-label={`Inspect ${node.label}`}
                      aria-pressed={selected === node.id}
                      className={`graph-node ${selected === node.id ? "selected" : ""}`}
                      transform={`translate(${point.x} ${point.y})`}
                      onClick={() => setSelected(node.id)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          setSelected(node.id);
                        }
                      }}
                    >
                      <circle r={20} />
                      <text textAnchor="middle" y={5} className="graph-symbol">
                        {node.kind.slice(0, 1).toUpperCase()}
                      </text>
                      <text textAnchor="middle" y={38}>
                        {node.label.length > 22
                          ? `${node.label.slice(0, 21)}…`
                          : node.label}
                      </text>
                      <title>
                        {node.label} · {node.kind}
                      </title>
                    </g>
                  );
                })}
              </svg>
            )}
            {visible.length > drawn.length && (
              <p className="muted">
                The graph shows the first {drawn.length} matching nodes. Use
                filters or the full list below to explore the rest.
              </p>
            )}
          </section>
          {selectedNode && (
            <section className="panel" aria-label="Selected node">
              <div className="row between">
                <div>
                  <Badge>{selectedNode.kind}</Badge>
                  <h2>{selectedNode.label}</h2>
                </div>
                <Link className="button secondary" to={nodeUrl(selectedNode)}>
                  Open{" "}
                  {selectedNode.kind === "entity"
                    ? "inventory record"
                    : selectedNode.kind}
                </Link>
              </div>
              {related.length === 0 ? (
                <p className="muted">
                  No recorded relationships for this node.
                </p>
              ) : (
                <ul className="relationship-list">
                  {related.map((edge) => (
                    <li key={edge.id}>
                      <span>
                        {findLabel(edge.source)} <strong>{edge.label}</strong>{" "}
                        {findLabel(edge.target)}
                      </span>
                      {edge.editable && canEdit && (
                        <Button variant="ghost" onClick={() => setRemove(edge)}>
                          Remove relationship
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}
          <section className="panel">
            <h2>Explore all nodes</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Type</th>
                    <th>Connections</th>
                    <th>Details</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((node) => (
                    <tr key={node.id}>
                      <td>
                        <Link to={nodeUrl(node)}>{node.label}</Link>
                      </td>
                      <td>
                        <Badge>{node.kind}</Badge>
                      </td>
                      <td>
                        {
                          graph.data?.edges.filter(
                            (edge) =>
                              edge.source === node.id ||
                              edge.target === node.id,
                          ).length
                        }
                      </td>
                      <td>
                        <Button
                          variant="ghost"
                          onClick={() => setSelected(node.id)}
                          aria-label={`Show relationships for ${node.label}`}
                        >
                          Inspect
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
      <Modal
        open={open}
        onOpenChange={setOpen}
        title="Add a relationship"
        description="Record an explicit relationship between documents or inventory records you can access."
      >
        <form
          className="form-stack"
          onSubmit={(event) => {
            event.preventDefault();
            const a = nodes.find((node) => node.id === source);
            const b = nodes.find((node) => node.id === target);
            if (!a || !b) return;
            void actions.run(async (signal) => {
              await api("/workbench/knowledge-map/relationships", {
                tenantId,
                method: "POST",
                signal,
                body: json({
                  source_kind: a.kind,
                  source_id: a.resource_id,
                  target_kind: b.kind,
                  target_id: b.resource_id,
                  label,
                }),
              });
              setOpen(false);
              setLabel("");
              actions.setMessage("Relationship added.");
            });
          }}
        >
          <ErrorNotice error={actions.error} />
          <SelectField
            label="From"
            value={source}
            onChange={setSource}
            required
          >
            <option value="">Choose a document or service</option>
            {linkable.map((node) => (
              <option key={node.id} value={node.id}>
                {node.label} · {node.kind}
              </option>
            ))}
          </SelectField>
          <Field
            label="Relationship"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder="depends on, documents, owned by…"
            required
            maxLength={120}
          />
          <SelectField label="To" value={target} onChange={setTarget} required>
            <option value="">Choose a document or service</option>
            {linkable
              .filter((node) => node.id !== source)
              .map((node) => (
                <option key={node.id} value={node.id}>
                  {node.label} · {node.kind}
                </option>
              ))}
          </SelectField>
          <Button
            busy={actions.busy}
            disabled={!source || !target || source === target}
          >
            Save relationship
          </Button>
        </form>
      </Modal>
      <Modal
        open={!!remove}
        onOpenChange={(open) => !open && setRemove(null)}
        title="Remove relationship"
        description="The linked documents and inventory records will remain available."
      >
        <ErrorNotice error={actions.error} />
        <Button
          variant="danger"
          busy={actions.busy}
          onClick={() =>
            actions.run(async (signal) => {
              await api(
                `/workbench/knowledge-map/relationships/${remove?.id}`,
                { tenantId, method: "DELETE", signal },
              );
              setRemove(null);
            })
          }
        >
          Remove relationship
        </Button>
      </Modal>
    </>
  );
}
