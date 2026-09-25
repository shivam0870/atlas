import { useState } from "react";
import { Link } from "react-router-dom";
import { FolderSync, Plus, RefreshCw } from "lucide-react";
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
  formatDate,
  SelectField,
  useWorkbenchActions,
  useWorkbenchQuery,
  type SpaceOption,
} from "./WorkbenchShared";
type Source = {
  id: string;
  name: string;
  kind: "github" | "folder";
  location: string;
  space_id: string;
  include_patterns: string[];
  interval_hours: number;
  enabled: boolean;
  last_synced_at?: string;
  next_run_at?: string;
  status?: string;
};
type SourceRun = {
  id: string;
  status: string;
  imported: number;
  unchanged: number;
  archived: number;
  error_code?: string;
  created_at?: string;
  completed_at?: string;
};
const blank: Omit<Source, "id"> = {
  name: "",
  kind: "github",
  location: "",
  space_id: "",
  include_patterns: ["**/*.md"],
  interval_hours: 24,
  enabled: true,
};
export function SourcesPage() {
  const { tenantId, canManage } = useWorkspace();
  const actions = useWorkbenchActions();
  const list = useWorkbenchQuery<{ items: Source[] }>(
    "sources",
    "/workbench/sources",
    true,
    true,
  );
  const spaces = useWorkbenchQuery<SpaceOption[]>("spaces", "/spaces");
  const [edit, setEdit] = useState<Source | Omit<Source, "id"> | null>(null);
  const [patterns, setPatterns] = useState("**/*.md");
  const [selected, setSelected] = useState<Source | null>(null);
  const [remove, setRemove] = useState<Source | null>(null);
  const [preview, setPreview] = useState<Source | null>(null);
  const previewFiles = useWorkbenchQuery<{
    items: { path: string; byte_size: number }[];
    total_bytes: number;
  }>("source-preview", `/workbench/sources/${preview?.id}/preview`, !!preview);
  const runs = useWorkbenchQuery<{ items: SourceRun[] }>(
    "source-runs",
    `/workbench/sources/${selected?.id}/runs`,
    !!selected,
    true,
  );
  function openEdit(source?: Source) {
    setEdit(source || { ...blank, space_id: spaces.data?.[0]?.id || "" });
    setPatterns(
      (source?.include_patterns || blank.include_patterns).join("\n"),
    );
    actions.setError(null);
  }
  return (
    <>
      <PageHeader
        title="Sources"
        description="Keep approved repositories and folders synchronized with your knowledge spaces."
        action={
          canManage && (
            <Button onClick={() => openEdit()}>
              <Plus size={16} />
              Connect source
            </Button>
          )
        }
      />
      <ErrorNotice error={actions.error || list.error || spaces.error} />
      {actions.message && <Notice>{actions.message}</Notice>}
      {list.isPending ? (
        <Loading />
      ) : list.data?.items.length === 0 ? (
        <EmptyState
          icon={<FolderSync />}
          title="Keep your knowledge up to date"
          description="Connect a public GitHub repository or a folder approved by your platform operator. Imported files follow the document safety and indexing pipeline."
          action={
            canManage && (
              <Button onClick={() => openEdit()}>Connect source</Button>
            )
          }
        />
      ) : (
        <div className="card-grid">
          {list.data?.items.map((source) => (
            <section className="panel workbench-card" key={source.id}>
              <div className="row between">
                <Badge>
                  {source.kind === "github" ? "GitHub" : "Approved folder"}
                </Badge>
                <Badge tone={source.enabled ? "success" : "neutral"}>
                  {source.enabled ? "Active" : "Paused"}
                </Badge>
              </div>
              <h2>{source.name}</h2>
              <p className="muted break-word">{source.location}</p>
              <dl className="workbench-facts">
                <div>
                  <dt>Destination</dt>
                  <dd>
                    <Link
                      to={`/o/${tenantId}/library?space=${source.space_id}`}
                    >
                      {spaces.data?.find(
                        (space) => space.id === source.space_id,
                      )?.name || "Knowledge space"}
                    </Link>
                  </dd>
                </div>
                <div>
                  <dt>Sync frequency</dt>
                  <dd>Every {source.interval_hours} hours</dd>
                </div>
                <div>
                  <dt>Last synchronized</dt>
                  <dd>{formatDate(source.last_synced_at)}</dd>
                </div>
              </dl>
              <div className="workbench-actions">
                <Button variant="secondary" onClick={() => setPreview(source)}>
                  Preview files
                </Button>
                <Button variant="secondary" onClick={() => setSelected(source)}>
                  Sync history
                </Button>
                {canManage && (
                  <>
                    <Button
                      variant="secondary"
                      busy={actions.busy}
                      onClick={() =>
                        actions.run(async (signal) => {
                          const result = await api<SourceRun>(
                            `/workbench/sources/${source.id}/sync`,
                            { tenantId, method: "POST", signal },
                          );
                          actions.setMessage(
                            `Synchronization ${result.status}.`,
                          );
                          setSelected(source);
                        })
                      }
                    >
                      <RefreshCw size={15} />
                      Sync now
                    </Button>
                    <Button variant="ghost" onClick={() => openEdit(source)}>
                      Edit
                    </Button>
                    <Button variant="ghost" onClick={() => setRemove(source)}>
                      Disconnect
                    </Button>
                  </>
                )}
              </div>
            </section>
          ))}
        </div>
      )}
      <Modal
        open={!!edit}
        onOpenChange={(open) => !open && setEdit(null)}
        title={edit && "id" in edit ? "Edit source" : "Connect source"}
        description="The platform operator controls which repositories and folders Atlas can read."
      >
        {edit && (
          <form
            className="form-stack"
            onSubmit={(event) => {
              event.preventDefault();
              void actions.run(async (signal) => {
                await api(
                  "id" in edit
                    ? `/workbench/sources/${edit.id}`
                    : "/workbench/sources",
                  {
                    tenantId,
                    method: "id" in edit ? "PATCH" : "POST",
                    signal,
                    body: json({
                      name: edit.name,
                      kind: edit.kind,
                      location: edit.location,
                      space_id: edit.space_id,
                      interval_hours: edit.interval_hours,
                      enabled: edit.enabled,
                      include_patterns: patterns
                        .split("\n")
                        .map((value) => value.trim())
                        .filter(Boolean),
                    }),
                  },
                );
                setEdit(null);
                actions.setMessage("Source saved.");
              });
            }}
          >
            <ErrorNotice error={actions.error} />
            <Field
              label="Source name"
              value={edit.name}
              onChange={(event) =>
                setEdit({ ...edit, name: event.target.value })
              }
              required
              maxLength={120}
            />
            <SelectField
              label="Source type"
              value={edit.kind}
              onChange={(kind) =>
                setEdit({ ...edit, kind: kind as Source["kind"] })
              }
            >
              <option value="github">GitHub repository</option>
              <option value="folder">Approved folder</option>
            </SelectField>
            <Field
              label={
                edit.kind === "github"
                  ? "Public repository URL"
                  : "Approved folder alias"
              }
              value={edit.location}
              onChange={(event) =>
                setEdit({ ...edit, location: event.target.value })
              }
              placeholder={
                edit.kind === "github"
                  ? "https://github.com/company/docs"
                  : "company-docs"
              }
              required
              maxLength={1000}
              hint={
                edit.kind === "github"
                  ? "Public repositories only. Use a repository approved by your operator. Do not include tokens in the URL."
                  : "Enter the tenant-specific folder alias configured by your operator."
              }
            />
            <SelectField
              label="Destination space"
              value={edit.space_id}
              onChange={(space_id) => setEdit({ ...edit, space_id })}
              required
            >
              <option value="">Choose a space</option>
              {spaces.data?.map((space) => (
                <option key={space.id} value={space.id}>
                  {space.name}
                </option>
              ))}
            </SelectField>
            <label className="field">
              <span>Included paths (one pattern per line)</span>
              <textarea
                value={patterns}
                onChange={(event) => setPatterns(event.target.value)}
                required
                placeholder="**/*.md"
              />
            </label>
            <Field
              label="Sync every (hours)"
              type="number"
              min={1}
              max={168}
              value={edit.interval_hours}
              onChange={(event) =>
                setEdit({ ...edit, interval_hours: Number(event.target.value) })
              }
              required
            />
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={edit.enabled}
                onChange={(event) =>
                  setEdit({ ...edit, enabled: event.target.checked })
                }
              />
              Enable scheduled synchronization
            </label>
            <Button busy={actions.busy}>Save source</Button>
          </form>
        )}
      </Modal>
      <Modal
        open={!!selected}
        onOpenChange={(open) => !open && setSelected(null)}
        title={`${selected?.name || "Source"} · Sync history`}
        description="Review imported, unchanged, and archived documents for each run."
        wide
      >
        <ErrorNotice error={runs.error || actions.error} />
        {runs.isPending ? (
          <Loading />
        ) : runs.data?.items.length === 0 ? (
          <p className="muted">
            No synchronization runs yet. Choose Sync now to import this source.
          </p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Started</th>
                  <th>Status</th>
                  <th>Imported</th>
                  <th>Unchanged</th>
                  <th>Archived</th>
                  <th>Failure</th>
                </tr>
              </thead>
              <tbody>
                {runs.data?.items.map((item) => (
                  <tr key={item.id}>
                    <td>{formatDate(item.created_at)}</td>
                    <td>
                      <Badge>{item.status}</Badge>
                    </td>
                    <td>{item.imported}</td>
                    <td>{item.unchanged}</td>
                    <td>{item.archived}</td>
                    <td>{item.error_code || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Modal>
      <Modal
        open={!!preview}
        onOpenChange={(open) => !open && setPreview(null)}
        title={`${preview?.name || "Source"} · Preview files`}
        description="Inspect the selected paths before synchronization. Files are checked again during import."
        wide
      >
        <ErrorNotice error={previewFiles.error} />
        {previewFiles.isPending ? (
          <Loading />
        ) : (
          previewFiles.data && (
            <>
              <p className="muted">
                {previewFiles.data.items.length} files ·{" "}
                {(previewFiles.data.total_bytes / 1024).toLocaleString(
                  undefined,
                  { maximumFractionDigits: 1 },
                )}{" "}
                KB
              </p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Path</th>
                      <th>Size</th>
                    </tr>
                  </thead>
                  <tbody>
                    {previewFiles.data.items.map((file) => (
                      <tr key={file.path}>
                        <td className="break-word">{file.path}</td>
                        <td>{file.byte_size.toLocaleString()} bytes</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {previewFiles.data.items.length === 0 && (
                <p className="muted">
                  No matching files. Review the included paths in source
                  settings.
                </p>
              )}
            </>
          )
        )}
      </Modal>
      <Modal
        open={!!remove}
        onOpenChange={(open) => !open && setRemove(null)}
        title="Disconnect source"
        description={`Stop synchronization for ${remove?.name || "this source"}. Review imported documents in Library separately.`}
      >
        <ErrorNotice error={actions.error} />
        <div className="workbench-actions">
          <Button variant="secondary" onClick={() => setRemove(null)}>
            Keep source
          </Button>
          <Button
            variant="danger"
            busy={actions.busy}
            onClick={() =>
              actions.run(async (signal) => {
                await api(`/workbench/sources/${remove?.id}`, {
                  tenantId,
                  method: "DELETE",
                  signal,
                });
                setRemove(null);
                actions.setMessage("Source disconnected.");
              })
            }
          >
            Disconnect
          </Button>
        </div>
      </Modal>
    </>
  );
}
