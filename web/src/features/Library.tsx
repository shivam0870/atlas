import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  Check,
  Download,
  FileText,
  FolderPlus,
  Grid2X2,
  List,
  Lock,
  Plus,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { ApiError, api, json } from "../api/client";
import { EffectiveAccess, type EffectivePrincipal } from "./EffectiveAccess";
import { PublicationControls } from "./PublicationControls";
import { useScopedQueryKey, useWorkspace } from "../app/context";
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
type Space = {
  id: string;
  name: string;
  description: string;
  visibility: "company" | "restricted";
  tags: string[];
  owner_user_id: string | null;
  owner_name?: string | null;
};
type Document = {
  id: string;
  title: string;
  content?: string;
  space_id: string | null;
  status: string;
  lifecycle: string;
  media_type: string;
  tags: string[];
  restricted: boolean;
  owner_user_id: string | null;
  review_due_at: string | null;
  updated_at: string;
  pending_status?: string;
  current_version_id: string | null;
  pending_version_id: string | null;
  publication_status?: string;
  effective_at?: string | null;
};
type Segment = { start: number; end: number; page?: number; section?: string };
type Version = {
  id: string;
  number: number;
  title: string;
  content?: string;
  status: string;
  filename: string;
  created_at: string;
  source_segments: Segment[];
  publication_status?: string;
  effective_at?: string | null;
};
type Grant = {
  subject_type: "user" | "team" | "service";
  subject_id: string;
  permission: "read" | "write";
};
type UploadItem = {
  id: number;
  file: File;
  status: string;
  error?: string;
  duplicateId?: string;
};
export function LibraryPage() {
  const { tenantId, user, canEdit, canManage } = useWorkspace();
  const client = useQueryClient();
  const navigate = useNavigate();
  const { documentId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestSpace = searchParams.get("requestSpace");
  const [requestReason, setRequestReason] = useState("");
  const [requestsOpen, setRequestsOpen] = useState(false);
  const requests = useQuery({
    queryKey: useScopedQueryKey("access-requests"),
    queryFn: ({ signal }) =>
      api<
        {
          id: string;
          user_id: string;
          space_id: string;
          reason: string;
          status: string;
        }[]
      >("/access-requests", { tenantId, signal }),
    enabled: canManage,
  });
  const updateFilter = (name: string, value: string, resetPage = true) => {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      if (value) next.set(name, value);
      else next.delete(name);
      if (resetPage) next.delete("page");
      return next;
    });
  };
  const q = searchParams.get("q") || "";
  const setQ = (value: string) => updateFilter("q", value);
  const spaceId = searchParams.get("space") || "";
  const setSpaceId = (value: string) => updateFilter("space", value);
  const lifecycle = searchParams.get("lifecycle") || "active";
  const setLifecycle = (value: string) => updateFilter("lifecycle", value);
  const sort = searchParams.get("sort") || "updated";
  const setSort = (value: string) => updateFilter("sort", value);
  const tag = searchParams.get("tag") || "";
  const setTag = (value: string) => updateFilter("tag", value);
  const status = searchParams.get("status") || "";
  const setStatus = (value: string) => updateFilter("status", value);
  const mediaType = searchParams.get("media_type") || "";
  const setMediaType = (value: string) => updateFilter("media_type", value);
  const ownerId = searchParams.get("owner") || "";
  const setOwnerId = (value: string) => updateFilter("owner", value);
  const updatedSince = searchParams.get("updated_since") || "";
  const setUpdatedSince = (value: string) =>
    updateFilter("updated_since", value);
  const page = Math.max(1, Number(searchParams.get("page")) || 1);
  const setPage = (value: number) => updateFilter("page", String(value), false);
  const [grid, setGrid] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState<unknown>();
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const spaces = useQuery({
    queryKey: useScopedQueryKey("spaces"),
    queryFn: ({ signal }) => api<Space[]>("/spaces", { tenantId, signal }),
  });
  const ownerParameters = new URLSearchParams({
    lifecycle,
    ...(spaceId ? { space_id: spaceId } : {}),
  });
  const owners = useQuery({
    queryKey: useScopedQueryKey("library-owners", ownerParameters.toString()),
    queryFn: ({ signal }) =>
      api<{ items: { user_id: string; name: string }[] }>(
        `/library/owners?${ownerParameters}`,
        { tenantId, signal },
      ),
  });
  const parameters = new URLSearchParams({
    q,
    lifecycle,
    sort,
    page: String(page),
    page_size: "20",
    ...(spaceId ? { space_id: spaceId } : {}),
    ...(tag ? { tag } : {}),
    ...(status ? { status } : {}),
    ...(mediaType ? { media_type: mediaType } : {}),
    ...(ownerId ? { owner_user_id: ownerId } : {}),
    ...(updatedSince
      ? { updated_since: new Date(updatedSince).toISOString() }
      : {}),
  });
  const list = useQuery({
    queryKey: useScopedQueryKey("library", parameters.toString()),
    queryFn: ({ signal }) =>
      api<{ items: Document[]; total: number }>(`/library?${parameters}`, {
        tenantId,
        signal,
      }),
    refetchInterval: (query) =>
      query.state.data?.items.some((item) =>
        ["pending", "indexing"].includes(item.pending_status || item.status),
      )
        ? 3000
        : false,
  });
  const detail = useQuery({
    queryKey: useScopedQueryKey("document", documentId),
    queryFn: ({ signal }) =>
      api<Document>(`/library/${documentId}`, { tenantId, signal }),
    enabled: !!documentId,
    refetchInterval: (query) =>
      query.state.data &&
      (["pending", "indexing"].includes(query.state.data.status) ||
        ["pending", "indexing"].includes(query.state.data.pending_status || ""))
        ? 3000
        : false,
  });
  const versions = useQuery({
    queryKey: useScopedQueryKey("versions", documentId),
    queryFn: ({ signal }) =>
      api<Version[]>(`/library/${documentId}/versions`, { tenantId, signal }),
    enabled: !!documentId,
    refetchInterval: (query) =>
      query.state.data?.some((item) =>
        ["pending", "indexing"].includes(item.status),
      )
        ? 3000
        : false,
  });
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [textTitle, setTextTitle] = useState("");
  const [textContent, setTextContent] = useState("");
  const [uploadPublication, setUploadPublication] = useState("published");
  const [uploadEffective, setUploadEffective] = useState("");
  const [replacement, setReplacement] = useState<string | null>(null);
  const sequence = useRef(0);
  const transferController = useRef(new AbortController());
  useEffect(() => {
    transferController.current = new AbortController();
    return () => transferController.current.abort();
  }, [tenantId]);
  const [spaceEdit, setSpaceEdit] = useState<Space | null | undefined>(
    undefined,
  );
  const [spaceName, setSpaceName] = useState("");
  const [spaceDescription, setSpaceDescription] = useState("");
  const [spaceTags, setSpaceTags] = useState("");
  const [spaceVisibility, setSpaceVisibility] = useState<
    "company" | "restricted"
  >("company");
  const [metadata, setMetadata] = useState<Document | null>(null);
  const [metadataTags, setMetadataTags] = useState("");
  const [versionId, setVersionId] = useState("");
  const version = useQuery({
    queryKey: useScopedQueryKey("version", documentId, versionId),
    queryFn: ({ signal }) =>
      api<Version>(`/library/${documentId}/versions/${versionId}`, {
        tenantId,
        signal,
      }),
    enabled: !!documentId && !!versionId,
  });
  const [bulkAction, setBulkAction] = useState<"tag" | "move" | null>(null);
  const [bulkValue, setBulkValue] = useState("");
  const [confirm, setConfirm] = useState<{
    title: string;
    description: string;
    action: () => Promise<unknown>;
  } | null>(null);
  const [grantTarget, setGrantTarget] = useState<{
    kind: "spaces" | "library";
    id: string;
    name: string;
  } | null>(null);
  const [grantRows, setGrantRows] = useState<Grant[]>([]);
  const [effectiveAccess, setEffectiveAccess] = useState<
    EffectivePrincipal[] | undefined
  >();
  const [grantSubject, setGrantSubject] = useState("");
  const [grantType, setGrantType] = useState<"user" | "team" | "service">(
    "user",
  );
  const [grantPermission, setGrantPermission] = useState<"read" | "write">(
    "read",
  );
  const members = useQuery({
    queryKey: useScopedQueryKey("members"),
    queryFn: ({ signal }) =>
      api<{ items: { user_id: string; name: string; email: string }[] }>(
        `/organizations/${tenantId}/members`,
        { signal },
      ),
    enabled: canManage,
  });
  const teams = useQuery({
    queryKey: useScopedQueryKey("teams"),
    queryFn: ({ signal }) =>
      api<{ items: { id: string; name: string }[] }>(
        `/organizations/${tenantId}/teams`,
        { signal },
      ),
    enabled: canManage,
  });
  const integrations = useQuery({
    queryKey: useScopedQueryKey("integrations"),
    queryFn: ({ signal }) =>
      api<{ items: { id: string; name: string; active: boolean }[] }>(
        "/integrations",
        { tenantId, signal },
      ),
    enabled: canManage && !!grantTarget,
  });
  useEffect(() => {
    setSelected([]);
  }, [
    q,
    spaceId,
    lifecycle,
    sort,
    tag,
    status,
    mediaType,
    ownerId,
    updatedSince,
  ]);
  useEffect(() => {
    setVersionId(
      searchParams.get("version") || detail.data?.current_version_id || "",
    );
  }, [
    detail.data?.current_version_id,
    documentId,
    searchParams.get("version"),
  ]);
  async function refresh() {
    await client.invalidateQueries({
      queryKey: ["workspace", user.id, tenantId],
    });
    await client.invalidateQueries({ queryKey: ["organizations"] });
  }
  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await refresh();
      setConfirm(null);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  function addFiles(files: FileList | File[] | null) {
    if (!files) return;
    setUploads((old) => [
      ...old,
      ...Array.from(files).map((file) => ({
        id: ++sequence.current,
        file,
        status: "Waiting",
      })),
    ]);
    setUploadOpen(true);
  }
  async function submitUploads() {
    setBusy(true);
    setError(null);
    for (const item of uploads.filter(
      (i) => i.status === "Waiting" || i.status === "Failed",
    )) {
      if (transferController.current.signal.aborted) break;
      setUploads((old) =>
        old.map((i) =>
          i.id === item.id
            ? { ...i, status: "Uploading", error: undefined }
            : i,
        ),
      );
      try {
        const form = new FormData();
        form.append("file", item.file);
        form.append("publication_status", uploadPublication);
        if (uploadEffective)
          form.append(
            "effective_at",
            new Date(`${uploadEffective}T00:00:00`).toISOString(),
          );
        if (spaceId) form.append("space_id", spaceId);
        if (replacement) form.append("replace_document_id", replacement);
        await api("/library/upload", {
          tenantId,
          method: "POST",
          body: form,
          signal: transferController.current.signal,
        });
        setUploads((old) =>
          old.map((i) =>
            i.id === item.id ? { ...i, status: "Indexing queued" } : i,
          ),
        );
      } catch (e) {
        setUploads((old) =>
          old.map((i) =>
            i.id === item.id
              ? {
                  ...i,
                  status: "Failed",
                  error: e instanceof Error ? e.message : String(e),
                  duplicateId:
                    e instanceof ApiError &&
                    e.detail &&
                    typeof e.detail === "object" &&
                    "document_id" in e.detail
                      ? String(e.detail.document_id)
                      : undefined,
                }
              : i,
          ),
        );
      }
    }
    setBusy(false);
    await refresh();
  }
  async function download(id: string) {
    const response = await fetch(
      `/api/library/${documentId}/versions/${id}/download`,
      {
        headers: { "X-Atlas-Tenant": tenantId, "X-Atlas-Client": "console" },
        credentials: "include",
        signal: transferController.current.signal,
      },
    );
    if (!response.ok)
      throw new Error(
        "Download unavailable. Your access or the original file may have changed.",
      );
    const blob = await response.blob();
    if (transferController.current.signal.aborted) return;
    const url = URL.createObjectURL(blob);
    const anchor = window.document.createElement("a");
    anchor.href = url;
    anchor.download =
      versions.data?.find((v) => v.id === id)?.filename || "document.txt";
    anchor.click();
    URL.revokeObjectURL(url);
  }
  async function openGrants(target: NonNullable<typeof grantTarget>) {
    setError(null);
    try {
      const result = await api<{
        grants: Grant[];
        effective_access?: EffectivePrincipal[];
      }>(`/${target.kind}/${target.id}/grants`, {
        tenantId,
        signal: transferController.current.signal,
      });
      setGrantRows(result.grants);
      setEffectiveAccess(result.effective_access);
      setGrantTarget(target);
    } catch (e) {
      setError(e);
    }
  }
  const selectedSpace = spaces.data?.find((s) => s.id === spaceId);
  const base = `/o/${tenantId}/library`;
  const listUrl = `${base}?${searchParams.toString()}`;
  const document = detail.data;
  return (
    <>
      <PageHeader
        eyebrow="YOUR COMPANY KNOWLEDGE"
        title="Knowledge library"
        description="A clear home for the documents your team depends on."
        action={
          canEdit ? (
            <div className="row">
              {canManage && (
                <Button
                  variant="secondary"
                  onClick={() => {
                    setSpaceEdit(null);
                    setSpaceName("");
                    setSpaceDescription("");
                    setSpaceTags("");
                    setSpaceVisibility("company");
                  }}
                >
                  <FolderPlus size={17} />
                  New space
                </Button>
              )}
              <Button
                onClick={() => {
                  setReplacement(null);
                  setUploads([]);
                  setUploadOpen(true);
                }}
              >
                <Plus size={17} />
                Add knowledge
              </Button>
            </div>
          ) : undefined
        }
      />
      <ErrorNotice error={error || list.error || spaces.error} />
      {canManage && (
        <Button variant="ghost" onClick={() => setRequestsOpen(true)}>
          Access requests (
          {requests.data?.filter((r) => r.status === "pending").length || 0})
        </Button>
      )}
      {message && <Notice>{message}</Notice>}
      <div className="page-tabs">
        <button
          className={!spaceId ? "active" : ""}
          onClick={() => setSpaceId("")}
        >
          All knowledge
        </button>
        {spaces.data?.map((space) => (
          <button
            key={space.id}
            className={spaceId === space.id ? "active" : ""}
            onClick={() => setSpaceId(space.id)}
          >
            {space.visibility === "restricted" && <Lock size={13} />}{" "}
            {space.name}
          </button>
        ))}
      </div>
      {selectedSpace && (
        <section className="panel">
          <div className="row between">
            <div>
              <h2>{selectedSpace.name}</h2>
              <p className="muted">
                {selectedSpace.description ||
                  "Knowledge organized for your team."}
              </p>
              <p className="muted text-small">
                Owner:{" "}
                {selectedSpace.owner_name ||
                  members.data?.items.find(
                    (member) => member.user_id === selectedSpace.owner_user_id,
                  )?.name ||
                  (selectedSpace.owner_user_id === user.id
                    ? user.name
                    : selectedSpace.owner_user_id
                      ? "Company member (name unavailable)"
                      : "Unassigned")}
              </p>
            </div>
            <Badge>
              {selectedSpace.visibility === "restricted"
                ? "Restricted space"
                : "Company-wide"}
            </Badge>
          </div>
          {canManage && (
            <div className="row">
              <Button
                variant="ghost"
                onClick={() => {
                  setSpaceEdit(selectedSpace);
                  setSpaceName(selectedSpace.name);
                  setSpaceDescription(selectedSpace.description);
                  setSpaceTags(selectedSpace.tags.join(", "));
                  setSpaceVisibility(selectedSpace.visibility);
                }}
              >
                Edit space
              </Button>
              <Button
                variant="ghost"
                onClick={() =>
                  openGrants({
                    kind: "spaces",
                    id: selectedSpace.id,
                    name: selectedSpace.name,
                  })
                }
              >
                Manage access
              </Button>
              <Button
                variant="ghost"
                onClick={() =>
                  run(async () => {
                    await navigator.clipboard.writeText(
                      `${window.location.origin}${base}?requestSpace=${selectedSpace.id}`,
                    );
                    setMessage(
                      "Access request link copied. Share it with a company member.",
                    );
                  })
                }
              >
                Copy access request link
              </Button>
            </div>
          )}
        </section>
      )}
      <section className="panel">
        <div className="row" style={{ flexWrap: "wrap" }}>
          <input
            aria-label="Search documents"
            placeholder="Find a document…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            style={{ flex: "1 1 200px" }}
          />
          <input
            aria-label="Filter tag"
            placeholder="Filter by tag"
            value={tag}
            onChange={(e) => setTag(e.target.value)}
          />
          <select
            aria-label="Document lifecycle"
            value={lifecycle}
            onChange={(e) => setLifecycle(e.target.value)}
          >
            <option value="active">Active</option>
            <option value="archived">Archived</option>
            <option value="trashed">Trash</option>
            <option value="all">All states</option>
          </select>
          <select
            aria-label="Indexing status"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="">All readiness</option>
            {["ready", "pending", "indexing", "failed"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
          <select
            aria-label="Sort documents"
            value={sort}
            onChange={(e) => setSort(e.target.value)}
          >
            <option value="updated">Recently updated</option>
            <option value="title">Title A–Z</option>
            <option value="oldest">Oldest first</option>
          </select>
          <Button
            variant="ghost"
            aria-label={grid ? "Show list" : "Show grid"}
            onClick={() => setGrid(!grid)}
          >
            {grid ? <List size={18} /> : <Grid2X2 size={18} />}
          </Button>
        </div>
        <details style={{ marginTop: 12 }}>
          <summary>More filters</summary>
          <div className="row" style={{ flexWrap: "wrap", marginTop: 12 }}>
            <label className="field">
              <span>File type</span>
              <select
                value={mediaType}
                onChange={(e) => setMediaType(e.target.value)}
              >
                <option value="">All types</option>
                <option value="text/plain">Text</option>
                <option value="text/markdown">Markdown</option>
                <option value="application/pdf">PDF</option>
                <option value="application/vnd.openxmlformats-officedocument.wordprocessingml.document">
                  DOCX
                </option>
              </select>
            </label>
            <label className="field">
              <span>Owner</span>
              <select
                value={ownerId}
                onChange={(e) => setOwnerId(e.target.value)}
              >
                <option value="">Anyone</option>
                <option value={user.id}>Me</option>
                {owners.data?.items
                  .filter((m) => m.user_id !== user.id)
                  .map((m) => (
                    <option key={m.user_id} value={m.user_id}>
                      {m.name}
                    </option>
                  ))}
                {ownerId &&
                  ownerId !== user.id &&
                  !owners.data?.items.some(
                    (owner) => owner.user_id === ownerId,
                  ) && (
                    <option value={ownerId}>
                      Selected owner (unavailable)
                    </option>
                  )}
              </select>
              {owners.error && (
                <span role="status" className="muted">
                  Owner choices could not be loaded.
                </span>
              )}
            </label>
            <Field
              label="Updated since"
              type="date"
              value={updatedSince}
              onChange={(e) => setUpdatedSince(e.target.value)}
            />
          </div>
        </details>
        {selected.length > 0 && canEdit && (
          <div className="row" style={{ marginTop: 16, flexWrap: "wrap" }}>
            <strong>{selected.length} selected</strong>
            <Button
              variant="secondary"
              onClick={() =>
                run(() =>
                  api("/library/bulk", {
                    tenantId,
                    method: "POST",
                    body: json({
                      document_ids: selected,
                      action: lifecycle === "archived" ? "restore" : "archive",
                    }),
                  }),
                )
              }
            >
              <Archive size={15} />
              {lifecycle === "archived" ? "Restore" : "Archive"}
            </Button>
            <Button
              variant="secondary"
              onClick={() =>
                setConfirm({
                  title: "Move selected documents to trash?",
                  description:
                    "They will stop appearing in answers. You can restore them later.",
                  action: () =>
                    api("/library/bulk", {
                      tenantId,
                      method: "POST",
                      body: json({ document_ids: selected, action: "trash" }),
                    }),
                })
              }
            >
              <Trash2 size={15} />
              Trash
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                setBulkValue("");
                setBulkAction("tag");
              }}
            >
              Add tags
            </Button>
            {canManage && (
              <Button
                variant="secondary"
                onClick={() => {
                  setBulkValue("");
                  setBulkAction("move");
                }}
              >
                Move to space
              </Button>
            )}
            <Button variant="ghost" onClick={() => setSelected([])}>
              Clear selection
            </Button>
          </div>
        )}
        {list.isPending ? (
          <Loading label="Loading your library…" />
        ) : !list.data?.items.length ? (
          <EmptyState
            title="Room for useful knowledge"
            description={
              q || tag
                ? "No documents match these filters."
                : "Add a runbook, guide or document to start asking questions with evidence."
            }
            action={
              canEdit ? (
                <Button
                  onClick={() => {
                    setReplacement(null);
                    setUploadOpen(true);
                  }}
                >
                  Upload documents
                </Button>
              ) : undefined
            }
          />
        ) : grid ? (
          <div className="card-grid" style={{ marginTop: 20 }}>
            {list.data.items.map((doc) => (
              <article className="panel" key={doc.id}>
                <FileText size={25} />
                <h3>
                  <Link to={`${base}/${doc.id}?${searchParams.toString()}`}>
                    {doc.title}
                  </Link>
                </h3>
                <Badge tone={doc.status === "ready" ? "success" : "neutral"}>
                  {doc.pending_status
                    ? `Replacement ${doc.pending_status}`
                    : doc.status}
                </Badge>
                <p className="muted text-small">
                  {doc.tags.join(" · ") || "No tags"} ·{" "}
                  {new Date(doc.updated_at).toLocaleDateString()}
                </p>
              </article>
            ))}
          </div>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  {canEdit && (
                    <th>
                      <span className="sr-only">Select</span>
                    </th>
                  )}
                  <th>Document</th>
                  <th>Status</th>
                  <th>Tags</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((doc) => (
                  <tr key={doc.id}>
                    {canEdit && (
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Select ${doc.title}`}
                          checked={selected.includes(doc.id)}
                          onChange={(e) =>
                            setSelected((old) =>
                              e.target.checked
                                ? [...old, doc.id]
                                : old.filter((id) => id !== doc.id),
                            )
                          }
                        />
                      </td>
                    )}
                    <td>
                      <Link to={`${base}/${doc.id}?${searchParams.toString()}`}>
                        <strong>{doc.title}</strong>
                      </Link>
                      <small>
                        {spaces.data?.find((s) => s.id === doc.space_id)
                          ?.name || "Company knowledge"}
                        {doc.restricted ? " · Restricted" : ""}
                        {doc.review_due_at &&
                        new Date(doc.review_due_at) < new Date()
                          ? " · Review overdue"
                          : ""}
                      </small>
                    </td>
                    <td>
                      <Badge
                        tone={doc.status === "ready" ? "success" : "neutral"}
                      >
                        {doc.status}
                      </Badge>
                      {doc.pending_status && (
                        <small>Replacement {doc.pending_status}</small>
                      )}
                    </td>
                    <td>{doc.tags.join(", ") || "—"}</td>
                    <td>{new Date(doc.updated_at).toLocaleDateString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="row between" style={{ marginTop: 18 }}>
          <span className="muted text-small">
            {list.data?.total || 0} documents
          </span>
          <div className="row">
            <Button
              variant="ghost"
              disabled={page === 1}
              onClick={() => setPage(page - 1)}
            >
              Previous
            </Button>
            <span>Page {page}</span>
            <Button
              variant="ghost"
              disabled={page * 20 >= (list.data?.total || 0)}
              onClick={() => setPage(page + 1)}
            >
              Next
            </Button>
          </div>
        </div>
      </section>
      <Modal
        open={uploadOpen}
        onOpenChange={(v) => !busy && setUploadOpen(v)}
        title={replacement ? "Replace document" : "Add knowledge"}
        description={
          replacement
            ? "The current ready version stays searchable until its replacement is ready."
            : "Upload text, Markdown, text PDF or DOCX. Scanned PDFs need OCR before upload."
        }
        wide
      >
        <ErrorNotice error={error} />
        <div className="form-stack">
          <label className="field">
            <span>Destination space</span>
            <select
              value={spaceId}
              disabled={!!replacement || busy}
              onChange={(e) => setSpaceId(e.target.value)}
            >
              <option value="">Company knowledge</option>
              {spaces.data?.map((s) => (
                <option value={s.id} key={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <div className="workbench-columns">
            <label className="field">
              <span>Publication after indexing</span>
              <select
                value={uploadPublication}
                disabled={busy}
                onChange={(event) => setUploadPublication(event.target.value)}
              >
                <option value="published">Publish when ready</option>
                <option value="draft">Keep as draft for review</option>
              </select>
            </label>
            <Field
              label="Effective date (optional)"
              type="date"
              value={uploadEffective}
              disabled={busy}
              onChange={(event) => setUploadEffective(event.target.value)}
            />
          </div>
          <div
            className="empty-state"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              if (!busy) addFiles(e.dataTransfer.files);
            }}
          >
            <Upload size={30} />
            <p>Drop files here or choose from your device</p>
            <input
              type="file"
              aria-label="Choose documents"
              accept=".txt,.md,.pdf,.docx"
              multiple={!replacement}
              disabled={busy}
              onChange={(e) => addFiles(e.target.files)}
            />
            <small className="muted">
              Up to 20 MB per file · 200 PDF pages
            </small>
          </div>
          {uploads.map((item) => (
            <div key={item.id} className="row between">
              <div>
                <strong>{item.file.name}</strong>
                <small style={{ display: "block" }}>
                  {item.error || item.status}
                </small>
                {item.duplicateId && (
                  <Button
                    variant="secondary"
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        const form = new FormData();
                        form.append("file", item.file);
                        form.append("replace_document_id", item.duplicateId!);
                        form.append("publication_status", uploadPublication);
                        if (uploadEffective)
                          form.append(
                            "effective_at",
                            new Date(
                              `${uploadEffective}T00:00:00`,
                            ).toISOString(),
                          );
                        if (spaceId) form.append("space_id", spaceId);
                        await api("/library/upload", {
                          tenantId,
                          method: "POST",
                          body: form,
                        });
                        setUploads((old) =>
                          old.map((i) =>
                            i.id === item.id
                              ? {
                                  ...i,
                                  status: "Indexing queued",
                                  error: undefined,
                                  duplicateId: undefined,
                                }
                              : i,
                          ),
                        );
                      })
                    }
                  >
                    Create a new version
                  </Button>
                )}
              </div>
              {item.status === "Waiting" || item.status === "Failed" ? (
                <Button
                  variant="ghost"
                  aria-label={`Remove ${item.file.name}`}
                  onClick={() =>
                    setUploads((old) => old.filter((i) => i.id !== item.id))
                  }
                >
                  <X size={17} />
                </Button>
              ) : item.status === "Indexing queued" ? (
                <Check size={18} />
              ) : null}
            </div>
          ))}
          {uploads.some((i) => ["Waiting", "Failed"].includes(i.status)) && (
            <Button busy={busy} onClick={submitUploads}>
              Upload{" "}
              {
                uploads.filter((i) => ["Waiting", "Failed"].includes(i.status))
                  .length
              }{" "}
              files
            </Button>
          )}
          {!replacement && (
            <details>
              <summary>Or write a document</summary>
              <form
                className="form-stack"
                onSubmit={(e: FormEvent) => {
                  e.preventDefault();
                  void run(async () => {
                    await api("/library/text", {
                      tenantId,
                      method: "POST",
                      body: json({
                        title: textTitle,
                        content: textContent,
                        space_id: spaceId || null,
                        publication_status: uploadPublication,
                        effective_at: uploadEffective
                          ? new Date(
                              `${uploadEffective}T00:00:00`,
                            ).toISOString()
                          : null,
                      }),
                    });
                    setTextTitle("");
                    setTextContent("");
                    setUploadOpen(false);
                    setMessage("Document queued for indexing.");
                  });
                }}
              >
                <Field
                  label="Document title"
                  value={textTitle}
                  onChange={(e) => setTextTitle(e.target.value)}
                  required
                />
                <label className="field">
                  <span>Document text</span>
                  <textarea
                    rows={8}
                    value={textContent}
                    onChange={(e) => setTextContent(e.target.value)}
                    required
                  />
                </label>
                <Button busy={busy}>Save and index</Button>
              </form>
            </details>
          )}
        </div>
      </Modal>
      <Modal
        open={spaceEdit !== undefined}
        onOpenChange={(v) => !v && setSpaceEdit(undefined)}
        title={spaceEdit ? "Edit space" : "Create a knowledge space"}
        description="Company owners and administrators can inspect company-owned knowledge."
      >
        <form
          className="form-stack"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await api(spaceEdit ? `/spaces/${spaceEdit.id}` : "/spaces", {
                tenantId,
                method: spaceEdit ? "PATCH" : "POST",
                body: json({
                  name: spaceName,
                  description: spaceDescription,
                  visibility: spaceVisibility,
                  tags: spaceTags
                    .split(",")
                    .map((t) => t.trim())
                    .filter(Boolean),
                }),
              });
              setSpaceEdit(undefined);
            });
          }}
        >
          <ErrorNotice error={error} />
          <Field
            label="Space name"
            value={spaceName}
            onChange={(e) => setSpaceName(e.target.value)}
            required
            maxLength={120}
          />
          <Field
            label="Description"
            value={spaceDescription}
            onChange={(e) => setSpaceDescription(e.target.value)}
          />
          <Field
            label="Space tags (comma separated)"
            value={spaceTags}
            onChange={(e) => setSpaceTags(e.target.value)}
          />
          <label className="field">
            <span>Who can access it?</span>
            <select
              value={spaceVisibility}
              onChange={(e) =>
                setSpaceVisibility(e.target.value as typeof spaceVisibility)
              }
            >
              <option value="company">Everyone in this company</option>
              <option value="restricted">Selected people and teams</option>
            </select>
          </label>
          {spaceVisibility === "restricted" && (
            <p className="muted">
              After creation, use Manage access to add the people and teams who
              need this space.
            </p>
          )}
          <Button busy={busy}>Save space</Button>
        </form>
      </Modal>
      <Modal
        open={!!documentId}
        onOpenChange={(v) => !v && navigate(listUrl)}
        title={document?.title || "Document"}
        description="Inspect exact source text and immutable versions."
        wide
      >
        <ErrorNotice
          error={detail.error || versions.error || version.error || error}
        />
        {detail.isPending ? (
          <Loading />
        ) : (
          document && (
            <>
              <div
                className="row"
                style={{ flexWrap: "wrap", marginBottom: 18 }}
              >
                <Badge>{document.status}</Badge>
                {document.pending_status && (
                  <Badge>Replacement {document.pending_status}</Badge>
                )}
                <select
                  aria-label="Document version"
                  value={versionId}
                  onChange={(e) => setVersionId(e.target.value)}
                >
                  {versions.data?.map((v) => (
                    <option value={v.id} key={v.id}>
                      Version {v.number} · {v.status}
                      {v.publication_status ? ` · ${v.publication_status}` : ""}
                      {v.id === document.current_version_id ? " · Current" : ""}
                    </option>
                  ))}
                </select>
                {document.status === "ready" &&
                  document.lifecycle === "active" && (
                    <>
                      <Link
                        className="button secondary"
                        to={`/o/${tenantId}/ask?summary=${document.id}`}
                      >
                        Summarize document
                      </Link>
                      <Button
                        variant="ghost"
                        onClick={() =>
                          run(async () => {
                            await api("/bookmarks", {
                              tenantId,
                              method: "POST",
                              body: json({
                                kind: "document",
                                resource_id: document.id,
                                title: document.title,
                              }),
                            });
                            setMessage(
                              "Document saved to your personal collection.",
                            );
                          })
                        }
                      >
                        Save document
                      </Button>
                    </>
                  )}
                <Button
                  variant="secondary"
                  disabled={!versionId}
                  onClick={() => run(() => download(versionId))}
                >
                  <Download size={15} />
                  Download
                </Button>
                {canEdit && (
                  <>
                    <Button
                      variant="ghost"
                      onClick={() => {
                        setMetadata({ ...document });
                        setMetadataTags(document.tags.join(", "));
                      }}
                    >
                      Edit details
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={() => {
                        setReplacement(document.id);
                        setSpaceId(document.space_id || "");
                        setUploads([]);
                        setUploadOpen(true);
                      }}
                    >
                      Replace file
                    </Button>
                    {document.pending_status === "failed" && (
                      <Button
                        variant="secondary"
                        onClick={() =>
                          run(() =>
                            api(`/library/${document.id}/retry`, {
                              tenantId,
                              method: "POST",
                            }),
                          )
                        }
                      >
                        Retry indexing
                      </Button>
                    )}
                    {versionId && versionId !== document.current_version_id && (
                      <Button
                        variant="secondary"
                        onClick={() =>
                          setConfirm({
                            title: "Restore this version?",
                            description:
                              "Atlas will create a new revision from this version and index it before publishing.",
                            action: () =>
                              api(
                                `/library/${document.id}/versions/${versionId}/restore`,
                                { tenantId, method: "POST" },
                              ),
                          })
                        }
                      >
                        Restore version
                      </Button>
                    )}
                  </>
                )}
                {canManage && (
                  <Button
                    variant="ghost"
                    onClick={() =>
                      openGrants({
                        kind: "library",
                        id: document.id,
                        name: document.title,
                      })
                    }
                  >
                    Manage access
                  </Button>
                )}
              </div>
              {version.data && (
                <PublicationControls
                  documentId={document.id}
                  version={version.data}
                />
              )}
              {versionId && versionId !== document.current_version_id && (
                <div className="notice" role="status">
                  You are viewing a historical or unpublished version. Answers
                  use the current effective published version.
                </div>
              )}
              {document.review_due_at &&
                new Date(document.review_due_at) < new Date() && (
                  <div className="notice" role="status">
                    This document is overdue for review. Check with its owner
                    before relying on it.
                  </div>
                )}
              {version.isPending && versionId ? (
                <Loading />
              ) : (
                <div
                  style={{
                    maxHeight: "48vh",
                    overflow: "auto",
                    background: "var(--canvas)",
                    padding: 20,
                    borderRadius: 10,
                  }}
                >
                  {(version.data?.source_segments.length
                    ? version.data.source_segments
                    : [
                        {
                          start: 0,
                          end: version.data?.content?.length || 0,
                          section: "Document",
                        },
                      ]
                  ).map((segment, index) => (
                    <section key={index} style={{ marginBottom: 24 }}>
                      <Badge>
                        {segment.page
                          ? `Page ${segment.page}`
                          : segment.section || "Source"}
                      </Badge>
                      <pre
                        style={{
                          whiteSpace: "pre-wrap",
                          font: "inherit",
                          lineHeight: 1.8,
                        }}
                      >
                        {Array.from(version.data?.content || " ")
                          .slice(segment.start, segment.end)
                          .join("")}
                      </pre>
                    </section>
                  )) || (
                    <pre style={{ whiteSpace: "pre-wrap", font: "inherit" }}>
                      {document.content}
                    </pre>
                  )}
                </div>
              )}
              {canEdit && (
                <div
                  className="row"
                  style={{ marginTop: 20, flexWrap: "wrap" }}
                >
                  <Button
                    variant="secondary"
                    onClick={() =>
                      run(() =>
                        api(`/library/${document.id}`, {
                          tenantId,
                          method: "PATCH",
                          body: json({
                            lifecycle:
                              document.lifecycle === "active"
                                ? "archived"
                                : "active",
                          }),
                        }),
                      )
                    }
                  >
                    {document.lifecycle === "active"
                      ? "Archive"
                      : "Restore to active"}
                  </Button>
                  <Button
                    variant="danger"
                    onClick={() =>
                      setConfirm({
                        title:
                          document.lifecycle === "trashed"
                            ? "Permanently delete this document?"
                            : "Move document to trash?",
                        description:
                          document.lifecycle === "trashed"
                            ? "Original files, versions and indexes will be removed. Historical answers will show unavailable evidence. This cannot be undone."
                            : "The document will stop appearing in answers. You can restore it from Trash.",
                        action: async () => {
                          await api(
                            `/library/${document.id}${document.lifecycle === "trashed" ? "?permanent=true" : ""}`,
                            { tenantId, method: "DELETE" },
                          );
                          navigate(listUrl);
                        },
                      })
                    }
                    disabled={document.lifecycle === "trashed" && !canManage}
                  >
                    {document.lifecycle === "trashed"
                      ? "Delete permanently"
                      : "Move to trash"}
                  </Button>
                </div>
              )}
            </>
          )
        )}
      </Modal>
      <Modal
        open={!!metadata}
        onOpenChange={(v) => !v && setMetadata(null)}
        title="Document details"
        description="Keep this document organized and reviewed."
      >
        <ErrorNotice error={error} />
        {metadata && (
          <form
            className="form-stack"
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                await api(`/library/${metadata.id}`, {
                  tenantId,
                  method: "PATCH",
                  body: json({
                    title: metadata.title,
                    tags: metadataTags
                      .split(",")
                      .map((t) => t.trim())
                      .filter(Boolean),
                    review_due_at: metadata.review_due_at
                      ? new Date(metadata.review_due_at).toISOString()
                      : null,
                    ...(canManage
                      ? {
                          space_id: metadata.space_id,
                          restricted: metadata.restricted,
                          owner_user_id: metadata.owner_user_id,
                        }
                      : {}),
                  }),
                });
                setMetadata(null);
              });
            }}
          >
            <Field
              label="Title"
              value={metadata.title}
              onChange={(e) =>
                setMetadata({ ...metadata, title: e.target.value })
              }
              required
            />
            <Field
              label="Tags (comma separated)"
              value={metadataTags}
              onChange={(e) => setMetadataTags(e.target.value)}
            />
            <Field
              label="Review due"
              type="date"
              value={metadata.review_due_at?.slice(0, 10) || ""}
              onChange={(e) =>
                setMetadata({
                  ...metadata,
                  review_due_at: e.target.value || null,
                })
              }
            />
            {canManage && (
              <>
                <label className="field">
                  <span>Space</span>
                  <select
                    value={metadata.space_id || ""}
                    onChange={(e) =>
                      setMetadata({
                        ...metadata,
                        space_id: e.target.value || null,
                      })
                    }
                  >
                    <option value="">Company knowledge</option>
                    {spaces.data?.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Document owner</span>
                  <select
                    value={metadata.owner_user_id || ""}
                    onChange={(e) =>
                      setMetadata({
                        ...metadata,
                        owner_user_id: e.target.value || null,
                      })
                    }
                  >
                    <option value="">No owner</option>
                    {members.data?.items.map((m) => (
                      <option key={m.user_id} value={m.user_id}>
                        {m.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={metadata.restricted}
                    onChange={(e) =>
                      setMetadata({ ...metadata, restricted: e.target.checked })
                    }
                  />{" "}
                  Restrict to selected people and teams within the space
                </label>
              </>
            )}
            <Button busy={busy}>Save details</Button>
          </form>
        )}
      </Modal>
      <Modal
        open={!!grantTarget}
        onOpenChange={(v) => !v && setGrantTarget(null)}
        title={`Access to ${grantTarget?.name || "knowledge"}`}
        description="Company owners and administrators can always inspect company-owned knowledge. Document grants cannot widen the enclosing space's access."
      >
        <ErrorNotice
          error={integrations.error || members.error || teams.error}
        />
        <ErrorNotice error={error} />
        <div className="form-stack">
          {effectiveAccess && <EffectiveAccess items={effectiveAccess} />}
          <h3>Configured grants</h3>
          {grantTarget?.kind === "library" && (
            <p className="muted">
              Enable “Restrict to selected people and teams” in Document details
              to narrow inherited access.
            </p>
          )}
          <div className="row">
            <select
              aria-label="Grant type"
              value={grantType}
              onChange={(e) => {
                setGrantType(e.target.value as typeof grantType);
                setGrantSubject("");
              }}
            >
              <option value="user">Person</option>
              <option value="team">Team</option>
              <option value="service">Integration</option>
            </select>
            <select
              aria-label="Grant recipient"
              value={grantSubject}
              onChange={(e) => setGrantSubject(e.target.value)}
            >
              <option value="">Choose recipient</option>
              {grantType === "user"
                ? members.data?.items.map((m) => (
                    <option key={m.user_id} value={m.user_id}>
                      {m.name} · {m.email}
                    </option>
                  ))
                : grantType === "team"
                  ? teams.data?.items.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))
                  : integrations.data?.items
                      .filter((s) => s.active)
                      .map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name}
                        </option>
                      ))}
            </select>
            <select
              aria-label="Grant permission"
              value={grantPermission}
              onChange={(e) =>
                setGrantPermission(e.target.value as typeof grantPermission)
              }
            >
              <option value="read">Read</option>
              <option value="write">Edit</option>
            </select>
            <Button
              variant="secondary"
              disabled={!grantSubject}
              onClick={() => {
                setGrantRows((old) => [
                  ...old.filter(
                    (g) =>
                      !(
                        g.subject_type === grantType &&
                        g.subject_id === grantSubject
                      ),
                  ),
                  {
                    subject_type: grantType,
                    subject_id: grantSubject,
                    permission: grantPermission,
                  },
                ]);
                setGrantSubject("");
              }}
            >
              Add
            </Button>
          </div>
          {grantRows.map((g, i) => (
            <div
              className="row between"
              key={`${g.subject_type}:${g.subject_id}`}
            >
              <span>
                {g.subject_type === "user"
                  ? members.data?.items.find((m) => m.user_id === g.subject_id)
                      ?.name || "Company member"
                  : g.subject_type === "team"
                    ? teams.data?.items.find((t) => t.id === g.subject_id)
                        ?.name || "Team (unavailable)"
                    : integrations.data?.items.find(
                        (service) => service.id === g.subject_id,
                      )?.name || "Integration (unavailable)"}{" "}
                · {g.permission}
              </span>
              <Button
                variant="ghost"
                aria-label="Remove grant"
                onClick={() =>
                  setGrantRows((old) => old.filter((_, index) => index !== i))
                }
              >
                <X size={15} />
              </Button>
            </div>
          ))}
          <Button
            busy={busy}
            onClick={() =>
              run(async () => {
                await api(`/${grantTarget?.kind}/${grantTarget?.id}/grants`, {
                  tenantId,
                  method: "PUT",
                  body: json({ grants: grantRows }),
                });
                setGrantTarget(null);
              })
            }
          >
            Save access
          </Button>
        </div>
      </Modal>
      <Modal
        open={!!confirm}
        onOpenChange={(v) => !v && setConfirm(null)}
        title={confirm?.title || "Confirm action"}
        description={confirm?.description}
      >
        <ErrorNotice error={error} />
        <div className="modal-actions">
          <Button variant="secondary" onClick={() => setConfirm(null)}>
            Cancel
          </Button>
          <Button busy={busy} onClick={() => confirm && run(confirm.action)}>
            Confirm
          </Button>
        </div>
      </Modal>
      <Modal
        open={!!requestSpace}
        onOpenChange={(v) => !v && setSearchParams({})}
        title="Request space access"
        description="A company administrator will review your request. This does not grant access automatically."
      >
        <form
          className="form-stack"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await api(`/spaces/${requestSpace}/access-requests`, {
                tenantId,
                method: "POST",
                body: json({ reason: requestReason }),
              });
              setSearchParams({});
              setMessage("Your access request has been sent.");
            });
          }}
        >
          <ErrorNotice error={error} />
          <Field
            label="Why do you need access?"
            value={requestReason}
            onChange={(e) => setRequestReason(e.target.value)}
            maxLength={1000}
          />
          <Button busy={busy}>Send request</Button>
        </form>
      </Modal>
      <Modal
        open={requestsOpen}
        onOpenChange={setRequestsOpen}
        title="Access requests"
        description="Review requests from company members."
      >
        <ErrorNotice error={error || requests.error} />
        {!requests.data?.some((r) => r.status === "pending") ? (
          <EmptyState
            title="All caught up"
            description="There are no pending access requests."
          />
        ) : (
          requests.data
            .filter((r) => r.status === "pending")
            .map((r) => (
              <section className="panel" key={r.id}>
                <strong>
                  {members.data?.items.find((m) => m.user_id === r.user_id)
                    ?.name || "Company member"}
                </strong>
                <p>
                  {spaces.data?.find((s) => s.id === r.space_id)?.name ||
                    "Knowledge space"}
                </p>
                <p className="muted">{r.reason || "No reason supplied"}</p>
                <div className="row">
                  <Button
                    busy={busy}
                    onClick={() =>
                      run(() =>
                        api(`/access-requests/${r.id}/resolve`, {
                          tenantId,
                          method: "POST",
                          body: json({ decision: "approved" }),
                        }),
                      )
                    }
                  >
                    Approve
                  </Button>
                  <Button
                    variant="secondary"
                    busy={busy}
                    onClick={() =>
                      run(() =>
                        api(`/access-requests/${r.id}/resolve`, {
                          tenantId,
                          method: "POST",
                          body: json({ decision: "denied" }),
                        }),
                      )
                    }
                  >
                    Decline
                  </Button>
                </div>
              </section>
            ))
        )}
      </Modal>
      <Modal
        open={!!bulkAction}
        onOpenChange={(v) => !v && setBulkAction(null)}
        title={
          bulkAction === "tag"
            ? "Tag selected documents"
            : "Move selected documents"
        }
        description={`${selected.length} documents will be updated together.`}
      >
        <form
          className="form-stack"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await api("/library/bulk", {
                tenantId,
                method: "POST",
                body: json({
                  document_ids: selected,
                  action: bulkAction,
                  ...(bulkAction === "tag"
                    ? {
                        tags: bulkValue
                          .split(",")
                          .map((t) => t.trim())
                          .filter(Boolean),
                      }
                    : { space_id: bulkValue || null }),
                }),
              });
              setBulkAction(null);
              setSelected([]);
            });
          }}
        >
          <ErrorNotice error={error} />
          {bulkAction === "tag" ? (
            <Field
              label="Tags (comma separated)"
              value={bulkValue}
              onChange={(e) => setBulkValue(e.target.value)}
              required
            />
          ) : (
            <label className="field">
              <span>Destination</span>
              <select
                value={bulkValue}
                onChange={(e) => setBulkValue(e.target.value)}
              >
                <option value="">Company knowledge</option>
                {spaces.data?.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <Button busy={busy}>Apply changes</Button>
        </form>
      </Modal>
    </>
  );
}
