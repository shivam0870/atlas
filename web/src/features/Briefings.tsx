import { useState } from "react";
import { Link } from "react-router-dom";
import { Newspaper, Plus, Play, Pause } from "lucide-react";
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
  useDocumentOptions,
  useWorkbenchActions,
  useWorkbenchQuery,
  type SpaceOption,
} from "./WorkbenchShared";
type Briefing = {
  id: string;
  name: string;
  space_ids: string[];
  document_ids: string[];
  question: string;
  cadence: "daily" | "weekly";
  enabled: boolean;
  last_run_at?: string;
  next_run_at?: string;
  last_error?: string;
};
type BriefingRun = {
  id: string;
  status: string;
  summary: string;
  items: {
    document_id: string;
    version_id: string;
    title: string;
    number: number;
    excerpt: string;
    changed_at: string;
  }[];
  created_at: string;
  error_code?: string;
};
export function BriefingsPage() {
  const { tenantId } = useWorkspace();
  const actions = useWorkbenchActions();
  const list = useWorkbenchQuery<{ items: Briefing[] }>(
    "briefings",
    "/workbench/briefings",
    true,
    true,
  );
  const spaces = useWorkbenchQuery<SpaceOption[]>("spaces", "/spaces");
  const documents = useDocumentOptions();
  const [edit, setEdit] = useState<Briefing | null>(null);
  const [selected, setSelected] = useState<Briefing | null>(null);
  const [remove, setRemove] = useState<Briefing | null>(null);
  const runs = useWorkbenchQuery<{ items: BriefingRun[] }>(
    "briefing-runs",
    `/workbench/briefings/${selected?.id}/runs`,
    !!selected,
    true,
  );
  function create() {
    setEdit({
      id: "",
      name: "",
      question: "",
      space_ids: [],
      document_ids: [],
      cadence: "weekly",
      enabled: true,
    });
    actions.setError(null);
  }
  return (
    <>
      <PageHeader
        title="Briefings"
        description="Follow the knowledge that matters to you and review changes in a daily or weekly digest."
        action={
          <Button onClick={create}>
            <Plus size={16} />
            Create briefing
          </Button>
        }
      />
      <ErrorNotice
        error={actions.error || list.error || spaces.error || documents.error}
      />
      {actions.message && <Notice>{actions.message}</Notice>}
      {list.isPending ? (
        <Loading />
      ) : list.data?.items.length === 0 ? (
        <EmptyState
          icon={<Newspaper />}
          title="Stay current without searching again"
          description="Follow selected documents or spaces and add a question to focus your briefing. Results appear here and in your notification inbox."
          action={<Button onClick={create}>Create briefing</Button>}
        />
      ) : (
        <div className="card-grid">
          {list.data?.items.map((briefing) => (
            <section className="panel workbench-card" key={briefing.id}>
              <div className="row between">
                <Badge>{briefing.cadence}</Badge>
                <Badge tone={briefing.enabled ? "success" : "neutral"}>
                  {briefing.enabled ? "Scheduled" : "Paused"}
                </Badge>
              </div>
              <h2>{briefing.name}</h2>
              <p className="muted">
                {briefing.question ||
                  "Changes to your selected knowledge sources."}
              </p>
              <dl className="workbench-facts">
                <div>
                  <dt>Following</dt>
                  <dd>
                    {briefing.space_ids.length} spaces ·{" "}
                    {briefing.document_ids.length} documents
                  </dd>
                </div>
                <div>
                  <dt>Last run</dt>
                  <dd>{formatDate(briefing.last_run_at)}</dd>
                </div>
                <div>
                  <dt>Next run</dt>
                  <dd>
                    {briefing.enabled
                      ? formatDate(briefing.next_run_at)
                      : "Paused"}
                  </dd>
                </div>
              </dl>
              {briefing.last_error && (
                <div className="notice" role="status">
                  Latest run: {briefing.last_error}. Edit and save this briefing
                  to renew its authorization after signing in.
                </div>
              )}
              <div className="workbench-actions">
                <Button
                  variant="secondary"
                  onClick={() => setSelected(briefing)}
                >
                  Read briefings
                </Button>
                <Button
                  busy={actions.busy}
                  onClick={() =>
                    actions.run(async (signal) => {
                      const run = await api<BriefingRun>(
                        `/workbench/briefings/${briefing.id}/run`,
                        { tenantId, method: "POST", signal },
                      );
                      setSelected(briefing);
                      actions.setMessage(`Briefing ${run.status}.`);
                    })
                  }
                >
                  Run now
                </Button>
                <Button
                  variant="ghost"
                  busy={actions.busy}
                  onClick={() =>
                    actions.run(async (signal) => {
                      await api(`/workbench/briefings/${briefing.id}`, {
                        tenantId,
                        method: "PATCH",
                        signal,
                        body: json({ enabled: !briefing.enabled }),
                      });
                    })
                  }
                >
                  {briefing.enabled ? <Pause size={15} /> : <Play size={15} />}
                  {briefing.enabled ? "Pause" : "Resume"}
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => {
                    setEdit(briefing);
                    actions.setError(null);
                  }}
                >
                  Edit
                </Button>
                <Button variant="ghost" onClick={() => setRemove(briefing)}>
                  Delete
                </Button>
              </div>
            </section>
          ))}
        </div>
      )}
      <Modal
        open={!!edit}
        onOpenChange={(open) => !open && setEdit(null)}
        title={edit?.id ? "Edit briefing" : "Create briefing"}
        description="Briefings use your current access. A schedule pauses when its authorization expires; sign in and save it again to resume."
        wide
      >
        {edit && (
          <form
            className="form-stack"
            onSubmit={(event) => {
              event.preventDefault();
              void actions.run(async (signal) => {
                await api(
                  edit.id
                    ? `/workbench/briefings/${edit.id}`
                    : "/workbench/briefings",
                  {
                    tenantId,
                    method: edit.id ? "PATCH" : "POST",
                    signal,
                    body: json({
                      name: edit.name,
                      question: edit.question,
                      cadence: edit.cadence,
                      enabled: edit.enabled,
                      space_ids: edit.space_ids,
                      document_ids: edit.document_ids,
                    }),
                  },
                );
                setEdit(null);
                actions.setMessage("Briefing saved.");
              });
            }}
          >
            <ErrorNotice error={actions.error} />
            <Field
              label="Briefing name"
              value={edit.name}
              onChange={(event) =>
                setEdit({ ...edit, name: event.target.value })
              }
              required
              maxLength={160}
            />
            <label className="field">
              <span>Question or focus (optional)</span>
              <textarea
                value={edit.question}
                onChange={(event) =>
                  setEdit({ ...edit, question: event.target.value })
                }
                maxLength={2000}
                placeholder="What changed in our deployment policies?"
              />
            </label>
            <SelectField
              label="Frequency"
              value={edit.cadence}
              onChange={(cadence) =>
                setEdit({ ...edit, cadence: cadence as Briefing["cadence"] })
              }
            >
              <option value="daily">Daily</option>
              <option value="weekly">Weekly</option>
            </SelectField>
            <div className="workbench-columns">
              <fieldset className="workbench-picker">
                <legend>Follow spaces</legend>
                {spaces.data?.map((space) => (
                  <label className="checkbox-row" key={space.id}>
                    <input
                      type="checkbox"
                      checked={edit.space_ids.includes(space.id)}
                      onChange={(event) =>
                        setEdit({
                          ...edit,
                          space_ids: event.target.checked
                            ? [...edit.space_ids, space.id]
                            : edit.space_ids.filter((id) => id !== space.id),
                        })
                      }
                    />
                    {space.name}
                  </label>
                ))}
                {spaces.data?.length === 0 && (
                  <p className="muted">No accessible spaces.</p>
                )}
              </fieldset>
              <fieldset className="workbench-picker">
                <legend>Follow documents</legend>
                {documents.data?.items.map((document) => (
                  <label className="checkbox-row" key={document.id}>
                    <input
                      type="checkbox"
                      checked={edit.document_ids.includes(document.id)}
                      onChange={(event) =>
                        setEdit({
                          ...edit,
                          document_ids: event.target.checked
                            ? [...edit.document_ids, document.id]
                            : edit.document_ids.filter(
                                (id) => id !== document.id,
                              ),
                        })
                      }
                    />
                    {document.title}
                  </label>
                ))}
                {documents.data?.items.length === 0 && (
                  <p className="muted">No accessible documents.</p>
                )}
              </fieldset>
            </div>
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={edit.enabled}
                onChange={(event) =>
                  setEdit({ ...edit, enabled: event.target.checked })
                }
              />
              Enable schedule
            </label>
            <Button
              busy={actions.busy}
              disabled={!edit.space_ids.length && !edit.document_ids.length}
            >
              Save briefing
            </Button>
          </form>
        )}
      </Modal>
      <Modal
        open={!!selected}
        onOpenChange={(open) => !open && setSelected(null)}
        title={selected?.name || "Briefing history"}
        description="Each briefing links to the exact document version behind its source excerpts."
        wide
      >
        <ErrorNotice error={runs.error || actions.error} />
        {runs.isPending ? (
          <Loading />
        ) : runs.data?.items.length === 0 ? (
          <p className="muted">
            No briefings yet. Choose Run now to create the first one.
          </p>
        ) : (
          runs.data?.items.map((run) => (
            <article className="panel" key={run.id}>
              <div className="row between">
                <h2>{formatDate(run.created_at)}</h2>
                <Badge>{run.status}</Badge>
              </div>
              {run.error_code && (
                <div className="notice" role="status">
                  Run could not complete: {run.error_code}
                </div>
              )}
              <p className="source-passage">{run.summary}</p>
              {run.items?.map((item) => (
                <section
                  className="briefing-source"
                  key={`${item.document_id}-${item.version_id}`}
                >
                  <div className="row between">
                    <Link
                      className="text-link"
                      to={`/o/${tenantId}/library/${item.document_id}?version=${item.version_id}`}
                    >
                      {item.title} · Version {item.number}
                    </Link>
                    <small className="muted">
                      {formatDate(item.changed_at)}
                    </small>
                  </div>
                  <blockquote className="source-passage">
                    {item.excerpt}
                  </blockquote>
                </section>
              ))}
            </article>
          ))
        )}
      </Modal>
      <Modal
        open={!!remove}
        onOpenChange={(open) => !open && setRemove(null)}
        title="Delete briefing"
        description={`Delete ${remove?.name || "this briefing"}, its schedule, and its run history?`}
      >
        <ErrorNotice error={actions.error} />
        <div className="workbench-actions">
          <Button variant="secondary" onClick={() => setRemove(null)}>
            Keep briefing
          </Button>
          <Button
            variant="danger"
            busy={actions.busy}
            onClick={() =>
              actions.run(async (signal) => {
                await api(`/workbench/briefings/${remove?.id}`, {
                  tenantId,
                  method: "DELETE",
                  signal,
                });
                setRemove(null);
              })
            }
          >
            Delete briefing
          </Button>
        </div>
      </Modal>
    </>
  );
}
