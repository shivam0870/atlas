import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  ArrowRight,
  Check,
  ClipboardList,
  Download,
  KeyRound,
  Plus,
  RefreshCw,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react";
import { api, json } from "../api/client";
import { useScopedQueryKey, useWorkspace } from "../app/context";
import { LegacyArchive } from "./LegacyArchive";
import { MonthlyQuota } from "./WorkspaceMetrics";
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
type Space = { id: string; name: string };
type DocumentSummary = { id: string; title: string; space_id: string };
type Inventory = {
  id: string;
  name: string;
  kind: string;
  space_id: string;
  attributes: Record<string, unknown>;
  document_ids: string[];
  archived: boolean;
  status?: string;
  updated_at?: string;
};
type PreviewRow = {
  row: number;
  valid: boolean;
  data: Record<string, unknown>;
  errors: string[];
};
const text = (value: unknown) =>
  value == null
    ? "—"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
const date = (value?: string) =>
  value ? new Date(value).toLocaleString() : "—";
const number = (value: unknown) =>
  Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: 2 });
function Forbidden() {
  return (
    <EmptyState
      icon={<ShieldCheck />}
      title="Management access required"
      description="An owner or administrator can manage this part of the company."
    />
  );
}
function useActions() {
  const { tenantId, user } = useWorkspace();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [message, setMessage] = useState("");
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setMessage("");
    try {
      await action();
      await client.invalidateQueries({
        queryKey: ["workspace", user.id, tenantId],
      });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, setError, message, setMessage, run };
}
function SpaceSelect({
  spaces,
  value,
  onChange,
  label = "Knowledge space",
  required = true,
}: {
  spaces: Space[];
  value: string;
  onChange: (v: string) => void;
  label?: string;
  required?: boolean;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required={required}
      >
        <option value="">
          {required ? "Choose a space" : "All permitted spaces"}
        </option>
        {spaces.map((s) => (
          <option value={s.id} key={s.id}>
            {s.name}
          </option>
        ))}
      </select>
    </label>
  );
}
export function InventoryPage() {
  const { tenantId, canEdit } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const { busy, error, setError, message, setMessage, run } = useActions();
  const [q, setQ] = useState("");
  const [space, setSpace] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string[]>([]);
  const [edit, setEdit] = useState<Inventory | { id?: string } | null>(null);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("service");
  const [owner, setOwner] = useState("");
  const [environment, setEnvironment] = useState("production");
  const [replicas, setReplicas] = useState("1");
  const [status, setStatus] = useState("active");
  const [description, setDescription] = useState("");
  const [editSpace, setEditSpace] = useState("");
  const [documentIds, setDocumentIds] = useState<string[]>([]);
  const [advanced, setAdvanced] = useState("{}");
  const [archive, setArchive] = useState<Inventory | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [csv, setCsv] = useState("");
  const [importSpace, setImportSpace] = useState("");
  const [preview, setPreview] = useState<{
    rows: PreviewRow[];
    valid_count: number;
  } | null>(null);
  const [comparison, setComparison] = useState<{
    items: Inventory[];
    fields: string[];
  } | null>(null);
  const spaces = useQuery({
    queryKey: useScopedQueryKey("spaces"),
    queryFn: ({ signal }) => api<Space[]>("/spaces", { tenantId, signal }),
  });
  const query = new URLSearchParams({
    q,
    page: String(page),
    page_size: "20",
    ...(space ? { space_id: space } : {}),
    ...(includeArchived ? { include_archived: "true" } : {}),
  });
  const inventory = useQuery({
    queryKey: useScopedQueryKey("inventory", query.toString()),
    queryFn: ({ signal }) =>
      api<{ items: Inventory[]; total: number }>(`/inventory?${query}`, {
        tenantId,
        signal,
      }),
  });
  const documents = useQuery({
    queryKey: useScopedQueryKey("inventory-documents", editSpace),
    queryFn: ({ signal }) =>
      api<{ items: DocumentSummary[] }>(
        "/library?" +
          new URLSearchParams({
            space_id: editSpace,
            page_size: "100",
            lifecycle: "active",
          }),
        { tenantId, signal },
      ),
    enabled: !!edit && !!editSpace,
  });
  const detailId = params.get("record");
  const detail = useQuery({
    queryKey: useScopedQueryKey("inventory-detail", detailId),
    queryFn: ({ signal }) =>
      api<{ item: Inventory }>(`/inventory/${detailId}`, { tenantId, signal }),
    enabled: !!detailId,
  });
  function openEdit(item?: Inventory) {
    setName(item?.name || "");
    setKind(item?.kind || "service");
    setOwner(
      text(item?.attributes.owner) === "—"
        ? ""
        : String(item?.attributes.owner || ""),
    );
    setEnvironment(String(item?.attributes.environment || "production"));
    setReplicas(String(item?.attributes.replicas ?? 1));
    setStatus(String(item?.attributes.status || "active"));
    setDescription(String(item?.attributes.description || ""));
    setEditSpace(item?.space_id || spaces.data?.[0]?.id || "");
    setDocumentIds(item?.document_ids || []);
    const rest = { ...item?.attributes };
    for (const key of [
      "owner",
      "environment",
      "replicas",
      "status",
      "description",
    ])
      delete rest[key];
    setAdvanced(JSON.stringify(rest, null, 2));
    setEdit(item || {});
    setError(null);
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      const extra = JSON.parse(advanced);
      if (!extra || Array.isArray(extra) || typeof extra !== "object")
        throw new Error("Advanced attributes must be a JSON object.");
      await api(edit?.id ? `/inventory/${edit.id}` : "/inventory", {
        tenantId,
        method: edit?.id ? "PATCH" : "POST",
        body: json({
          name,
          kind,
          space_id: editSpace,
          document_ids: documentIds,
          attributes: {
            ...extra,
            owner,
            environment,
            replicas: Number(replicas),
            status,
            description,
          },
        }),
      });
      setEdit(null);
      setMessage("Inventory record saved.");
    });
  }
  return (
    <>
      <PageHeader
        title="Service inventory"
        description="The structured details behind your team’s everyday questions."
        action={
          canEdit ? (
            <>
              <Button
                variant="secondary"
                onClick={() => {
                  setImportOpen(true);
                  setError(null);
                  setImportSpace(spaces.data?.[0]?.id || "");
                }}
              >
                <Upload size={16} />
                Import CSV
              </Button>
              <Button onClick={() => openEdit()}>
                <Plus size={16} />
                Add record
              </Button>
            </>
          ) : undefined
        }
      />
      <ErrorNotice
        error={
          !edit && !archive && !importOpen ? error || inventory.error : null
        }
      />
      {message && <Notice>{message}</Notice>}
      <div className="toolbar">
        <input
          aria-label="Search inventory"
          placeholder="Search services and deployments…"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setPage(1);
          }}
        />
        <select
          aria-label="Filter inventory space"
          value={space}
          onChange={(e) => {
            setSpace(e.target.value);
            setPage(1);
          }}
        >
          <option value="">All permitted spaces</option>
          {spaces.data?.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={includeArchived}
            onChange={(e) => setIncludeArchived(e.target.checked)}
          />
          Include archived
        </label>
        {selected.length > 1 && (
          <Button
            variant="secondary"
            busy={busy}
            onClick={() =>
              run(async () =>
                setComparison(
                  await api("/inventory/compare", {
                    tenantId,
                    method: "POST",
                    body: json({ ids: selected }),
                  }),
                ),
              )
            }
          >
            Compare {selected.length} records
          </Button>
        )}
      </div>
      {inventory.isPending ? (
        <Loading />
      ) : !inventory.data?.items.length ? (
        <EmptyState
          title="Your service context belongs here"
          description="Add owners, environments and deployment details. Atlas can combine these records with your documents."
          action={
            canEdit ? (
              <Button onClick={() => openEdit()}>Add your first record</Button>
            ) : undefined
          }
        />
      ) : (
        <section className="panel">
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>
                    <span className="sr-only">Compare</span>
                  </th>
                  <th>Record</th>
                  <th>Owner</th>
                  <th>Environment</th>
                  <th>Replicas</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {inventory.data.items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Compare ${item.name}`}
                        checked={selected.includes(item.id)}
                        onChange={(e) =>
                          setSelected(
                            e.target.checked
                              ? [...selected, item.id]
                              : selected.filter((id) => id !== item.id),
                          )
                        }
                      />
                    </td>
                    <td>
                      <button
                        className="link-button"
                        onClick={() => setParams({ record: item.id })}
                      >
                        {item.name}
                      </button>
                      <small>{item.kind}</small>
                    </td>
                    <td>{text(item.attributes.owner)}</td>
                    <td>{text(item.attributes.environment)}</td>
                    <td>{text(item.attributes.replicas)}</td>
                    <td>
                      <Badge>
                        {item.archived
                          ? "archived"
                          : text(item.attributes.status)}
                      </Badge>
                    </td>
                    <td>
                      {canEdit && (
                        <div className="row">
                          <Button
                            variant="ghost"
                            onClick={() => openEdit(item)}
                          >
                            Edit
                          </Button>
                          {item.archived && (
                            <Button
                              variant="ghost"
                              disabled={busy}
                              onClick={() =>
                                run(async () => {
                                  await api(`/inventory/${item.id}`, {
                                    tenantId,
                                    method: "PATCH",
                                    body: json({ archived: false }),
                                  });
                                  setMessage("Record restored.");
                                })
                              }
                            >
                              Restore
                            </Button>
                          )}
                          {!item.archived && (
                            <Button
                              variant="ghost"
                              aria-label={`Archive ${item.name}`}
                              onClick={() => setArchive(item)}
                            >
                              <Archive size={15} />
                            </Button>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pagination">
            <span>{inventory.data.total} records</span>
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
              disabled={page * 20 >= inventory.data.total}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        </section>
      )}
      <Modal
        open={!!edit}
        onOpenChange={(v) => !v && setEdit(null)}
        title={edit?.id ? "Edit inventory record" : "Add inventory record"}
        description="Records inherit access from their knowledge space."
        wide
      >
        <form onSubmit={save} className="form-stack">
          <ErrorNotice error={error} />
          <div className="grid-2">
            <Field
              label="Record name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
            <label className="field">
              <span>Record type</span>
              <select
                value={kind}
                disabled={!!edit?.id}
                onChange={(e) => setKind(e.target.value)}
              >
                <option value="service">Service</option>
                <option value="deployment">Deployment</option>
              </select>
            </label>
            <SpaceSelect
              spaces={spaces.data || []}
              value={editSpace}
              onChange={(value) => {
                setEditSpace(value);
                setDocumentIds([]);
              }}
            />
            <Field
              label="Owner team"
              value={owner}
              onChange={(e) => setOwner(e.target.value)}
              required
            />
            <Field
              label="Environment"
              value={environment}
              onChange={(e) => setEnvironment(e.target.value)}
              required
            />
            <Field
              label="Replicas"
              type="number"
              min={0}
              max={100000}
              value={replicas}
              onChange={(e) => setReplicas(e.target.value)}
              required
            />
            <label className="field">
              <span>Status</span>
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
              >
                <option value="active">Active</option>
                <option value="degraded">Degraded</option>
                <option value="maintenance">Maintenance</option>
                <option value="retired">Retired</option>
              </select>
            </label>
          </div>
          <label className="field">
            <span>Description</span>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>
          <details>
            <summary>Supporting documents</summary>
            <div className="member-checklist">
              {documents.data?.items.map((d) => (
                <label className="checkbox-row" key={d.id}>
                  <input
                    type="checkbox"
                    checked={documentIds.includes(d.id)}
                    onChange={(e) =>
                      setDocumentIds(
                        e.target.checked
                          ? [...documentIds, d.id]
                          : documentIds.filter((id) => id !== d.id),
                      )
                    }
                  />
                  {d.title}
                </label>
              ))}
              {!documents.data?.items.length && (
                <p className="muted">No documents in this space yet.</p>
              )}
            </div>
          </details>
          <details>
            <summary>Advanced attributes</summary>
            <label className="field">
              <span>Additional JSON attributes</span>
              <textarea
                className="code-input"
                value={advanced}
                onChange={(e) => setAdvanced(e.target.value)}
              />
            </label>
          </details>
          <Button busy={busy}>Save record</Button>
        </form>
      </Modal>
      <Modal
        open={!!archive}
        onOpenChange={(v) => !v && setArchive(null)}
        title="Archive this record?"
        description="Archived records are excluded from active inventory tools."
      >
        <ErrorNotice error={error} />
        <Button
          variant="danger"
          busy={busy}
          onClick={() =>
            run(async () => {
              await api(`/inventory/${archive?.id}`, {
                tenantId,
                method: "DELETE",
              });
              setArchive(null);
              setSelected(selected.filter((id) => id !== archive?.id));
              setMessage("Record archived.");
            })
          }
        >
          Archive record
        </Button>
      </Modal>
      <Modal
        open={!!detailId}
        onOpenChange={(v) => !v && setParams({})}
        title={detail.data?.item.name || "Inventory record"}
        description="Current structured details and supporting knowledge."
        wide
      >
        <ErrorNotice error={detail.error} />
        {detail.isPending ? (
          <Loading />
        ) : (
          detail.data && (
            <>
              <KeyValues values={detail.data.item.attributes} />
              <h3>Supporting documents</h3>
              {detail.data.item.document_ids?.length ? (
                detail.data.item.document_ids.map((id) => (
                  <Link
                    className="block-link"
                    key={id}
                    to={`/o/${tenantId}/library/${id}`}
                  >
                    Open supporting document
                    <ArrowRight size={15} />
                  </Link>
                ))
              ) : (
                <p className="muted">No supporting documents linked.</p>
              )}
              {canEdit && (
                <Button
                  variant="secondary"
                  onClick={() => {
                    openEdit(detail.data.item);
                    setParams({});
                  }}
                >
                  Edit record
                </Button>
              )}
            </>
          )
        )}
      </Modal>
      <Modal
        open={importOpen}
        onOpenChange={setImportOpen}
        title="Import inventory from CSV"
        description="Preview and validate every row before importing. Columns: name, kind, owner, environment, replicas, status, description."
        wide
      >
        <div className="form-stack">
          <ErrorNotice error={error} />
          <SpaceSelect
            spaces={spaces.data || []}
            value={importSpace}
            onChange={(v) => {
              setImportSpace(v);
              setPreview(null);
            }}
          />
          <Field
            label="CSV file"
            type="file"
            accept=".csv,text/csv"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (file) {
                setCsv(await file.text());
                setPreview(null);
              }
            }}
          />
          <label className="field">
            <span>CSV contents</span>
            <textarea
              className="code-input"
              value={csv}
              onChange={(e) => {
                setCsv(e.target.value);
                setPreview(null);
              }}
              placeholder="name,kind,owner,environment,replicas,status,description"
            />
          </label>
          <Button
            variant="secondary"
            busy={busy}
            disabled={!csv.trim() || !importSpace}
            onClick={() =>
              run(async () =>
                setPreview(
                  await api("/inventory/import/preview", {
                    tenantId,
                    method: "POST",
                    body: json({ csv, space_id: importSpace }),
                  }),
                ),
              )
            }
          >
            Validate preview
          </Button>
          {preview && (
            <>
              <p>
                {preview.valid_count} valid rows of {preview.rows.length}
              </p>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Row</th>
                      <th>Record</th>
                      <th>Validation</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.rows.map((row) => (
                      <tr key={row.row}>
                        <td>{row.row}</td>
                        <td>{text(row.data.name)}</td>
                        <td>
                          {row.valid ? (
                            <Badge tone="success">Ready</Badge>
                          ) : (
                            row.errors.join("; ")
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Button
                busy={busy}
                disabled={
                  !preview.valid_count || preview.rows.some((row) => !row.valid)
                }
                onClick={() =>
                  run(async () => {
                    const result = await api<{ imported: number }>(
                      "/inventory/import",
                      {
                        tenantId,
                        method: "POST",
                        body: json({ csv, space_id: importSpace }),
                      },
                    );
                    setImportOpen(false);
                    setPreview(null);
                    setCsv("");
                    setMessage(`Imported ${result.imported} records.`);
                  })
                }
              >
                Import {preview.valid_count} records
              </Button>
            </>
          )}
        </div>
      </Modal>
      <Modal
        open={!!comparison}
        onOpenChange={(v) => !v && setComparison(null)}
        title="Compare inventory"
        description="Only records you can access are included."
        wide
      >
        {comparison && (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Attribute</th>
                  {comparison.items.map((i) => (
                    <th key={i.id}>{i.name}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {comparison.fields.map((field) => (
                  <tr key={field}>
                    <th>{field.replaceAll("_", " ")}</th>
                    {comparison.items.map((i) => (
                      <td key={i.id}>
                        {text(
                          field === "name"
                            ? i.name
                            : field === "kind"
                              ? i.kind
                              : i.attributes[field],
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Modal>
    </>
  );
}
function KeyValues({ values }: { values: Record<string, unknown> }) {
  return (
    <dl className="key-values">
      {Object.entries(values).map(([key, value]) => (
        <div key={key}>
          <dt>{key.replaceAll("_", " ")}</dt>
          <dd>{text(value)}</dd>
        </div>
      ))}
    </dl>
  );
}
type ServiceKey = {
  id: string;
  label?: string;
  scopes: string[];
  expires_at?: string;
  revoked_at?: string;
  last_used_at?: string;
  created_at?: string;
};
type Integration = {
  id: string;
  name: string;
  active: boolean;
  grants: { space_id: string; permission: "read" | "write" }[];
  keys: ServiceKey[];
};
export function IntegrationsPage() {
  const { tenantId, canManage } = useWorkspace();
  const { busy, error, setError, message, setMessage, run } = useActions();
  const services = useQuery({
    queryKey: useScopedQueryKey("integrations"),
    queryFn: ({ signal }) =>
      api<{ items: Integration[] }>("/integrations", { tenantId, signal }),
    enabled: canManage,
  });
  const spaces = useQuery({
    queryKey: useScopedQueryKey("spaces"),
    queryFn: ({ signal }) => api<Space[]>("/spaces", { tenantId, signal }),
    enabled: canManage,
  });
  const [edit, setEdit] = useState<Integration | { id?: string } | null>(null);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState(["read", "query"]);
  const [days, setDays] = useState(30);
  const [grants, setGrants] = useState<Integration["grants"]>([]);
  const [secret, setSecret] = useState("");
  const [copied, setCopied] = useState(false);
  const [rotate, setRotate] = useState<Integration | null>(null);
  const [revoke, setRevoke] = useState<{
    service: Integration;
    key: ServiceKey;
  } | null>(null);
  const [toggle, setToggle] = useState<Integration | null>(null);
  function open(item?: Integration) {
    setName(item?.name || "");
    setGrants(item?.grants || []);
    setScopes(["read", "query"]);
    setDays(30);
    setEdit(item || {});
    setError(null);
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      if (edit?.id)
        await api(`/integrations/${edit.id}`, {
          tenantId,
          method: "PATCH",
          body: json({ name, grants }),
        });
      else {
        const result = await api<{ key: string }>("/integrations", {
          tenantId,
          method: "POST",
          body: json({ name, scopes, expires_in_days: days, grants }),
        });
        setSecret(result.key);
        setCopied(false);
      }
      setEdit(null);
      setMessage(
        edit?.id
          ? "Integration updated."
          : "Integration created. Copy the credential before closing its dialog.",
      );
    });
  }
  function scopeControls(): ReactNode {
    return (
      <>
        <fieldset className="fieldset">
          <legend>Allowed operations</legend>
          {["read", "query", "write"].map((scope) => (
            <label className="checkbox-row" key={scope}>
              <input
                type="checkbox"
                checked={scopes.includes(scope)}
                onChange={(e) =>
                  setScopes(
                    e.target.checked
                      ? [...scopes, scope]
                      : scopes.filter((s) => s !== scope),
                  )
                }
              />
              <span>
                {scope === "read"
                  ? "Read permitted knowledge"
                  : scope === "query"
                    ? "Ask questions and use search tools"
                    : "Maintain permitted knowledge"}
              </span>
            </label>
          ))}
        </fieldset>
        <Field
          label="Expires after (days)"
          type="number"
          min={1}
          max={365}
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          required
        />
      </>
    );
  }
  if (!canManage) return <Forbidden />;
  return (
    <>
      <PageHeader
        title="Integrations"
        description="Separate credentials for tools and services, with explicit access to knowledge."
        action={
          <Button onClick={() => open()}>
            <Plus size={16} />
            Create integration
          </Button>
        }
      />
      <ErrorNotice
        error={
          !edit && !rotate && !revoke && !toggle
            ? error || services.error
            : null
        }
      />
      {message && <Notice>{message}</Notice>}
      <div className="notice">
        Integration credentials never inherit a person’s private conversations.
        Keep each service limited to the spaces and operations it needs.
      </div>
      {services.isPending ? (
        <Loading />
      ) : !services.data?.items.length ? (
        <EmptyState
          icon={<KeyRound />}
          title="Connect tools with controlled access"
          description="Create a service identity and a scoped credential for API or MCP clients."
          action={<Button onClick={() => open()}>Create integration</Button>}
        />
      ) : (
        <div className="stack">
          {services.data.items.map((service) => (
            <section className="panel" key={service.id}>
              <div className="row between">
                <div>
                  <h2>{service.name}</h2>
                  <div className="row">
                    <Badge tone={service.active ? "success" : "neutral"}>
                      {service.active ? "Active" : "Disabled"}
                    </Badge>
                    <span className="muted text-small">
                      {service.grants?.length || 0} space grants
                    </span>
                  </div>
                </div>
                <div className="row">
                  <Button variant="secondary" onClick={() => open(service)}>
                    Access settings
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setRotate(service);
                      setScopes(["read", "query"]);
                      setDays(30);
                      setError(null);
                    }}
                  >
                    Rotate key
                  </Button>
                  <Button variant="ghost" onClick={() => setToggle(service)}>
                    {service.active ? "Disable" : "Enable"}
                  </Button>
                </div>
              </div>
              <div className="grant-chips">
                {service.grants?.map((g) => (
                  <Badge key={g.space_id}>
                    {spaces.data?.find((s) => s.id === g.space_id)?.name ||
                      "Unavailable space"}{" "}
                    · {g.permission}
                  </Badge>
                ))}
              </div>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Key</th>
                      <th>Scopes</th>
                      <th>Expires</th>
                      <th>Last used</th>
                      <th>Status</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {service.keys?.map((key) => (
                      <tr key={key.id}>
                        <td>{key.label || `${key.id.slice(0, 8)}…`}</td>
                        <td>{key.scopes.join(", ")}</td>
                        <td>{date(key.expires_at)}</td>
                        <td>{date(key.last_used_at)}</td>
                        <td>
                          <Badge>
                            {key.revoked_at
                              ? "Revoked"
                              : key.expires_at &&
                                  new Date(key.expires_at) < new Date()
                                ? "Expired"
                                : "Active"}
                          </Badge>
                        </td>
                        <td>
                          {!key.revoked_at && (
                            <Button
                              variant="ghost"
                              onClick={() => setRevoke({ service, key })}
                            >
                              Revoke
                            </Button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ))}
        </div>
      )}
      <Modal
        open={!!edit}
        onOpenChange={(v) => !v && setEdit(null)}
        title={edit?.id ? "Integration access" : "Create integration"}
        description="Grant only the spaces this service needs."
      >
        <form className="form-stack" onSubmit={save}>
          <ErrorNotice error={error} />
          <Field
            label="Integration name"
            placeholder="e.g. Internal support assistant"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          {!edit?.id && scopeControls()}
          <fieldset className="fieldset">
            <legend>Space grants</legend>
            {spaces.data?.map((space) => {
              const grant = grants.find((g) => g.space_id === space.id);
              return (
                <div className="setting-row" key={space.id}>
                  <label className="checkbox-row">
                    <input
                      type="checkbox"
                      checked={!!grant}
                      onChange={(e) =>
                        setGrants(
                          e.target.checked
                            ? [
                                ...grants,
                                { space_id: space.id, permission: "read" },
                              ]
                            : grants.filter((g) => g.space_id !== space.id),
                        )
                      }
                    />
                    {space.name}
                  </label>
                  {grant && (
                    <select
                      aria-label={`Permission for ${space.name}`}
                      value={grant.permission}
                      onChange={(e) =>
                        setGrants(
                          grants.map((g) =>
                            g.space_id === space.id
                              ? {
                                  ...g,
                                  permission: e.target.value as
                                    "read" | "write",
                                }
                              : g,
                          ),
                        )
                      }
                    >
                      <option value="read">Read</option>
                      <option value="write">Read & write</option>
                    </select>
                  )}
                </div>
              );
            })}
            {!spaces.data?.length && (
              <p className="muted">
                Create a knowledge space before granting access.
              </p>
            )}
          </fieldset>
          <Button
            busy={busy}
            disabled={!edit?.id && (!scopes.length || !grants.length)}
          >
            {edit?.id ? "Save access settings" : "Create credential"}
          </Button>
        </form>
      </Modal>
      <Modal
        open={!!rotate}
        onOpenChange={(v) => !v && setRotate(null)}
        title="Rotate integration key"
        description="A new key replaces the current credentials. Update your integration immediately."
      >
        <form
          className="form-stack"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              const result = await api<{ key: string }>(
                `/integrations/${rotate?.id}/rotate`,
                {
                  tenantId,
                  method: "POST",
                  body: json({ scopes, expires_in_days: days }),
                },
              );
              setRotate(null);
              setSecret(result.key);
              setCopied(false);
            });
          }}
        >
          <ErrorNotice error={error} />
          {scopeControls()}
          <Button busy={busy} disabled={!scopes.length}>
            Rotate key
          </Button>
        </form>
      </Modal>
      <Modal
        open={!!secret}
        onOpenChange={(v) => !v && setSecret("")}
        title="Save your integration key"
        description="This secret is shown once. Copy it to your integration’s secure configuration before closing."
      >
        <code className="secret-code integration-secret">{secret}</code>
        <Button
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(secret);
              setCopied(true);
            } catch {
              setCopied(false);
            }
          }}
        >
          {copied ? <Check size={16} /> : <KeyRound size={16} />}{" "}
          {copied ? "Copied" : "Copy key"}
        </Button>
      </Modal>
      <Modal
        open={!!revoke}
        onOpenChange={(v) => !v && setRevoke(null)}
        title="Revoke this key?"
        description="Requests using this credential will lose access immediately."
      >
        <ErrorNotice error={error} />
        <Button
          variant="danger"
          busy={busy}
          onClick={() =>
            run(async () => {
              await api(
                `/integrations/${revoke?.service.id}/keys/${revoke?.key.id}`,
                { tenantId, method: "DELETE" },
              );
              setRevoke(null);
              setMessage("Credential revoked.");
            })
          }
        >
          Revoke key
        </Button>
      </Modal>
      <Modal
        open={!!toggle}
        onOpenChange={(v) => !v && setToggle(null)}
        title={toggle?.active ? "Disable integration?" : "Enable integration?"}
        description="This changes whether the service identity can access the company."
      >
        <ErrorNotice error={error} />
        <Button
          busy={busy}
          onClick={() =>
            run(async () => {
              await api(`/integrations/${toggle?.id}`, {
                tenantId,
                method: "PATCH",
                body: json({ active: !toggle?.active }),
              });
              setToggle(null);
            })
          }
        >
          Confirm
        </Button>
      </Modal>
    </>
  );
}
type Usage = {
  summary: {
    requests: number;
    input_tokens: number;
    output_tokens: number;
    actual_api_cost_usd: number;
    average_duration_ms: number;
    p95_duration_ms: number;
    failures: number;
    cache_hits: number | null;
    indexed_documents: number;
    average_indexing_ms: number;
  };
  daily: {
    day: string;
    requests: number;
    input_tokens: number;
    output_tokens: number;
    average_duration_ms: number;
    failures: number;
  }[];
  members: {
    user_id: string;
    name: string;
    requests: number;
    input_tokens: number;
    output_tokens: number;
  }[];
};
type SystemStatus = {
  database: { ready: boolean };
  redis: { ready: boolean };
  cache: { ready: boolean };
  generation: { ready: boolean; model?: string };
  embeddings: { ready: boolean };
  reranker: { ready: boolean };
  paid_apis: boolean;
};
export function InsightsPage() {
  const { tenantId, canManage } = useWorkspace();
  const [days, setDays] = useState("30");
  const usage = useQuery({
    queryKey: useScopedQueryKey("usage", days),
    queryFn: ({ signal }) =>
      api<Usage>(`/administration/usage?days=${days}`, { tenantId, signal }),
    enabled: canManage,
  });
  const system = useQuery({
    queryKey: useScopedQueryKey("system"),
    queryFn: ({ signal }) =>
      api<SystemStatus>("/administration/system", { tenantId, signal }),
    enabled: canManage,
  });
  if (!canManage) return <Forbidden />;
  const summary = usage.data?.summary;
  const max = Math.max(1, ...(usage.data?.daily.map((d) => d.requests) || []));
  return (
    <>
      <PageHeader
        title="Workspace insights"
        description="Measured activity and actual component readiness."
        action={
          <>
            <select
              aria-label="Insights time range"
              value={days}
              onChange={(e) => setDays(e.target.value)}
            >
              <option value="7">Last 7 days</option>
              <option value="30">Last 30 days</option>
              <option value="90">Last 90 days</option>
            </select>
            <Button
              variant="secondary"
              busy={usage.isFetching || system.isFetching}
              onClick={() => {
                void usage.refetch();
                void system.refetch();
              }}
            >
              <RefreshCw size={16} />
              Refresh
            </Button>
          </>
        }
      />
      <ErrorNotice error={usage.error || system.error} />
      {usage.isPending ? (
        <Loading />
      ) : (
        summary && (
          <>
            <div className="metrics-grid">
              {[
                ["Requests", number(summary.requests)],
                [
                  "Tokens used",
                  number(summary.input_tokens + summary.output_tokens),
                ],
                [
                  "Average response",
                  `${number(summary.average_duration_ms)} ms`,
                ],
                ["95th percentile", `${number(summary.p95_duration_ms)} ms`],
                [
                  "Cache reuse",
                  summary.cache_hits === null
                    ? "Not recorded"
                    : summary.requests
                      ? `${Math.round((summary.cache_hits / summary.requests) * 100)}%`
                      : "No requests",
                ],
                ["Failures", number(summary.failures)],
                ["Indexed documents", number(summary.indexed_documents)],
                [
                  "Average indexing (including queue)",
                  summary.indexed_documents
                    ? `${number(summary.average_indexing_ms)} ms`
                    : "No completed jobs",
                ],
                [
                  "Model API cost",
                  `$${Number(summary.actual_api_cost_usd || 0).toFixed(2)}`,
                ],
              ].map(([label, value]) => (
                <div className="stat-card" key={label}>
                  <span>{label}</span>
                  <strong>{value}</strong>
                </div>
              ))}
            </div>
            <MonthlyQuota />
            <section className="panel">
              <div className="row between">
                <h2>Daily requests</h2>
                <span className="muted text-small">
                  Recorded activity · {days} days
                </span>
              </div>
              {usage.data!.daily.length ? (
                <>
                  <div
                    className="activity-chart"
                    role="img"
                    aria-label={`Daily request counts; exact values are available in the table below.`}
                  >
                    {usage.data!.daily.map((day) => (
                      <div className="chart-column" key={day.day}>
                        <span>{day.requests}</span>
                        <div
                          className="chart-bar"
                          style={{
                            height: `${Math.max(2, (day.requests / max) * 140)}px`,
                          }}
                        />
                        <small>
                          {new Date(day.day).toLocaleDateString(undefined, {
                            month: "short",
                            day: "numeric",
                          })}
                        </small>
                      </div>
                    ))}
                  </div>
                  <details>
                    <summary>View exact daily measurements</summary>
                    <div className="table-wrap">
                      <table className="data-table">
                        <thead>
                          <tr>
                            <th>Date</th>
                            <th>Requests</th>
                            <th>Tokens</th>
                            <th>Average duration</th>
                            <th>Failures</th>
                          </tr>
                        </thead>
                        <tbody>
                          {usage.data!.daily.map((day) => (
                            <tr key={day.day}>
                              <td>{new Date(day.day).toLocaleDateString()}</td>
                              <td>{number(day.requests)}</td>
                              <td>
                                {number(day.input_tokens + day.output_tokens)}
                              </td>
                              <td>{number(day.average_duration_ms)} ms</td>
                              <td>{day.failures}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </details>
                </>
              ) : (
                <EmptyState
                  title="No recorded requests yet"
                  description="Ask a question to begin building your workspace’s usage history."
                />
              )}
            </section>
            <section className="panel">
              <h2>Usage by member</h2>
              <p className="muted">
                Usage totals do not expose private conversation contents.
              </p>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Member</th>
                      <th>Requests</th>
                      <th>Input tokens</th>
                      <th>Output tokens</th>
                    </tr>
                  </thead>
                  <tbody>
                    {usage.data!.members.map((member) => (
                      <tr key={member.user_id}>
                        <td>
                          {member.name || "Integration or legacy activity"}
                        </td>
                        <td>{number(member.requests)}</td>
                        <td>{number(member.input_tokens)}</td>
                        <td>{number(member.output_tokens)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )
      )}
      <section className="panel">
        <h2>Runtime readiness</h2>
        <p className="muted">
          Checks reflect this request. Optional reranking can be unavailable
          while ordinary retrieval remains usable.
        </p>
        {system.isPending ? (
          <Loading label="Checking components…" />
        ) : (
          system.data && (
            <div className="readiness-grid">
              {(
                [
                  "database",
                  "redis",
                  "cache",
                  "embeddings",
                  "generation",
                  "reranker",
                ] as const
              ).map((component) => (
                <div className="setting-row" key={component}>
                  <span>
                    {
                      {
                        database: "PostgreSQL",
                        redis: "Job queue",
                        cache: "Answer cache",
                        embeddings: "Embedding model",
                        generation: "Generation model",
                        reranker: "Optional reranker",
                      }[component]
                    }
                  </span>
                  <Badge
                    tone={system.data![component].ready ? "success" : "error"}
                  >
                    {system.data![component].ready ? "Ready" : "Unavailable"}
                  </Badge>
                </div>
              ))}
            </div>
          )
        )}
        {system.data && (
          <p className="muted text-small">
            Generation: {system.data.generation.model || "No model reported"} ·
            Paid model APIs: {system.data.paid_apis ? "Enabled" : "Disabled"}
          </p>
        )}
      </section>
    </>
  );
}
type Limits = {
  limits: {
    requests_per_minute: number;
    monthly_tokens: number;
    monthly_usd: number;
  };
  members: {
    user_id: string;
    name: string;
    email: string;
    monthly_tokens: number | null;
    can_evaluate: boolean;
  }[];
};
type Audit = {
  id: string;
  actor_name?: string;
  actor_id?: string;
  action: string;
  resource_type?: string;
  resource_id?: string;
  outcome?: string;
  created_at: string;
  metadata?: Record<string, unknown>;
};
export function AdministrationPage() {
  const { tenantId, canManage } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "limits";
  const [page, setPage] = useState(1);
  const { busy, error, setError, message, setMessage, run } = useActions();
  const limits = useQuery({
    queryKey: useScopedQueryKey("limits"),
    queryFn: ({ signal }) =>
      api<Limits>("/administration/limits", { tenantId, signal }),
    enabled: canManage && tab === "limits",
  });
  const audit = useQuery({
    queryKey: useScopedQueryKey("audit", page),
    queryFn: ({ signal }) =>
      api<{ items: Audit[]; total: number }>(
        `/administration/audit?offset=${(page - 1) * 30}&limit=30`,
        { tenantId, signal },
      ),
    enabled: canManage && tab === "audit",
  });
  const [rpm, setRpm] = useState(60);
  const [tokens, setTokens] = useState(1000000);
  const [member, setMember] = useState<Limits["members"][number] | null>(null);
  const [memberTokens, setMemberTokens] = useState("");
  const [canEvaluate, setCanEvaluate] = useState(false);
  const [auditItem, setAuditItem] = useState<Audit | null>(null);
  useEffect(() => {
    if (limits.data) {
      setRpm(limits.data.limits.requests_per_minute);
      setTokens(limits.data.limits.monthly_tokens);
    }
  }, [limits.data]);
  if (!canManage) return <Forbidden />;
  return (
    <>
      <PageHeader
        title="Administration"
        description="Usage boundaries and an accountable history of company changes."
      />
      <div className="page-tabs">
        <button
          className="link-button"
          aria-pressed={tab === "limits"}
          onClick={() => setParams({ tab: "limits" })}
        >
          Limits & capabilities
        </button>
        <button
          className="link-button"
          aria-pressed={tab === "audit"}
          onClick={() => setParams({ tab: "audit" })}
        >
          Audit history
        </button>
        <button
          className="link-button"
          aria-pressed={tab === "legacy"}
          onClick={() => setParams({ tab: "legacy" })}
        >
          Legacy archive
        </button>
      </div>
      <ErrorNotice
        error={!member ? error || limits.error || audit.error : null}
      />
      {message && <Notice>{message}</Notice>}
      {tab === "legacy" ? (
        <LegacyArchive />
      ) : tab === "limits" ? (
        <>
          <section className="panel narrow-panel">
            <h2>Company limits</h2>
            <p className="muted">
              All model-consuming actions share the company token budget. Paid
              model API spending remains zero.
            </p>
            {limits.isPending ? (
              <Loading />
            ) : (
              <form
                className="form-stack"
                onSubmit={(e) => {
                  e.preventDefault();
                  void run(async () => {
                    await api("/administration/limits", {
                      tenantId,
                      method: "PATCH",
                      body: json({
                        requests_per_minute: rpm,
                        monthly_tokens: tokens,
                      }),
                    });
                    setMessage("Company limits updated.");
                  });
                }}
              >
                <Field
                  label="Requests per minute"
                  type="number"
                  min={1}
                  max={10000}
                  value={rpm}
                  onChange={(e) => setRpm(Number(e.target.value))}
                  required
                />
                <Field
                  label="Monthly token budget"
                  type="number"
                  min={1}
                  value={tokens}
                  onChange={(e) => setTokens(Number(e.target.value))}
                  required
                />
                <Button busy={busy}>Save company limits</Button>
              </form>
            )}
          </section>
          <section className="panel">
            <h2>Member limits & evaluation access</h2>
            <p className="muted">
              A member limit adds a smaller boundary within the company budget.
              Evaluation access may be delegated to editors.
            </p>
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Member</th>
                    <th>Monthly token limit</th>
                    <th>Evaluation capability</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {limits.data?.members.map((m) => (
                    <tr key={m.user_id}>
                      <td>
                        <strong>{m.name}</strong>
                        <small>{m.email}</small>
                      </td>
                      <td>
                        {m.monthly_tokens === null
                          ? "Company budget only"
                          : number(m.monthly_tokens)}
                      </td>
                      <td>
                        <Badge>
                          {m.can_evaluate ? "Delegated" : "Role default"}
                        </Badge>
                      </td>
                      <td>
                        <Button
                          variant="secondary"
                          onClick={() => {
                            setMember(m);
                            setMemberTokens(
                              m.monthly_tokens === null
                                ? ""
                                : String(m.monthly_tokens),
                            );
                            setCanEvaluate(m.can_evaluate);
                            setError(null);
                          }}
                        >
                          Edit limits
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      ) : (
        <section className="panel">
          <h2>Audit history</h2>
          <p className="muted">
            Metadata records who changed what. Passwords, credentials and
            private conversation text are excluded.
          </p>
          {audit.isPending ? (
            <Loading />
          ) : !audit.data?.items.length ? (
            <EmptyState
              icon={<ClipboardList />}
              title="No audit events yet"
              description="Administrative actions will appear here as they happen."
            />
          ) : (
            <>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Actor</th>
                      <th>Action</th>
                      <th>Resource</th>
                      <th>Outcome</th>
                      <th>Details</th>
                    </tr>
                  </thead>
                  <tbody>
                    {audit.data.items.map((event) => (
                      <tr key={event.id}>
                        <td>{date(event.created_at)}</td>
                        <td>
                          {event.actor_name ||
                            event.actor_id?.slice(0, 8) ||
                            "System or integration"}
                        </td>
                        <td>{event.action.replaceAll("_", " ")}</td>
                        <td>{event.resource_type || "—"}</td>
                        <td>
                          <Badge>{event.outcome || "Recorded"}</Badge>
                        </td>
                        <td>
                          <Button
                            variant="ghost"
                            onClick={() => setAuditItem(event)}
                          >
                            Inspect
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="pagination">
                <span>{audit.data.total} events</span>
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
                  disabled={page * 30 >= audit.data.total}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next
                </Button>
              </div>
            </>
          )}
        </section>
      )}
      <Modal
        open={!!member}
        onOpenChange={(v) => !v && setMember(null)}
        title={`Limits for ${member?.name || "member"}`}
        description="Leave the token limit blank to use only the company budget."
      >
        <form
          className="form-stack"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await api(`/administration/limits/members/${member?.user_id}`, {
                tenantId,
                method: "PUT",
                body: json({
                  monthly_tokens: memberTokens ? Number(memberTokens) : null,
                  can_evaluate: canEvaluate,
                }),
              });
              setMember(null);
              setMessage("Member settings updated.");
            });
          }}
        >
          <ErrorNotice error={error} />
          <Field
            label="Monthly token limit (optional)"
            type="number"
            min={1}
            value={memberTokens}
            onChange={(e) => setMemberTokens(e.target.value)}
          />
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={canEvaluate}
              onChange={(e) => setCanEvaluate(e.target.checked)}
            />
            Allow evaluation review and runs for an editor
          </label>
          <Button busy={busy}>Save member settings</Button>
        </form>
      </Modal>
      <Modal
        open={!!auditItem}
        onOpenChange={(v) => !v && setAuditItem(null)}
        title="Audit event"
        description="Administrative metadata only."
      >
        {auditItem && (
          <KeyValues
            values={{
              action: auditItem.action,
              time: date(auditItem.created_at),
              actor: auditItem.actor_name || auditItem.actor_id,
              resource: auditItem.resource_id,
              outcome: auditItem.outcome,
              ...auditItem.metadata,
            }}
          />
        )}
      </Modal>
    </>
  );
}
type Label = {
  id: string;
  question: string;
  document_id: string;
  source_title: string;
  evidence: string;
  start_offset: number;
  end_offset: number;
  split: string;
  reviewed: boolean;
  reviewed_at?: string;
  source_hash: string;
};
type EvaluationRun = {
  id: string;
  created_at: string;
  mode: string;
  label_count: number;
  config: Record<string, unknown>;
  metrics: Record<string, number>;
  manifest: { quality_basis?: string; judge_calibrated?: boolean };
  results: {
    label_id: string;
    question: string;
    recall_at_5: number;
    mrr: number;
    ndcg_at_5: number;
    judgment?: { score: number | null };
  }[];
};
type EvaluationJob = {
  id: string;
  status: string;
  progress: number;
  total?: number;
  config: Record<string, unknown>;
  result_id?: string;
  error_code?: string;
  cancel_requested: boolean;
  created_at: string;
  updated_at: string;
};
type Feedback = {
  id: string;
  rating?: string;
  reason?: string;
  correction?: string;
  status: string;
  created_at: string;
  question?: string;
  document_id?: string;
};
export function QualityPage() {
  const { tenantId, canManage, organization } = useWorkspace();
  const permitted =
    canManage ||
    !!(organization as typeof organization & { can_evaluate?: boolean })
      .can_evaluate;
  const { busy, error, setError, message, setMessage, run } = useActions();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "review";
  const labels = useQuery({
    queryKey: useScopedQueryKey("labels"),
    queryFn: ({ signal }) =>
      api<Label[]>("/evaluation/labels", { tenantId, signal }),
    enabled: permitted,
  });
  const runs = useQuery({
    queryKey: useScopedQueryKey("evaluation-runs"),
    queryFn: ({ signal }) =>
      api<EvaluationRun[]>("/evaluation/runs", { tenantId, signal }),
    enabled: permitted && tab === "runs",
  });
  const jobs = useQuery({
    queryKey: useScopedQueryKey("evaluation-jobs"),
    queryFn: ({ signal }) =>
      api<{ items: EvaluationJob[] }>("/evaluation/jobs", { tenantId, signal }),
    enabled: permitted && tab === "runs",
    refetchInterval: (q) =>
      q.state.data?.items.some((job) =>
        ["queued", "running"].includes(job.status),
      )
        ? 2000
        : false,
  });
  const feedback = useQuery({
    queryKey: useScopedQueryKey("feedback"),
    queryFn: ({ signal }) =>
      api<{ items: Feedback[] }>("/administration/feedback", {
        tenantId,
        signal,
      }),
    enabled: canManage && tab === "feedback",
  });
  const [selected, setSelected] = useState(0);
  const [question, setQuestion] = useState("");
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(1);
  const [runOpen, setRunOpen] = useState(false);
  const [mode, setMode] = useState("hybrid");
  const [topK, setTopK] = useState(5);
  const [chunkSize, setChunkSize] = useState(1000);
  const [split, setSplit] = useState("development");
  const [rerank, setRerank] = useState(false);
  const [judge, setJudge] = useState(false);
  const [runDetail, setRunDetail] = useState<EvaluationRun | null>(null);
  const [left, setLeft] = useState("");
  const [right, setRight] = useState("");
  const [comparison, setComparison] = useState<{
    left: EvaluationRun;
    right: EvaluationRun;
    deltas: Record<string, number>;
    comparable: boolean;
    reasons: string[];
    regressions?: {
      label_id: string;
      question: string;
      before: number;
      after: number;
    }[];
  } | null>(null);
  const completedRuns = jobs.data?.items
    .map((job) => job.result_id || "")
    .join(",");
  useEffect(() => {
    if (completedRuns) void runs.refetch();
  }, [completedRuns]);
  const [candidate, setCandidate] = useState<Feedback | null>(null);
  const [candidateDocument, setCandidateDocument] = useState("");
  const label = labels.data?.[selected];
  const source = useQuery({
    queryKey: useScopedQueryKey("review-source", label?.document_id),
    queryFn: ({ signal }) =>
      api<{ id: string; title: string; content: string }>(
        `/library/${label!.document_id}`,
        { tenantId, signal },
      ),
    enabled: permitted && tab === "review" && !!label,
  });
  const documents = useQuery({
    queryKey: useScopedQueryKey("candidate-documents"),
    queryFn: ({ signal }) =>
      api<{ items: DocumentSummary[] }>(
        "/library?page_size=100&lifecycle=active",
        { tenantId, signal },
      ),
    enabled: !!candidate,
  });
  const candidateSource = useQuery({
    queryKey: useScopedQueryKey("candidate-source", candidateDocument),
    queryFn: ({ signal }) =>
      api<{ content: string }>(`/library/${candidateDocument}`, {
        tenantId,
        signal,
      }),
    enabled: !!candidate && !!candidateDocument,
  });
  useEffect(() => {
    if (label) {
      setQuestion(label.question);
      setStart(label.start_offset);
      setEnd(label.end_offset);
    }
  }, [label?.id, label?.question, label?.start_offset, label?.end_offset]);
  useEffect(() => {
    if (selected >= (labels.data?.length || 1)) setSelected(0);
  }, [labels.data?.length, selected]);
  const reviewed = labels.data?.filter((l) => l.reviewed).length || 0;
  const allReviewed =
    !!labels.data &&
    labels.data.length >= 40 &&
    reviewed === labels.data.length;
  async function saveReview(reviewed: boolean) {
    if (!label) return;
    await run(async () => {
      await api(`/evaluation/labels/${label.id}`, {
        tenantId,
        method: "PUT",
        body: json({
          question,
          start_offset: start,
          end_offset: end,
          reviewed,
        }),
      });
      setMessage(
        reviewed
          ? "Evidence reviewed and approved by you."
          : "Label saved as unreviewed.",
      );
      if (reviewed && selected < (labels.data?.length || 0) - 1)
        setSelected((v) => v + 1);
    });
  }
  if (!permitted) return <Forbidden />;
  return (
    <>
      <PageHeader
        title="Knowledge quality"
        description="Review evidence, learn from feedback and measure what your answers retrieve."
      />
      <div className="page-tabs">
        <button
          className="link-button"
          aria-pressed={tab === "review"}
          onClick={() => setParams({ tab: "review" })}
        >
          Evidence review
        </button>
        <button
          className="link-button"
          aria-pressed={tab === "runs"}
          onClick={() => setParams({ tab: "runs" })}
        >
          Evaluation runs
        </button>
        {canManage && (
          <button
            className="link-button"
            aria-pressed={tab === "feedback"}
            onClick={() => setParams({ tab: "feedback" })}
          >
            Answer feedback
          </button>
        )}
      </div>
      <ErrorNotice
        error={
          !runOpen && !candidate
            ? error ||
              labels.error ||
              runs.error ||
              jobs.error ||
              feedback.error
            : null
        }
      />
      {message && <Notice>{message}</Notice>}
      {tab === "review" ? (
        <>
          <section className="panel review-progress">
            <div>
              <strong>
                {reviewed} of {labels.data?.length || 0} labels reviewed
              </strong>
              <p className="muted">
                Human-reviewed evidence is required for corpus evaluation. No
                automated action approves a label.
              </p>
            </div>
            <progress
              aria-label="Evidence review progress"
              value={reviewed}
              max={Math.max(1, labels.data?.length || 0)}
            />
          </section>
          {labels.isPending ? (
            <Loading />
          ) : !label ? (
            <EmptyState
              icon={<ClipboardList />}
              title="Build your evaluation set"
              description="Convert submitted feedback into unreviewed candidates, or import a prepared review set through the documented workflow."
            />
          ) : (
            <div
              className="review-layout"
              onKeyDown={(e) => {
                if (e.altKey && e.key === "ArrowRight") {
                  e.preventDefault();
                  setSelected(
                    Math.min(selected + 1, (labels.data?.length || 1) - 1),
                  );
                }
                if (e.altKey && e.key === "ArrowLeft") {
                  e.preventDefault();
                  setSelected(Math.max(0, selected - 1));
                }
              }}
            >
              <aside
                className="panel review-queue"
                aria-label="Evidence review queue"
              >
                {labels.data?.map((item, index) => (
                  <button
                    className={selected === index ? "selected" : ""}
                    key={item.id}
                    onClick={() => setSelected(index)}
                  >
                    <span>
                      {item.reviewed ? <Check size={15} /> : index + 1}
                    </span>
                    <span>{item.question}</span>
                  </button>
                ))}
              </aside>
              <section className="panel">
                <div className="row between">
                  <Badge>{label.split.replaceAll("_", " ")}</Badge>
                  <span className="muted text-small">
                    {selected + 1} / {labels.data?.length} · Alt + ← / →
                  </span>
                </div>
                <h2 className="section-gap">
                  Review the question and evidence
                </h2>
                <label className="field">
                  <span>Evaluation question</span>
                  <textarea
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    minLength={5}
                  />
                </label>
                <div className="grid-2 section-gap">
                  <Field
                    label="Evidence start offset"
                    type="number"
                    min={0}
                    value={start}
                    onChange={(e) => setStart(Number(e.target.value))}
                  />
                  <Field
                    label="Evidence end offset"
                    type="number"
                    min={1}
                    value={end}
                    onChange={(e) => setEnd(Number(e.target.value))}
                  />
                </div>
                <h3 className="section-gap">Selected evidence</h3>
                <blockquote className="evidence-quote">
                  {(source.data
                    ? Array.from(source.data.content).slice(start, end).join("")
                    : "") || label.evidence}
                </blockquote>
                <p className="muted text-small">
                  {label.source_title} · characters {start}–{end}
                </p>
                <div className="row">
                  <Button busy={busy} onClick={() => saveReview(true)}>
                    Approve evidence & next
                  </Button>
                  <Button
                    variant="secondary"
                    busy={busy}
                    onClick={() => saveReview(false)}
                  >
                    Save unreviewed
                  </Button>
                </div>
                <ErrorNotice error={source.error} />
                <details className="section-gap">
                  <summary>Inspect full source</summary>
                  {source.isPending ? (
                    <Loading />
                  ) : (
                    <pre className="source-text">{source.data?.content}</pre>
                  )}
                  <Link
                    className="text-link"
                    to={`/o/${tenantId}/library/${label.document_id}`}
                  >
                    Open document
                  </Link>
                </details>
              </section>
            </div>
          )}
        </>
      ) : tab === "runs" ? (
        <>
          <section className="panel">
            <div className="row between">
              <div>
                <h2>Corpus evaluations</h2>
                <p className="muted">
                  Runs use explicitly reviewed labels. Local judge scores are
                  uncalibrated proxies.
                </p>
              </div>
              <Button
                disabled={!allReviewed}
                onClick={() => {
                  setRunOpen(true);
                  setError(null);
                }}
              >
                Start evaluation
              </Button>
            </div>
            {!allReviewed && (
              <div className="notice">
                Review all current labels, with at least 40 valid labels, before
                starting a corpus evaluation.
              </div>
            )}
            {jobs.data?.items.map((job) => (
              <div className="setting-row" key={job.id}>
                <div>
                  <strong>Evaluation {job.id.slice(0, 8)}</strong>
                  <p>
                    {date(job.created_at)} · {job.progress}
                    {job.total ? ` / ${job.total}` : ""} labels processed
                    {job.error_code ? ` · ${job.error_code}` : ""}
                  </p>
                </div>
                <Badge>
                  {job.cancel_requested ? "Cancellation requested" : job.status}
                </Badge>
                {["queued", "running"].includes(job.status) && (
                  <Button
                    variant="secondary"
                    disabled={busy || job.cancel_requested}
                    onClick={() =>
                      run(async () => {
                        await api(`/evaluation/jobs/${job.id}/cancel`, {
                          tenantId,
                          method: "POST",
                        });
                        setMessage(
                          "Cancellation requested; the current operation may finish first.",
                        );
                      })
                    }
                  >
                    Cancel
                  </Button>
                )}
              </div>
            ))}
          </section>
          {runs.isPending ? (
            <Loading />
          ) : !runs.data?.length ? (
            <EmptyState
              title="No corpus results yet"
              description="Complete the review queue and run an evaluation to establish a measured baseline."
            />
          ) : (
            <section className="panel">
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Run</th>
                      <th>Basis</th>
                      <th>Labels</th>
                      <th>Recall@5</th>
                      <th>MRR</th>
                      <th>nDCG@5</th>
                      <th>Details</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.data.map((r) => (
                      <tr key={r.id}>
                        <td>
                          {date(r.created_at)}
                          <small>{r.mode}</small>
                        </td>
                        <td>{r.manifest.quality_basis || "Unspecified"}</td>
                        <td>{r.label_count}</td>
                        <td>{r.metrics.recall_at_5?.toFixed(3) || "—"}</td>
                        <td>{r.metrics.mrr?.toFixed(3) || "—"}</td>
                        <td>{r.metrics.ndcg_at_5?.toFixed(3) || "—"}</td>
                        <td>
                          <Button
                            variant="ghost"
                            onClick={() => setRunDetail(r)}
                          >
                            Inspect
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <h3 className="section-gap">Compare runs</h3>
              <div className="toolbar">
                <select
                  aria-label="Baseline run"
                  value={left}
                  onChange={(e) => setLeft(e.target.value)}
                >
                  <option value="">Choose baseline</option>
                  {runs.data.map((r) => (
                    <option value={r.id} key={r.id}>
                      {date(r.created_at)} · {r.mode}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="Comparison run"
                  value={right}
                  onChange={(e) => setRight(e.target.value)}
                >
                  <option value="">Choose comparison</option>
                  {runs.data.map((r) => (
                    <option value={r.id} key={r.id}>
                      {date(r.created_at)} · {r.mode}
                    </option>
                  ))}
                </select>
                <Button
                  variant="secondary"
                  busy={busy}
                  disabled={!left || !right || left === right}
                  onClick={() =>
                    run(async () =>
                      setComparison(
                        await api(
                          `/evaluation/compare?left=${left}&right=${right}`,
                          { tenantId },
                        ),
                      ),
                    )
                  }
                >
                  Compare runs
                </Button>
              </div>
            </section>
          )}
        </>
      ) : (
        <section className="panel">
          <h2>Submitted answer feedback</h2>
          <p className="muted">
            Only feedback explicitly submitted to administrators appears here.
            Personal conversation history is not an administration feed.
          </p>
          {feedback.isPending ? (
            <Loading />
          ) : !feedback.data?.items.length ? (
            <EmptyState
              title="No feedback to triage"
              description="Members can submit an answer, a reason and an optional correction for review."
            />
          ) : (
            <div className="feedback-list">
              {feedback.data.items.map((item) => (
                <article key={item.id}>
                  <div className="row between">
                    <Badge>
                      {item.reason?.replaceAll("_", " ") ||
                        item.rating ||
                        "Feedback"}
                    </Badge>
                    <small className="muted">{date(item.created_at)}</small>
                  </div>
                  {item.question && <h3>{item.question}</h3>}
                  <p>{item.correction || "No additional correction."}</p>
                  <div className="row">
                    <label className="field">
                      <span className="sr-only">Feedback status</span>
                      <select
                        aria-label={`Status for feedback ${item.id.slice(0, 8)}`}
                        value={item.status}
                        disabled={busy}
                        onChange={(e) =>
                          run(async () => {
                            await api(`/administration/feedback/${item.id}`, {
                              tenantId,
                              method: "PATCH",
                              body: json({ status: e.target.value }),
                            });
                          })
                        }
                      >
                        <option value="new">New</option>
                        <option value="reviewed">Reviewed</option>
                        <option value="candidate" disabled>
                          Review candidate created
                        </option>
                        <option value="closed">Closed</option>
                      </select>
                    </label>
                    <Button
                      variant="secondary"
                      onClick={() => {
                        setCandidate(item);
                        setQuestion(item.question || "");
                        setCandidateDocument(item.document_id || "");
                        setStart(0);
                        setEnd(1);
                        setError(null);
                      }}
                    >
                      Create review candidate
                    </Button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      )}
      <Modal
        open={runOpen}
        onOpenChange={setRunOpen}
        title="Start corpus evaluation"
        description="This is a background job. Judge mode also generates answers and consumes your local token budget."
      >
        <form
          className="form-stack"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await api("/evaluation/jobs", {
                tenantId,
                method: "POST",
                body: json({
                  mode,
                  top_k: topK,
                  chunk_size: chunkSize,
                  split,
                  rerank,
                  with_judge: judge,
                }),
              });
              setRunOpen(false);
              setMessage("Evaluation queued.");
            });
          }}
        >
          <ErrorNotice error={error} />
          <label className="field">
            <span>Retrieval mode</span>
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="hybrid">Hybrid</option>
              <option value="vector">Vector</option>
            </select>
          </label>
          <label className="field">
            <span>Dataset split</span>
            <select value={split} onChange={(e) => setSplit(e.target.value)}>
              <option value="development">Development</option>
              <option value="held_out">Held out</option>
              <option value="all">All reviewed labels</option>
            </select>
          </label>
          <div className="grid-2">
            <Field
              label="Passages to retrieve"
              type="number"
              min={1}
              max={10}
              value={topK}
              onChange={(e) => setTopK(Number(e.target.value))}
            />
            <Field
              label="Chunk size"
              type="number"
              min={300}
              max={2000}
              value={chunkSize}
              onChange={(e) => setChunkSize(Number(e.target.value))}
            />
          </div>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={rerank}
              onChange={(e) => setRerank(e.target.checked)}
            />
            Use optional reranker
          </label>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={judge}
              onChange={(e) => setJudge(e.target.checked)}
            />
            Include uncalibrated local judge
          </label>
          <Button busy={busy}>Queue evaluation</Button>
        </form>
      </Modal>
      <Modal
        open={!!runDetail}
        onOpenChange={(v) => !v && setRunDetail(null)}
        title="Evaluation result"
        description="Per-question retrieval results from this recorded run."
        wide
      >
        {runDetail && (
          <>
            <KeyValues
              values={{
                ...runDetail.config,
                ...runDetail.metrics,
                quality_basis: runDetail.manifest.quality_basis,
                judge_calibrated: runDetail.manifest.judge_calibrated,
              }}
            />
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Question</th>
                    <th>Recall@5</th>
                    <th>MRR</th>
                    <th>nDCG@5</th>
                  </tr>
                </thead>
                <tbody>
                  {runDetail.results.map((r) => (
                    <tr key={r.label_id}>
                      <td>{r.question}</td>
                      <td>{r.recall_at_5?.toFixed(3)}</td>
                      <td>{r.mrr?.toFixed(3)}</td>
                      <td>{r.ndcg_at_5?.toFixed(3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Modal>
      <Modal
        open={!!comparison}
        onOpenChange={(v) => !v && setComparison(null)}
        title="Run comparison"
        description="Metric differences are comparison minus baseline."
        wide
      >
        {comparison && (
          <>
            <Notice>
              {comparison.comparable
                ? "These runs have comparable evaluation inputs."
                : "These runs differ in evaluation inputs; do not treat differences as a controlled improvement."}
            </Notice>
            {comparison.reasons?.map((reason) => (
              <p key={reason} className="muted">
                {reason}
              </p>
            ))}
            <KeyValues values={comparison.deltas} />
            <h3>Per-question recall regressions</h3>
            {comparison.regressions?.length ? (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Question</th>
                      <th>Baseline recall@5</th>
                      <th>Comparison recall@5</th>
                    </tr>
                  </thead>
                  <tbody>
                    {comparison.regressions.map((row) => (
                      <tr key={row.label_id}>
                        <td>{row.question}</td>
                        <td>{row.before.toFixed(3)}</td>
                        <td>{row.after.toFixed(3)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted">
                No per-question recall regressions were recorded for matching
                labels.
              </p>
            )}
          </>
        )}
      </Modal>
      <Modal
        open={!!candidate}
        onOpenChange={(v) => !v && setCandidate(null)}
        title="Create an unreviewed candidate"
        description="Choose supporting evidence. This action never approves a label."
        wide
      >
        <form
          className="form-stack"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await api(`/administration/feedback/${candidate?.id}/candidate`, {
                tenantId,
                method: "POST",
                body: json({
                  document_id: candidateDocument,
                  start_offset: start,
                  end_offset: end,
                  question,
                }),
              });
              setCandidate(null);
              setMessage("Unreviewed evaluation candidate created.");
            });
          }}
        >
          <ErrorNotice error={error} />
          <label className="field">
            <span>Question</span>
            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              minLength={5}
              required
            />
          </label>
          <label className="field">
            <span>Supporting document</span>
            <select
              value={candidateDocument}
              onChange={(e) => setCandidateDocument(e.target.value)}
              required
            >
              <option value="">Choose a document</option>
              {documents.data?.items.map((d) => (
                <option value={d.id} key={d.id}>
                  {d.title}
                </option>
              ))}
            </select>
          </label>
          <div className="grid-2">
            <Field
              label="Evidence start"
              type="number"
              min={0}
              value={start}
              onChange={(e) => setStart(Number(e.target.value))}
            />
            <Field
              label="Evidence end"
              type="number"
              min={1}
              value={end}
              onChange={(e) => setEnd(Number(e.target.value))}
            />
          </div>
          {candidateSource.data && (
            <>
              <blockquote className="evidence-quote">
                {Array.from(candidateSource.data.content)
                  .slice(start, end)
                  .join("")}
              </blockquote>
              <details>
                <summary>Full source</summary>
                <pre className="source-text">
                  {candidateSource.data.content}
                </pre>
              </details>
            </>
          )}
          <ErrorNotice error={candidateSource.error} />
          <Button busy={busy} disabled={!candidateDocument || end <= start}>
            Create unreviewed candidate
          </Button>
        </form>
      </Modal>
    </>
  );
}
