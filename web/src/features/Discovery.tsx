import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  ArrowUp,
  Bell,
  BookOpen,
  Bookmark,
  Check,
  FileText,
  History,
  Search,
  Sparkles,
  Trash2,
  Upload,
  Users,
} from "lucide-react";
import { api, json } from "../api/client";
import { useScopedQueryKey, useWorkspace } from "../app/context";
import {
  Badge,
  Button,
  EmptyState,
  ErrorNotice,
  Field,
  Loading,
  Modal,
  PageHeader,
} from "../components/ui";
import {
  EvidenceDialog,
  SourceCard,
  type Evidence,
} from "../components/Evidence";
import type { Conversation } from "./Conversations";
import { WorkspaceHealth } from "./WorkspaceMetrics";
type DiscoveryDocument = {
  id: string;
  title: string;
  space_id: string;
  status: string;
  updated_at: string;
  media_type?: string;
  snippet?: string;
  excerpt?: string;
  content?: string;
};
type DiscoveryResult = {
  documents: DiscoveryDocument[];
  conversations: Conversation[];
};
type SavedItem = {
  id: string;
  kind: "document" | "message";
  resource_id: string;
  title: string;
  available: boolean;
  conversation_id?: string;
  document_id?: string;
  content?: string;
  sources?: Evidence[];
  created_at: string;
};
type Notification = {
  id: string;
  kind: string;
  title: string;
  body: string;
  link: string;
  read_at: string | null;
  created_at: string;
};
function when(value: string) {
  return new Date(value).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}
export function HomePage() {
  const { tenantId, user, organization, canEdit, canManage } = useWorkspace();
  const navigate = useNavigate();
  const [question, setQuestion] = useState("");
  const threads = useQuery({
    queryKey: useScopedQueryKey("home-conversations"),
    queryFn: ({ signal }) =>
      api<{ items: Conversation[] }>("/conversations", { tenantId, signal }),
  });
  const documents = useQuery({
    queryKey: useScopedQueryKey("home-library"),
    queryFn: ({ signal }) =>
      api<{ items: DiscoveryDocument[]; total: number }>(
        "/library?page_size=6&lifecycle=active&sort=updated",
        { tenantId, signal },
      ),
  });
  const saved = useQuery({
    queryKey: useScopedQueryKey("home-bookmarks"),
    queryFn: ({ signal }) =>
      api<{ items: SavedItem[] }>("/bookmarks", { tenantId, signal }),
  });
  const members = useQuery({
    queryKey: useScopedQueryKey("home-members"),
    queryFn: ({ signal }) =>
      api<{ items: { user_id: string }[] }>(
        `/organizations/${tenantId}/members`,
        { signal },
      ),
    enabled: canManage && organization.kind !== "personal",
  });
  const base = `/o/${tenantId}`;
  const firstName = user.name.split(" ")[0];
  return (
    <>
      <PageHeader
        eyebrow="YOUR WORKSPACE, AT A GLANCE"
        title={`Welcome back, ${firstName}`}
        description={`Pick up where you left off in ${organization.name}.`}
      />
      <section className="home-hero">
        <div>
          <span className="eyebrow">A LITTLE CONTEXT. A CLEARER ANSWER.</span>
          <h2>
            Your knowledge. <em>A clearer next step.</em>
          </h2>
          <p>
            Find the right document, understand a process, or connect the
            details across your team.
          </p>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            navigate(`${base}/ask?q=${encodeURIComponent(question.trim())}`);
          }}
        >
          <label className="sr-only" htmlFor="home-question">
            Ask your knowledge
          </label>
          <textarea
            id="home-question"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="What would you like to understand?"
            maxLength={2000}
          />
          <div className="row between">
            <span>
              <Sparkles size={14} />
              Answers with sources you can check
            </span>
            <Button disabled={question.trim().length < 2}>
              Ask Atlas
              <ArrowUp size={17} />
            </Button>
          </div>
        </form>
      </section>
      <div className="workspace-overview" aria-label="Workspace overview">
        <Link to={`${base}/library`}>
          <span className="overview-icon">
            <BookOpen size={21} />
          </span>
          <div>
            <span className="overview-value">
              {documents.isPending
                ? "…"
                : documents.error
                  ? "—"
                  : (documents.data?.total ?? 0)}
            </span>
            <span>Documents you can access</span>
          </div>
          <ArrowRight size={17} />
        </Link>
        <Link to={`${base}/ask`}>
          <span className="overview-icon">
            <History size={21} />
          </span>
          <div>
            <span className="overview-value">
              {threads.isPending
                ? "…"
                : threads.error
                  ? "—"
                  : (threads.data?.items.length ?? 0)}
            </span>
            <span>Recent conversations</span>
          </div>
          <ArrowRight size={17} />
        </Link>
        <Link to={`${base}/saved`}>
          <span className="overview-icon">
            <Bookmark size={21} />
          </span>
          <div>
            <span className="overview-value">
              {saved.isPending
                ? "…"
                : saved.error
                  ? "—"
                  : (saved.data?.items.length ?? 0)}
            </span>
            <span>Saved items</span>
          </div>
          <ArrowRight size={17} />
        </Link>
      </div>
      {(!documents.data?.total ||
        !threads.data?.items.length ||
        (canManage &&
          organization.kind !== "personal" &&
          (members.data?.items.length || 0) < 2)) && (
        <section className="panel getting-started">
          <div className="row between">
            <h2>Make this workspace yours</h2>
            <Badge>Getting started</Badge>
          </div>
          <div className="onboarding-steps">
            <Link to={`${base}/library`}>
              <span className={documents.data?.total ? "step done" : "step"}>
                {documents.data?.total ? (
                  <Check size={17} />
                ) : (
                  <Upload size={17} />
                )}
              </span>
              <div>
                <strong>
                  {canEdit ? "Add your knowledge" : "Explore your knowledge"}
                </strong>
                <small>
                  {documents.data?.total
                    ? `${documents.data.total} documents in your accessible library`
                    : "Start with a guide, runbook or useful document"}
                </small>
              </div>
              <ArrowRight size={16} />
            </Link>
            {canManage && organization.kind !== "personal" && (
              <Link to={`${base}/people`}>
                <span
                  className={
                    (members.data?.items.length || 0) > 1 ? "step done" : "step"
                  }
                >
                  {(members.data?.items.length || 0) > 1 ? (
                    <Check size={17} />
                  ) : (
                    <Users size={17} />
                  )}
                </span>
                <div>
                  <strong>Invite your colleagues</strong>
                  <small>Give each person the right role and access</small>
                </div>
                <ArrowRight size={16} />
              </Link>
            )}
            <Link to={`${base}/ask`}>
              <span
                className={threads.data?.items.length ? "step done" : "step"}
              >
                {threads.data?.items.length ? (
                  <Check size={17} />
                ) : (
                  <Sparkles size={17} />
                )}
              </span>
              <div>
                <strong>Ask your first question</strong>
                <small>Inspect the citations and try a follow-up</small>
              </div>
              <ArrowRight size={16} />
            </Link>
          </div>
        </section>
      )}
      <ErrorNotice error={threads.error || documents.error || saved.error} />
      <div className="home-columns">
        <section className="panel">
          <div className="row between">
            <h2>Continue a conversation</h2>
            <Link className="text-link" to={`${base}/ask`}>
              View all
            </Link>
          </div>
          {threads.isPending ? (
            <Loading />
          ) : threads.data?.items.length ? (
            <div className="home-list">
              {threads.data.items.slice(0, 5).map((thread) => (
                <Link key={thread.id} to={`${base}/ask/${thread.id}`}>
                  <History size={18} />
                  <span>
                    <strong>{thread.title}</strong>
                    <small>Private · {when(thread.updated_at)}</small>
                  </span>
                  <ArrowRight size={15} />
                </Link>
              ))}
            </div>
          ) : (
            <EmptyState
              icon={<Sparkles size={22} />}
              title="A question is a good beginning"
              description="Your conversations stay private and can be continued whenever you return."
              action={
                <Link className="button secondary" to={`${base}/ask`}>
                  Start a conversation
                </Link>
              }
            />
          )}
        </section>
        <section className="panel">
          <div className="row between">
            <h2>Recently updated knowledge</h2>
            <Link className="text-link" to={`${base}/library`}>
              Open library
            </Link>
          </div>
          {documents.isPending ? (
            <Loading />
          ) : documents.data?.items.length ? (
            <div className="home-list">
              {documents.data.items.map((document) => (
                <Link key={document.id} to={`${base}/library/${document.id}`}>
                  <FileText size={18} />
                  <span>
                    <strong>{document.title}</strong>
                    <small>
                      {when(document.updated_at)} · {document.status}
                    </small>
                  </span>
                  <ArrowRight size={15} />
                </Link>
              ))}
            </div>
          ) : (
            <EmptyState
              icon={<BookOpen size={22} />}
              title="Build a shared starting point"
              description="Upload a document and Atlas will make it searchable for the people who can access it."
              action={
                canEdit ? (
                  <Link className="button secondary" to={`${base}/library`}>
                    Add knowledge
                  </Link>
                ) : undefined
              }
            />
          )}
        </section>
      </div>
      {!!saved.data?.items.length && (
        <section className="panel">
          <div className="row between">
            <h2>Saved for later</h2>
            <Link className="text-link" to={`${base}/saved`}>
              View saved items
            </Link>
          </div>
          <div className="saved-home-grid">
            {saved.data.items
              .filter((item) => item.available)
              .slice(0, 4)
              .map((item) => (
                <Link
                  className="saved-home-card"
                  key={item.id}
                  to={
                    item.kind === "document"
                      ? `${base}/library/${item.resource_id}`
                      : `${base}/ask/${item.conversation_id}`
                  }
                >
                  <Bookmark size={17} />
                  <strong>{item.title}</strong>
                  <small>
                    {item.kind === "document" ? "Document" : "Saved answer"}
                  </small>
                </Link>
              ))}
          </div>
        </section>
      )}
      {canManage && <WorkspaceHealth />}
    </>
  );
}
export function SearchPage() {
  const { tenantId } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const query = params.get("q") || "";
  const kind = params.get("kind") || "passages";
  const [draft, setDraft] = useState(query);
  const [space, setSpace] = useState(params.get("space") || "");
  const [source, setSource] = useState<Evidence | null>(null);
  const spaces = useQuery({
    queryKey: useScopedQueryKey("spaces"),
    queryFn: ({ signal }) =>
      api<{ id: string; name: string }[]>("/spaces", { tenantId, signal }),
  });
  const passages = useQuery({
    queryKey: useScopedQueryKey("search-passages", query, params.get("space")),
    queryFn: ({ signal }) =>
      api<{ sources: Evidence[] }>("/search", {
        tenantId,
        method: "POST",
        signal,
        body: json({
          question: query,
          mode: "hybrid",
          top_k: 10,
          space_ids: params.get("space") ? [params.get("space")] : [],
        }),
      }),
    enabled: query.trim().length >= 2 && kind === "passages",
    retry: false,
  });
  const metadata = useQuery({
    queryKey: useScopedQueryKey(
      "discovery-search",
      query,
      kind,
      params.get("space"),
    ),
    queryFn: ({ signal }) =>
      api<DiscoveryResult>(
        `/discovery?${new URLSearchParams({ q: query, kind, ...(params.get("space") ? { space_id: params.get("space")! } : {}) })}`,
        { tenantId, signal },
      ),
    enabled: query.trim().length >= 2 && kind !== "passages",
  });
  useEffect(() => {
    setDraft(query);
    setSpace(params.get("space") || "");
  }, [query, params.get("space")]);
  function submit(e: FormEvent) {
    e.preventDefault();
    setParams({ q: draft.trim(), kind, ...(space ? { space } : {}) });
  }
  return (
    <>
      <PageHeader
        title="Search your knowledge"
        description="Find passages, documents and your private conversations without generating an answer."
      />
      <form className="search-bar" onSubmit={submit}>
        <Search size={19} />
        <input
          aria-label="Search knowledge"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Search a topic, service or question…"
          minLength={2}
        />
        <select
          aria-label="Search space"
          value={space}
          onChange={(e) => setSpace(e.target.value)}
        >
          <option value="">All permitted spaces</option>
          {spaces.data?.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <Button disabled={draft.trim().length < 2}>Search</Button>
      </form>
      <div className="page-tabs">
        {[
          ["passages", "Passages"],
          ["documents", "Documents"],
          ["conversations", "Conversations"],
        ].map(([value, label]) => (
          <button
            key={value}
            className="link-button"
            aria-pressed={kind === value}
            onClick={() =>
              setParams({ q: query, kind: value, ...(space ? { space } : {}) })
            }
          >
            {label}
          </button>
        ))}
      </div>
      <ErrorNotice error={passages.error || metadata.error} />
      {query.trim().length < 2 ? (
        <EmptyState
          icon={<Search size={24} />}
          title="Find what you need"
          description="Search a phrase or keyword. Results include only knowledge you can currently access."
        />
      ) : kind === "passages" ? (
        passages.isPending ? (
          <Loading label="Finding matching passages…" />
        ) : passages.data?.sources.length ? (
          <div className="search-results">
            {passages.data.sources.map((item, index) => (
              <SourceCard
                key={item.id}
                source={item}
                index={index}
                onClick={() => setSource(item)}
              />
            ))}
          </div>
        ) : (
          <EmptyState
            title="No matching passages"
            description="Try broader terms or another space. If your source is new, check that indexing has finished."
          />
        )
      ) : metadata.isPending ? (
        <Loading />
      ) : kind === "documents" ? (
        metadata.data?.documents.length ? (
          <div className="search-document-list">
            {metadata.data.documents.map((document) => (
              <Link
                className="panel"
                key={document.id}
                to={`/o/${tenantId}/library/${document.id}`}
              >
                <FileText size={22} />
                <div>
                  <h2>{document.title}</h2>
                  <p>
                    {document.snippet ||
                      document.excerpt ||
                      document.content?.slice(0, 200) ||
                      "Open this document to inspect its contents and versions."}
                  </p>
                  <small>
                    {document.status} · {when(document.updated_at)}
                  </small>
                </div>
                <ArrowRight size={18} />
              </Link>
            ))}
          </div>
        ) : (
          <EmptyState
            title="No matching documents"
            description="Try another title or search passages to match document content."
          />
        )
      ) : metadata.data?.conversations.length ? (
        <div className="panel home-list">
          {metadata.data.conversations.map((thread) => (
            <Link key={thread.id} to={`/o/${tenantId}/ask/${thread.id}`}>
              <History size={18} />
              <span>
                <strong>{thread.title}</strong>
                <small>Private conversation · {when(thread.updated_at)}</small>
              </span>
              <ArrowRight size={15} />
            </Link>
          ))}
        </div>
      ) : (
        <EmptyState
          title="No matching conversations"
          description="Only your own private conversations are searched."
        />
      )}
      <EvidenceDialog source={source} onClose={() => setSource(null)} />
    </>
  );
}
export function SavedPage() {
  const { tenantId, user } = useWorkspace();
  const client = useQueryClient();
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState<SavedItem | null>(null);
  const [source, setSource] = useState<Evidence | null>(null);
  const [error, setError] = useState<unknown>();
  const saved = useQuery({
    queryKey: useScopedQueryKey("bookmarks"),
    queryFn: ({ signal }) =>
      api<{ items: SavedItem[] }>("/bookmarks", { tenantId, signal }),
    staleTime: 0,
    refetchOnWindowFocus: true,
  });
  useEffect(() => {
    if (saved.isFetching) setSelected(null);
  }, [saved.isFetching]);
  async function remove(id: string) {
    setError(null);
    try {
      await api(`/bookmarks/${id}`, { tenantId, method: "DELETE" });
      setSelected(null);
      await client.invalidateQueries({
        queryKey: ["workspace", user.id, tenantId],
      });
    } catch (e) {
      setError(e);
    }
  }
  return (
    <>
      <PageHeader
        title="Saved for later"
        description="The documents and answers you want to keep close. Access is rechecked on every visit."
      />
      <div className="page-tabs">
        {[
          ["all", "Everything"],
          ["document", "Documents"],
          ["message", "Answers"],
        ].map(([value, label]) => (
          <button
            className="link-button"
            aria-pressed={filter === value}
            key={value}
            onClick={() => setFilter(value)}
          >
            {label}
          </button>
        ))}
      </div>
      <ErrorNotice error={error || saved.error} />
      {saved.isPending || saved.isFetching ? (
        <Loading />
      ) : !saved.data?.items.length ? (
        <EmptyState
          icon={<Bookmark size={24} />}
          title="Keep the useful things close"
          description="Save an answer from a conversation or bookmark a document from the library."
        />
      ) : (
        <div className="card-grid">
          {saved.data.items
            .filter((item) => filter === "all" || item.kind === filter)
            .map((item) => (
              <section className="panel saved-card" key={item.id}>
                <div className="row between">
                  <Badge>
                    {item.kind === "document" ? "Document" : "Answer"}
                  </Badge>
                  <Button
                    variant="ghost"
                    aria-label={`Remove saved ${item.available ? item.title : "unavailable item"}`}
                    onClick={() => remove(item.id)}
                  >
                    <Trash2 size={15} />
                  </Button>
                </div>
                <h2>
                  {item.available ? item.title : "Saved content unavailable"}
                </h2>
                <p>
                  {item.available
                    ? item.content?.slice(0, 190) ||
                      "Open this saved item to continue."
                    : "The underlying knowledge was removed or your access has changed."}
                </p>
                {item.available &&
                  (item.kind === "document" ? (
                    <Link
                      className="text-link"
                      to={`/o/${tenantId}/library/${item.resource_id}`}
                    >
                      Open document
                      <ArrowRight size={15} />
                    </Link>
                  ) : (
                    <Button
                      variant="secondary"
                      onClick={async () => {
                        try {
                          const fresh = await saved.refetch();
                          const current = fresh.data?.items.find(
                            (i) => i.id === item.id,
                          );
                          if (current?.available) setSelected(current);
                          else
                            setError(
                              new Error(
                                "This saved answer is no longer accessible.",
                              ),
                            );
                        } catch (e) {
                          setError(e);
                        }
                      }}
                    >
                      Read saved answer
                    </Button>
                  ))}
              </section>
            ))}
        </div>
      )}
      <Modal
        open={!!selected}
        onOpenChange={(v) => !v && setSelected(null)}
        title={selected?.title || "Saved answer"}
        description="This answer is private to your account."
        wide
      >
        {selected && (
          <>
            <div className="saved-answer-text">{selected.content}</div>
            <div className="grant-chips">
              {selected.sources?.map((item, index) => (
                <Button
                  key={item.id}
                  variant="secondary"
                  onClick={() => setSource(item)}
                >
                  [{index + 1}] {item.title}
                </Button>
              ))}
            </div>
            {selected.conversation_id && (
              <Link
                className="button primary"
                to={`/o/${tenantId}/ask/${selected.conversation_id}`}
              >
                Continue conversation
                <ArrowRight size={15} />
              </Link>
            )}
          </>
        )}
      </Modal>
      <EvidenceDialog source={source} onClose={() => setSource(null)} />
    </>
  );
}
function notificationLink(tenantId: string, link: string) {
  if (
    !link ||
    !link.startsWith("/") ||
    link.startsWith("//") ||
    link.includes("\\")
  )
    return null;
  if (link.startsWith(`/o/${tenantId}/`)) return link;
  if (link.startsWith("/o/")) return null;
  if (link === "/settings/access") return `/o/${tenantId}/library`;
  return `/o/${tenantId}${link}`;
}
export function NotificationsPage() {
  const { tenantId, user } = useWorkspace();
  const client = useQueryClient();
  const [unread, setUnread] = useState(false);
  const [error, setError] = useState<unknown>();
  const notices = useQuery({
    queryKey: useScopedQueryKey("notifications"),
    queryFn: ({ signal }) =>
      api<{ items: Notification[] }>("/notifications", { tenantId, signal }),
    staleTime: 10000,
  });
  async function read(id?: string) {
    setError(null);
    try {
      await api(id ? `/notifications/${id}/read` : "/notifications/read-all", {
        tenantId,
        method: "POST",
      });
      await client.invalidateQueries({
        queryKey: ["workspace", user.id, tenantId],
      });
    } catch (e) {
      setError(e);
    }
  }
  const items = notices.data?.items.filter((n) => !unread || !n.read_at) || [];
  return (
    <>
      <PageHeader
        title="Notifications"
        description="Updates about your knowledge, access and workspace."
        action={
          <Button
            variant="secondary"
            disabled={!notices.data?.items.some((n) => !n.read_at)}
            onClick={() => read()}
          >
            Mark all as read
          </Button>
        }
      />
      <label className="checkbox-row">
        <input
          type="checkbox"
          checked={unread}
          onChange={(e) => setUnread(e.target.checked)}
        />
        Unread only
      </label>
      <ErrorNotice error={error || notices.error} />
      {notices.isPending ? (
        <Loading />
      ) : !items.length ? (
        <EmptyState
          icon={<Bell size={25} />}
          title="You’re all caught up"
          description="Indexing updates, invitations and review reminders will appear here."
        />
      ) : (
        <section className="panel notification-list">
          {items.map((item) => (
            <article key={item.id} className={item.read_at ? "read" : "unread"}>
              <div className="notification-icon">
                <Bell size={18} />
              </div>
              <div className="grow">
                <h2>{item.title}</h2>
                <p>{item.body}</p>
                <small>{new Date(item.created_at).toLocaleString()}</small>
              </div>
              <div className="row">
                {notificationLink(tenantId, item.link) && (
                  <Link
                    className="button secondary"
                    to={notificationLink(tenantId, item.link)!}
                    onClick={() => void read(item.id)}
                  >
                    Open
                  </Link>
                )}
                {!item.read_at && (
                  <Button
                    variant="ghost"
                    aria-label={`Mark ${item.title} as read`}
                    onClick={() => read(item.id)}
                  >
                    <Check size={17} />
                  </Button>
                )}
              </div>
            </article>
          ))}
        </section>
      )}
    </>
  );
}
export function NotificationButton() {
  const { tenantId } = useWorkspace();
  const data = useQuery({
    queryKey: useScopedQueryKey("notification-summary"),
    queryFn: ({ signal }) =>
      api<{ items: Notification[] }>("/notifications", { tenantId, signal }),
    staleTime: 30000,
    refetchOnWindowFocus: true,
  });
  const count = data.data?.items.filter((n) => !n.read_at).length || 0;
  return (
    <Link
      className="notification-button"
      to={`/o/${tenantId}/notifications`}
      aria-label={count ? `Notifications, ${count} unread` : "Notifications"}
    >
      <Bell size={18} />
      {count > 0 && <span>{count > 9 ? "9+" : count}</span>}
    </Link>
  );
}
export function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const { tenantId, canManage } = useWorkspace();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query), 250);
    return () => clearTimeout(timer);
  }, [query]);
  useEffect(() => {
    if (!open) {
      setQuery("");
      setDebounced("");
    }
  }, [open]);
  const results = useQuery({
    queryKey: useScopedQueryKey("command-search", debounced),
    queryFn: ({ signal }) =>
      api<DiscoveryResult>(
        `/discovery?${new URLSearchParams({ q: debounced, kind: "all" })}`,
        { tenantId, signal },
      ),
    enabled: open && debounced.trim().length >= 2,
  });
  const destinations = [
    ["Home", ""],
    ["Ask Atlas", "/ask"],
    ["Library", "/library"],
    ["Search", "/search"],
    ["Saved items", "/saved"],
    ["Inventory", "/inventory"],
    ["Sources", "/sources"],
    ["Compare", "/compare"],
    ["Knowledge Map", "/knowledge-map"],
    ["Playbooks", "/playbooks"],
    ["Briefings", "/briefings"],
    ["Notifications", "/notifications"],
    ...(canManage
      ? [
          ["People & teams", "/people"],
          ["Insights", "/insights"],
          ["Integrations", "/integrations"],
        ]
      : []),
  ];
  function go(path: string) {
    onOpenChange(false);
    navigate(`/o/${tenantId}${path}`);
  }
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Find your next step"
      description="Search permitted documents and your private conversations, or jump to a page."
    >
      <Field
        label="Search Atlas"
        autoFocus
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Type a document, conversation or page…"
      />
      <ErrorNotice error={results.error} />
      <div className="command-results">
        {destinations
          .filter(([name]) => name.toLowerCase().includes(query.toLowerCase()))
          .map(([name, path]) => (
            <button key={path} onClick={() => go(path)}>
              <Search size={15} />
              <span>{name}</span>
              <ArrowRight size={14} />
            </button>
          ))}
        {results.data?.documents.map((document) => (
          <button
            key={document.id}
            onClick={() => go(`/library/${document.id}`)}
          >
            <FileText size={15} />
            <span>
              {document.title}
              <small>Document</small>
            </span>
          </button>
        ))}
        {results.data?.conversations.map((thread) => (
          <button key={thread.id} onClick={() => go(`/ask/${thread.id}`)}>
            <History size={15} />
            <span>
              {thread.title}
              <small>Private conversation</small>
            </span>
          </button>
        ))}
        {results.isFetching && <Loading label="Searching…" />}
      </div>
      <p className="muted text-small">
        Use Tab to move, Enter to open and Escape to close.
      </p>
    </Modal>
  );
}
