import { useState } from "react";
import { Link } from "react-router-dom";
import { CheckSquare, Plus, Sparkles, Trash2 } from "lucide-react";
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
  VersionSelect,
  type SpaceOption,
} from "./WorkbenchShared";
type Step = {
  id: string;
  title: string;
  instructions: string;
  document_id?: string | null;
  version_id?: string | null;
  assignee_user_id?: string | null;
  requires_approval: boolean;
  completed?: boolean;
  approved?: boolean;
  note?: string;
};
type Playbook = {
  id: string;
  name: string;
  description: string;
  space_id: string;
  status: "draft" | "published";
  steps: Step[];
  needs_review?: boolean;
  updated_at?: string;
};
type PlaybookRun = {
  id: string;
  name?: string;
  status: string;
  created_at: string;
  steps: Step[];
  needs_review?: boolean;
};
const newStep = (): Step => ({
  id: crypto.randomUUID(),
  title: "",
  instructions: "",
  requires_approval: false,
});
export function PlaybooksPage() {
  const { tenantId, canEdit, canManage } = useWorkspace();
  const actions = useWorkbenchActions();
  const list = useWorkbenchQuery<{ items: Playbook[] }>(
    "playbooks",
    "/workbench/playbooks",
  );
  const spaces = useWorkbenchQuery<SpaceOption[]>("spaces", "/spaces");
  const documents = useDocumentOptions();
  const [edit, setEdit] = useState<Playbook | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [selected, setSelected] = useState<Playbook | null>(null);
  const [runId, setRunId] = useState("");
  const [remove, setRemove] = useState<Playbook | null>(null);
  const [draftOpen, setDraftOpen] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [draftSpace, setDraftSpace] = useState("");
  const [draftDocs, setDraftDocs] = useState<string[]>([]);
  const members = useWorkbenchQuery<{
    items: { user_id: string; name: string; status: string }[];
  }>("playbook-members", `/organizations/${tenantId}/members`, !!edit);
  const runs = useWorkbenchQuery<{ items: PlaybookRun[] }>(
    "playbook-runs",
    `/workbench/playbooks/${selected?.id}/runs`,
    !!selected,
  );
  const detail = useWorkbenchQuery<PlaybookRun>(
    "playbook-run",
    `/workbench/playbook-runs/${runId}`,
    !!runId,
  );
  function create() {
    setIsNew(true);
    setEdit({
      id: "",
      name: "",
      description: "",
      space_id: spaces.data?.[0]?.id || "",
      status: "draft",
      steps: [newStep()],
    });
    actions.setError(null);
  }
  function changeStep(index: number, patch: Partial<Step>) {
    if (edit)
      setEdit({
        ...edit,
        steps: edit.steps.map((step, i) =>
          i === index ? { ...step, ...patch } : step,
        ),
      });
  }
  async function openEdit(book: Playbook, signal: AbortSignal) {
    const result = await api<Playbook>(`/workbench/playbooks/${book.id}`, {
      tenantId,
      signal,
    });
    if (!signal.aborted) {
      setIsNew(false);
      setEdit(result);
    }
  }
  return (
    <>
      <PageHeader
        title="Playbooks"
        description="Turn approved knowledge into repeatable work, with versioned evidence and progress you can track."
        action={
          canEdit && (
            <div className="workbench-actions">
              <Button
                variant="secondary"
                onClick={() => {
                  setDraftOpen(true);
                  setDraftSpace(spaces.data?.[0]?.id || "");
                }}
              >
                <Sparkles size={16} />
                Draft from documents
              </Button>
              <Button onClick={create}>
                <Plus size={16} />
                Create playbook
              </Button>
            </div>
          )
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
          icon={<CheckSquare />}
          title="A clear path through the work"
          description="Create an onboarding, deployment, or incident checklist. Attach exact source versions so every step can be checked."
          action={canEdit && <Button onClick={create}>Create playbook</Button>}
        />
      ) : (
        <div className="card-grid">
          {list.data?.items.map((book) => (
            <section key={book.id} className="panel workbench-card">
              <div className="row between">
                <Badge>{book.status}</Badge>
                {book.needs_review && (
                  <Badge tone="warning">Sources changed · review needed</Badge>
                )}
              </div>
              <h2>{book.name}</h2>
              <p className="muted">
                {book.description ||
                  "A guided checklist with source references."}
              </p>
              <p className="muted">
                {book.steps?.length ?? "—"} steps · Updated{" "}
                {formatDate(book.updated_at)}
              </p>
              <div className="workbench-actions">
                <Button
                  variant="secondary"
                  onClick={() => {
                    setSelected(book);
                    setRunId("");
                  }}
                >
                  View runs
                </Button>
                <Button
                  disabled={
                    book.status !== "published" ||
                    !!book.needs_review ||
                    actions.busy
                  }
                  onClick={() =>
                    actions.run(async (signal) => {
                      const run = await api<PlaybookRun>(
                        `/workbench/playbooks/${book.id}/runs`,
                        { tenantId, method: "POST", signal, body: json({}) },
                      );
                      setSelected(book);
                      setRunId(run.id);
                    })
                  }
                >
                  Start checklist
                </Button>
                {canEdit && (
                  <>
                    <Button
                      variant="ghost"
                      busy={actions.busy}
                      onClick={() =>
                        actions.run((signal) => openEdit(book, signal))
                      }
                    >
                      Edit
                    </Button>
                    <Button variant="ghost" onClick={() => setRemove(book)}>
                      Delete
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
        title={isNew ? "Create playbook" : "Edit playbook"}
        description="Review each step and its evidence before publishing. Publishing makes this checklist available to members who can access its space."
        wide
      >
        {edit && (
          <form
            className="form-stack"
            onSubmit={(event) => {
              event.preventDefault();
              void actions.run(async (signal) => {
                await api(
                  isNew
                    ? "/workbench/playbooks"
                    : `/workbench/playbooks/${edit.id}`,
                  {
                    tenantId,
                    method: isNew ? "POST" : "PATCH",
                    signal,
                    body: json({
                      name: edit.name,
                      description: edit.description,
                      space_id: edit.space_id,
                      status: edit.status,
                      steps: edit.steps.map((step) => ({
                        id: step.id,
                        title: step.title,
                        instructions: step.instructions,
                        document_id: step.document_id || null,
                        version_id: step.version_id || null,
                        assignee_user_id: step.assignee_user_id || null,
                        requires_approval: step.requires_approval,
                      })),
                    }),
                  },
                );
                setEdit(null);
                actions.setMessage("Playbook saved.");
              });
            }}
          >
            <ErrorNotice error={actions.error || members.error} />
            <Field
              label="Playbook name"
              value={edit.name}
              onChange={(event) =>
                setEdit({ ...edit, name: event.target.value })
              }
              required
              maxLength={160}
            />
            <Field
              label="Description"
              value={edit.description}
              onChange={(event) =>
                setEdit({ ...edit, description: event.target.value })
              }
              maxLength={2000}
            />
            <div className="workbench-columns">
              <SelectField
                label="Knowledge space"
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
              <SelectField
                label="Publication status"
                value={edit.status}
                onChange={(status) =>
                  setEdit({ ...edit, status: status as Playbook["status"] })
                }
              >
                <option value="draft">Draft</option>
                <option value="published">Published</option>
              </SelectField>
            </div>
            {edit.needs_review && (
              <div className="notice" role="status">
                One or more source versions have changed. Review and update
                their references before republishing.
              </div>
            )}
            {edit.steps.map((step, index) => (
              <fieldset className="playbook-step form-stack" key={step.id}>
                <legend>Step {index + 1}</legend>
                <Field
                  label={`Step ${index + 1} title`}
                  value={step.title}
                  onChange={(event) =>
                    changeStep(index, { title: event.target.value })
                  }
                  required
                  maxLength={200}
                />
                <label className="field">
                  <span>Instructions</span>
                  <textarea
                    value={step.instructions}
                    onChange={(event) =>
                      changeStep(index, { instructions: event.target.value })
                    }
                    maxLength={10000}
                  />
                </label>
                <div className="workbench-columns">
                  <SelectField
                    label="Source document"
                    value={step.document_id || ""}
                    onChange={(document_id) =>
                      changeStep(index, { document_id, version_id: "" })
                    }
                  >
                    <option value="">No source document</option>
                    {documents.data?.items.map((document) => (
                      <option key={document.id} value={document.id}>
                        {document.title}
                      </option>
                    ))}
                  </SelectField>
                  <VersionSelect
                    documentId={step.document_id || ""}
                    value={step.version_id || ""}
                    onChange={(version_id) => changeStep(index, { version_id })}
                    required={!!step.document_id}
                  />
                </div>
                <SelectField
                  label="Assigned to"
                  value={step.assignee_user_id || ""}
                  onChange={(assignee_user_id) =>
                    changeStep(index, { assignee_user_id })
                  }
                >
                  <option value="">Checklist participant</option>
                  {members.data?.items
                    .filter((member) => member.status === "active")
                    .map((member) => (
                      <option key={member.user_id} value={member.user_id}>
                        {member.name}
                      </option>
                    ))}
                </SelectField>
                <label className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={step.requires_approval}
                    onChange={(event) =>
                      changeStep(index, {
                        requires_approval: event.target.checked,
                      })
                    }
                  />
                  Require administrator approval
                </label>
                <Button
                  type="button"
                  variant="ghost"
                  disabled={edit.steps.length <= 1}
                  onClick={() =>
                    setEdit({
                      ...edit,
                      steps: edit.steps.filter((_, i) => i !== index),
                    })
                  }
                >
                  <Trash2 size={15} />
                  Remove step {index + 1}
                </Button>
              </fieldset>
            ))}
            <div className="workbench-actions">
              <Button
                type="button"
                variant="secondary"
                onClick={() =>
                  setEdit({ ...edit, steps: [...edit.steps, newStep()] })
                }
              >
                Add step
              </Button>
              <Button busy={actions.busy}>Save playbook</Button>
            </div>
          </form>
        )}
      </Modal>
      <Modal
        open={draftOpen}
        onOpenChange={setDraftOpen}
        title="Draft from documents"
        description="Atlas proposes a checklist from the selected sources. Review and edit the result before saving or publishing it."
        wide
      >
        <form
          className="form-stack"
          onSubmit={(event) => {
            event.preventDefault();
            void actions.run(async (signal) => {
              const result = await api<Omit<Playbook, "id">>(
                "/workbench/playbooks/draft",
                {
                  tenantId,
                  method: "POST",
                  signal,
                  body: json({
                    name: draftName,
                    space_id: draftSpace,
                    document_ids: draftDocs,
                  }),
                },
              );
              if (!signal.aborted) {
                setEdit({
                  ...result,
                  id: "",
                  status: "draft",
                  steps: result.steps.map((step) => ({
                    ...step,
                    id: step.id || crypto.randomUUID(),
                  })),
                });
                setIsNew(true);
                setDraftOpen(false);
              }
            });
          }}
        >
          <ErrorNotice error={actions.error} />
          <Field
            label="Draft name"
            value={draftName}
            onChange={(event) => setDraftName(event.target.value)}
            required
            maxLength={160}
          />
          <SelectField
            label="Destination space"
            value={draftSpace}
            onChange={setDraftSpace}
            required
          >
            <option value="">Choose a space</option>
            {spaces.data?.map((space) => (
              <option key={space.id} value={space.id}>
                {space.name}
              </option>
            ))}
          </SelectField>
          <fieldset className="workbench-picker">
            <legend>Source documents</legend>
            {documents.data?.items.map((document) => (
              <label className="checkbox-row" key={document.id}>
                <input
                  type="checkbox"
                  checked={draftDocs.includes(document.id)}
                  onChange={(event) =>
                    setDraftDocs(
                      event.target.checked
                        ? [...draftDocs, document.id]
                        : draftDocs.filter((id) => id !== document.id),
                    )
                  }
                />
                {document.title}
              </label>
            ))}
            {!documents.data?.items.length && (
              <p className="muted">
                Add documents to Library before creating a draft.
              </p>
            )}
          </fieldset>
          <Button busy={actions.busy} disabled={!draftDocs.length}>
            Prepare draft for review
          </Button>
        </form>
      </Modal>
      <Modal
        open={!!selected}
        onOpenChange={(open) => {
          if (!open) {
            setSelected(null);
            setRunId("");
          }
        }}
        title={selected?.name || "Checklist runs"}
        description="Runs record your progress against a saved version of the playbook."
        wide
      >
        <ErrorNotice error={runs.error || detail.error || actions.error} />
        {runs.isPending ? (
          <Loading />
        ) : (
          <>
            <SelectField
              label="Checklist run"
              value={runId}
              onChange={setRunId}
            >
              <option value="">Choose a run</option>
              {runs.data?.items.map((run) => (
                <option key={run.id} value={run.id}>
                  {run.name || formatDate(run.created_at)} · {run.status}
                </option>
              ))}
            </SelectField>
            {runs.data?.items.length === 0 && (
              <p className="muted">
                No runs yet. Start a published checklist to record progress.
              </p>
            )}
          </>
        )}
        {runId &&
          (detail.isPending ? (
            <Loading />
          ) : (
            detail.data && (
              <div className="form-stack">
                <div className="workbench-summary">
                  <strong>
                    {
                      detail.data.steps.filter(
                        (step) =>
                          step.completed &&
                          (!step.requires_approval || step.approved),
                      ).length
                    }{" "}
                    / {detail.data.steps.length} steps complete
                  </strong>
                  <Badge>{detail.data.status}</Badge>
                </div>
                {detail.data.needs_review && (
                  <div className="notice" role="status">
                    Source documents changed after this checklist was created.
                    Review the evidence before continuing.
                  </div>
                )}
                {detail.data.steps.map((step, index) => (
                  <section className="playbook-step" key={step.id}>
                    <h3>
                      {index + 1}. {step.title}
                    </h3>
                    <p className="source-passage">{step.instructions}</p>
                    {step.document_id && (
                      <Link
                        className="text-link"
                        to={`/o/${tenantId}/library/${step.document_id}${step.version_id ? `?version=${step.version_id}` : ""}`}
                      >
                        Open source version
                      </Link>
                    )}
                    <div className="workbench-actions">
                      <label className="checkbox-row">
                        <input
                          type="checkbox"
                          checked={!!step.completed}
                          disabled={actions.busy}
                          onChange={(event) => {
                            const completed = event.target.checked;
                            void actions.run(async (signal) => {
                              await api(
                                `/workbench/playbook-runs/${runId}/steps/${step.id}`,
                                {
                                  tenantId,
                                  method: "PATCH",
                                  signal,
                                  body: json({ completed }),
                                },
                              );
                            });
                          }}
                        />
                        Completed
                      </label>
                      {step.requires_approval && (
                        <>
                          <Badge>
                            {step.approved ? "Approved" : "Approval required"}
                          </Badge>
                          {canManage && (
                            <Button
                              variant="secondary"
                              busy={actions.busy}
                              disabled={!step.completed}
                              onClick={() =>
                                actions.run(async (signal) => {
                                  await api(
                                    `/workbench/playbook-runs/${runId}/steps/${step.id}`,
                                    {
                                      tenantId,
                                      method: "PATCH",
                                      signal,
                                      body: json({ approved: !step.approved }),
                                    },
                                  );
                                })
                              }
                            >
                              {step.approved
                                ? "Withdraw approval"
                                : "Approve step"}
                            </Button>
                          )}
                        </>
                      )}
                    </div>
                  </section>
                ))}
              </div>
            )
          ))}
      </Modal>
      <Modal
        open={!!remove}
        onOpenChange={(open) => !open && setRemove(null)}
        title="Delete playbook"
        description={`Delete ${remove?.name || "this playbook"} and its checklists? This cannot be undone.`}
      >
        <ErrorNotice error={actions.error} />
        <div className="workbench-actions">
          <Button variant="secondary" onClick={() => setRemove(null)}>
            Keep playbook
          </Button>
          <Button
            variant="danger"
            busy={actions.busy}
            onClick={() =>
              actions.run(async (signal) => {
                await api(`/workbench/playbooks/${remove?.id}`, {
                  tenantId,
                  method: "DELETE",
                  signal,
                });
                setRemove(null);
              })
            }
          >
            Delete playbook
          </Button>
        </div>
      </Modal>
    </>
  );
}
