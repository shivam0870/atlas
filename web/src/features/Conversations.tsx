import {
  EvidenceDialog,
  SourceCard,
  type Evidence,
} from "../components/Evidence";
export type { Evidence } from "../components/Evidence";
import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  Link,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowUp,
  BookOpen,
  Bookmark,
  Check,
  ChevronDown,
  Copy,
  Download,
  FileText,
  Filter,
  History,
  MoreHorizontal,
  PanelRightClose,
  PanelRightOpen,
  Pin,
  Plus,
  RefreshCw,
  Send,
  ShieldCheck,
  Sparkles,
  Square,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  X,
} from "lucide-react";
import { api, ApiError, json } from "../api/client";
import { RequestDiagnostics } from "./RequestDiagnostics";
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
} from "../components/ui";
export type Conversation = {
  id: string;
  title: string;
  pinned: boolean;
  archived: boolean;
  space_ids: string[];
  document_ids: string[];
  created_at: string;
  updated_at: string;
};
export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: string;
  sources: Evidence[];
  metadata: Record<string, unknown>;
  created_at: string;
};
type ConversationDetail = {
  conversation: Conversation;
  messages: Message[];
  pagination?: { has_older: boolean; before: string | null };
};
function citedIndices(content: string) {
  return new Set(
    [...content.matchAll(/\[(\d+)\]/g)].map((match) => Number(match[1]) - 1),
  );
}
function Answer({
  message,
  onSource,
}: {
  message: Message;
  onSource: (s: Evidence) => void;
}) {
  return (
    <div className="answer-prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          img: ({ alt }) => <span>{alt || "Image reference"}</span>,
          a: ({ href, children }) =>
            href?.startsWith("#source-") ? (
              <button
                className="citation"
                aria-label={`Open citation ${href.slice(8)}`}
                onClick={() => {
                  const source = message.sources[Number(href.slice(8)) - 1];
                  if (source) onSource(source);
                }}
              >
                {children}
              </button>
            ) : (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            ),
        }}
      >
        {message.content.replace(/\[(\d+)\](?!\()/g, "[$1](#source-$1)")}
      </ReactMarkdown>
    </div>
  );
}
export function ConversationsPage() {
  const { tenantId, user, organization } = useWorkspace();
  const { conversationId } = useParams();
  const [params, setParams] = useSearchParams();
  const before = params.get("before");
  const summaryDocument = params.get("summary");
  const navigate = useNavigate();
  const location = useLocation();
  const client = useQueryClient();
  const [threadSearch, setThreadSearch] = useState("");
  const [archiveView, setArchiveView] = useState(false);
  const [threadDrawer, setThreadDrawer] = useState(false);
  const threads = useQuery({
    queryKey: useScopedQueryKey("conversations", threadSearch, archiveView),
    queryFn: ({ signal }) =>
      api<{ items: Conversation[] }>(
        `/conversations?${new URLSearchParams({ q: threadSearch, archived: String(archiveView) })}`,
        { tenantId, signal },
      ),
  });
  const detail = useQuery({
    queryKey: useScopedQueryKey("conversation", conversationId, before),
    queryFn: ({ signal }) =>
      api<ConversationDetail>(
        `/conversations/${conversationId}?limit=100${before ? `&before=${encodeURIComponent(before)}` : ""}`,
        {
          tenantId,
          signal,
        },
      ),
    enabled: !!conversationId,
    retry: false,
    staleTime: 0,
    refetchOnMount: "always",
    refetchOnWindowFocus: true,
    refetchInterval: (q) =>
      q.state.data?.messages.some((m) =>
        ["running", "queued"].includes(m.status),
      )
        ? 2000
        : false,
  });
  const spaces = useQuery({
    queryKey: useScopedQueryKey("spaces"),
    queryFn: ({ signal }) =>
      api<{ id: string; name: string }[]>("/spaces", { tenantId, signal }),
  });
  const [scopeOpen, setScopeOpen] = useState(false);
  const [scopeSearch, setScopeSearch] = useState("");
  const documents = useQuery({
    queryKey: useScopedQueryKey("conversation-scope-documents", scopeSearch),
    queryFn: ({ signal }) =>
      api<{ items: { id: string; title: string; space_id: string }[] }>(
        `/library?page_size=100&lifecycle=active&q=${encodeURIComponent(scopeSearch)}`,
        { tenantId, signal },
      ),
    enabled: scopeOpen,
  });
  const [spaceIds, setSpaceIds] = useState<string[]>([]);
  const [documentIds, setDocumentIds] = useState<string[]>(
    summaryDocument ? [summaryDocument] : [],
  );
  const [question, setQuestion] = useState(
    params.get("q") ||
      (summaryDocument
        ? "Summarize this document with citations to its key points."
        : ""),
  );
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [stage, setStage] = useState("Finding sources");
  const [live, setLive] = useState<Message[]>([]);
  const [error, setError] = useState<unknown>();
  const [notice, setNotice] = useState("");
  const [mode, setMode] = useState("hybrid");
  const [rerank, setRerank] = useState(false);
  const [topK, setTopK] = useState(5);
  const [compact, setCompact] = useState(
    () => matchMedia("(max-width:1100px)").matches,
  );
  const [evidenceOpen, setEvidenceOpen] = useState(
    () => !matchMedia("(max-width:1100px)").matches,
  );
  useEffect(() => {
    const media = matchMedia("(max-width:1100px)");
    const change = () => {
      setCompact(media.matches);
      if (media.matches) setEvidenceOpen(false);
    };
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, []);
  const [evidenceWidth, setEvidenceWidth] = useState(310);
  const [source, setSource] = useState<Evidence | null>(null);
  const [activeAnswer, setActiveAnswer] = useState<string | null>(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<{
    message: Message;
    rating: number;
  } | null>(null);
  const [reason, setReason] = useState("unsupported");
  const [correction, setCorrection] = useState("");
  const [copied, setCopied] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const exportController = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const skipNavigationAbort = useRef<string | null>(null);
  const previousId = useRef(conversationId);
  const previousCursor = useRef(before);
  const activeThreadId = useRef<string | undefined>(conversationId);
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      controller.current?.abort();
      exportController.current?.abort();
    };
  }, []);
  useEffect(() => {
    activeThreadId.current = conversationId;
    if (previousId.current !== conversationId) {
      if (skipNavigationAbort.current === conversationId)
        skipNavigationAbort.current = null;
      else {
        controller.current?.abort();
        busyRef.current = false;
        setBusy(false);
        setLive([]);
        setSource(null);
        setActiveAnswer(null);
        setError(null);
        setNotice("");
        setQuestion(params.get("q") || "");
        setSpaceIds([]);
        setDocumentIds([]);
      }
      previousId.current = conversationId;
    }
  }, [conversationId]);
  useEffect(() => {
    if (previousCursor.current !== before) {
      controller.current?.abort();
      busyRef.current = false;
      setBusy(false);
      setLive([]);
      setSource(null);
      setActiveAnswer(null);
      setError(null);
      setNotice("");
      previousCursor.current = before;
    }
  }, [before]);
  useEffect(() => {
    if (detail.data && !busyRef.current) {
      setSpaceIds(detail.data.conversation.space_ids);
      setDocumentIds(detail.data.conversation.document_ids);
      setTitle(detail.data.conversation.title);
    }
  }, [detail.data]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest", behavior: "auto" });
  }, [live.length, busy]);
  const messages = detail.error
    ? []
    : busy
      ? live
      : detail.isFetching
        ? []
        : detail.data?.messages || live;
  const pendingSaved = !!detail.data?.messages.some((message) =>
    ["running", "queued"].includes(message.status),
  );
  const evidenceMessage =
    messages.find((m) => m.id === activeAnswer) ||
    [...messages]
      .reverse()
      .find((m) => m.role === "assistant" && m.status !== "unavailable");
  const cited = citedIndices(evidenceMessage?.content || "");
  async function refresh() {
    await client.invalidateQueries({
      queryKey: ["workspace", user.id, tenantId],
    });
  }
  async function mutate(action: () => Promise<void>) {
    setSaving(true);
    setError(null);
    try {
      await action();
      await refresh();
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  }
  async function send(text: string, regenerate = false) {
    if (busyRef.current || !text.trim()) return;
    if (before) {
      setNotice("Return to the latest messages before starting a follow-up.");
      return;
    }
    if (pendingSaved) {
      setNotice(
        "An earlier answer is still running. Stop it or wait for its saved result before starting another.",
      );
      return;
    }
    busyRef.current = true;
    setBusy(true);
    setError(null);
    setNotice("");
    setStage("Queued");
    setQuestion("");
    const abort = new AbortController();
    controller.current = abort;
    let id = conversationId;
    let optimisticAssistant = crypto.randomUUID();
    const original = detail.data?.messages || [];
    setLive([
      ...original,
      ...(regenerate
        ? []
        : [
            {
              id: crypto.randomUUID(),
              role: "user" as const,
              content: text,
              status: "completed",
              sources: [],
              metadata: {},
              created_at: new Date().toISOString(),
            },
          ]),
      {
        id: optimisticAssistant,
        role: "assistant",
        content: "",
        status: "running",
        sources: [],
        metadata: {},
        created_at: new Date().toISOString(),
      },
    ]);
    const update = (fn: (message: Message) => Message) => {
      if (mounted.current && !abort.signal.aborted)
        setLive((current) =>
          current.map((m) => (m.id === optimisticAssistant ? fn(m) : m)),
        );
    };
    let completed = false;
    try {
      if (!id) {
        const result = await api<{ conversation: Conversation }>(
          "/conversations",
          {
            tenantId,
            method: "POST",
            signal: abort.signal,
            body: json({
              title: text.slice(0, 90),
              space_ids: spaceIds,
              document_ids: documentIds,
            }),
          },
        );
        id = result.conversation.id;
        activeThreadId.current = id;
        skipNavigationAbort.current = id;
        navigate(`/o/${tenantId}/ask/${id}`, { replace: true });
      }
      const response = await fetch("/api/query", {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "X-Atlas-Client": "console",
          "X-Atlas-Tenant": tenantId,
        },
        signal: abort.signal,
        body: json({
          question: text,
          conversation_id: id,
          idempotency_key: crypto.randomUUID(),
          regenerate,
          mode,
          rerank,
          top_k: topK,
          use_cache: true,
          space_ids: spaceIds,
          document_ids: documentIds,
          summary_document_id: summaryDocument || undefined,
        }),
      });
      if (!response.ok) {
        const body = await response
          .json()
          .catch(() => ({ detail: "The answer could not be started." }));
        if (response.status === 401)
          window.dispatchEvent(new Event("atlas:session-expired"));
        throw new ApiError(
          response.status,
          typeof body.detail === "string"
            ? body.detail
            : "The answer could not be started.",
        );
      }
      if (!response.body)
        throw new Error("The server did not provide an answer stream.");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder
          .decode(chunk.value, { stream: true })
          .replace(/\r\n/g, "\n");
        let boundary: number;
        while ((boundary = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const event = frame.match(/^event:\s*(.+)$/m)?.[1];
          const dataLines = frame
            .split("\n")
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart())
            .join("\n");
          if (!dataLines) continue;
          const data = JSON.parse(dataLines);
          if (data.reset) {
            setLive((current) =>
              current.map((message) =>
                message.role === "assistant"
                  ? {
                      ...message,
                      content: "",
                      sources: [],
                      status: "unavailable",
                    }
                  : message,
              ),
            );
            setSource(null);
            setActiveAnswer(null);
            setError(
              new Error(
                data.message ||
                  "Knowledge access changed. Previously delivered evidence has been cleared.",
              ),
            );
            void client.invalidateQueries({ queryKey: ["organizations"] });
            if (event === "done") completed = true;
            continue;
          }
          if (event === "metadata") {
            if (data.assistant_message_id) {
              const nextId = data.assistant_message_id;
              const previousMessageId = optimisticAssistant;
              setLive((current) =>
                current.map((m) =>
                  m.id === previousMessageId ? { ...m, id: nextId } : m,
                ),
              );
              optimisticAssistant = nextId;
            }
            if (data.stage)
              setStage(
                data.stage === "generating"
                  ? "Writing your answer"
                  : data.stage === "agent"
                    ? "Checking records and documents"
                    : data.stage === "cached"
                      ? "Opening a verified cached answer"
                      : "Finding sources",
              );
          } else if (event === "delta")
            update((m) => ({ ...m, content: m.content + data.text }));
          else if (event === "sources")
            update((m) => ({ ...m, sources: data.sources || [] }));
          else if (event === "step")
            update((m) => ({
              ...m,
              metadata: {
                ...m.metadata,
                steps: [...((m.metadata.steps as unknown[]) || []), data],
              },
            }));
          else if (event === "warning") setNotice(data.message);
          else if (event === "error") {
            setError(
              new Error(data.message || "The answer could not be completed."),
            );
            update((m) => ({ ...m, status: "failed" }));
          } else if (event === "done") {
            completed = true;
            update((m) => ({
              ...m,
              status: data.status,
              metadata: { ...m.metadata, ...data },
            }));
            setStage("Complete");
          }
        }
      }
      if (!completed && !abort.signal.aborted)
        setNotice(
          "The connection ended before completion. Atlas will show the saved attempt; it will not automatically repeat your question.",
        );
    } catch (e) {
      if (mounted.current) {
        if (abort.signal.aborted)
          setNotice(
            "Generation stopped. Any completed text remains in the saved attempt.",
          );
        else setError(e);
      }
    } finally {
      if (mounted.current) {
        busyRef.current = false;
        await refresh();
        if (mounted.current) setBusy(false);
        if (id)
          await client.invalidateQueries({
            queryKey: [
              "workspace",
              user.id,
              tenantId,
              organization.auth_revision,
              "conversation",
              id,
            ],
          });
      }
    }
  }
  async function stop() {
    const id = activeThreadId.current;
    if (id) {
      try {
        await api(`/conversations/${id}/stop`, { tenantId, method: "POST" });
      } catch (e) {
        setError(e);
      }
    }
    controller.current?.abort();
    busyRef.current = false;
    setBusy(false);
  }
  async function exportConversation() {
    if (!conversationId) return;
    await mutate(async () => {
      exportController.current?.abort();
      const transfer = new AbortController();
      exportController.current = transfer;
      const response = await fetch(
        `/api/conversations/${conversationId}/export`,
        {
          credentials: "include",
          signal: transfer.signal,
          headers: { "X-Atlas-Client": "console", "X-Atlas-Tenant": tenantId },
        },
      );
      if (!response.ok) {
        if (response.status === 401)
          window.dispatchEvent(new Event("atlas:session-expired"));
        const body = await response
          .json()
          .catch(() => ({ detail: "Export unavailable" }));
        throw new Error(body.detail);
      }
      const blob = await response.blob();
      if (transfer.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `atlas-${conversationId.slice(0, 8)}.md`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  const threadList = (
    <>
      <div className="row between">
        <strong>Conversations</strong>
        <Button
          variant="ghost"
          aria-label="New conversation"
          onClick={() => {
            controller.current?.abort();
            navigate(`/o/${tenantId}/ask`);
            setThreadDrawer(false);
          }}
        >
          <Plus size={17} />
        </Button>
      </div>
      <input
        aria-label="Search conversations"
        placeholder="Find a conversation…"
        value={threadSearch}
        onChange={(e) => setThreadSearch(e.target.value)}
      />
      <label className="checkbox-row">
        <input
          type="checkbox"
          checked={archiveView}
          onChange={(e) => setArchiveView(e.target.checked)}
        />
        Archived
      </label>
      <ErrorNotice error={threads.error} />
      <div className="thread-list">
        {threads.data?.items.map((thread) => (
          <Link
            className={thread.id === conversationId ? "active" : ""}
            key={thread.id}
            to={`/o/${tenantId}/ask/${thread.id}`}
            onClick={() => setThreadDrawer(false)}
          >
            {thread.pinned && <Pin size={12} />}
            <span>{thread.title}</span>
            <small>
              {new Date(thread.updated_at).toLocaleDateString(undefined, {
                month: "short",
                day: "numeric",
              })}
            </small>
          </Link>
        ))}
        {!threads.isPending && !threads.data?.items.length && (
          <p className="muted text-small">
            {threadSearch
              ? "No matching conversations."
              : "Your private conversations will appear here."}
          </p>
        )}
      </div>
      <div className="thread-privacy">
        <ShieldCheck size={14} />
        <span>Only you can read these conversations.</span>
      </div>
    </>
  );
  return (
    <div
      className={`conversation-workspace ${evidenceOpen ? "show-evidence" : ""}`}
    >
      <aside className="conversation-sidebar">{threadList}</aside>
      <section className="conversation-main">
        <header className="conversation-header">
          <Button
            className="mobile-threads"
            variant="ghost"
            aria-label="Open conversations"
            onClick={() => setThreadDrawer(true)}
          >
            <History size={19} />
          </Button>
          <div className="grow">
            <h1>{detail.data?.conversation.title || "Ask Atlas"}</h1>
            <span className="muted text-small">
              Private conversation · {organization.name}
            </span>
          </div>
          {conversationId && (
            <details className="thread-actions">
              <summary aria-label="Conversation actions">
                <MoreHorizontal size={20} />
              </summary>
              <div>
                <Button
                  variant="ghost"
                  onClick={() => {
                    setTitle(detail.data?.conversation.title || "");
                    setRenameOpen(true);
                  }}
                >
                  Rename
                </Button>
                <Button
                  variant="ghost"
                  onClick={() =>
                    mutate(async () => {
                      await api(`/conversations/${conversationId}`, {
                        tenantId,
                        method: "PATCH",
                        body: json({
                          pinned: !detail.data?.conversation.pinned,
                        }),
                      });
                    })
                  }
                >
                  {detail.data?.conversation.pinned ? "Unpin" : "Pin"}
                </Button>
                <Button
                  variant="ghost"
                  onClick={() =>
                    mutate(async () => {
                      await api(`/conversations/${conversationId}`, {
                        tenantId,
                        method: "PATCH",
                        body: json({
                          archived: !detail.data?.conversation.archived,
                        }),
                      });
                    })
                  }
                >
                  {detail.data?.conversation.archived ? "Unarchive" : "Archive"}
                </Button>
                <Button variant="ghost" onClick={exportConversation}>
                  <Download size={15} />
                  Export Markdown
                </Button>
                <Button variant="ghost" onClick={() => setDeleteOpen(true)}>
                  Delete
                </Button>
              </div>
            </details>
          )}
          <Button
            variant="ghost"
            aria-label={
              evidenceOpen ? "Hide evidence panel" : "Show evidence panel"
            }
            onClick={() => setEvidenceOpen((v) => !v)}
          >
            {evidenceOpen ? (
              <PanelRightClose size={19} />
            ) : (
              <PanelRightOpen size={19} />
            )}
          </Button>
        </header>
        <div className="conversation-scroll">
          <ErrorNotice error={error || detail.error} />
          {notice && <Notice>{notice}</Notice>}
          {(before || detail.data?.pagination?.has_older) && (
            <nav className="row" aria-label="Conversation pages">
              {!!detail.data?.pagination?.has_older && (
                <Button
                  variant="secondary"
                  disabled={busy || pendingSaved || detail.isFetching}
                  onClick={() => {
                    const next = new URLSearchParams(params);
                    next.set("before", detail.data!.pagination!.before!);
                    setParams(next);
                  }}
                >
                  Older messages
                </Button>
              )}
              {!!before && (
                <Button
                  variant="secondary"
                  disabled={busy}
                  onClick={() => {
                    const next = new URLSearchParams(params);
                    next.delete("before");
                    setParams(next);
                  }}
                >
                  Latest messages
                </Button>
              )}
              <span className="muted text-small">
                {before
                  ? "Earlier conversation history"
                  : "Latest 100 messages"}
              </span>
            </nav>
          )}
          {conversationId && detail.isFetching && !busy ? (
            <Loading label="Rechecking conversation access…" />
          ) : !messages.length && !detail.isPending ? (
            <div className="conversation-welcome">
              <div className="welcome-symbol">
                <Sparkles size={30} />
              </div>
              <span className="eyebrow">CLARITY STARTS WITH A QUESTION</span>
              <h2>
                What would you like
                <br />
                to understand?
              </h2>
              <p>
                Ask about your team’s documents and service records. Follow the
                citations to see where an answer comes from.
              </p>
              <div className="suggested-questions">
                {[
                  "How does our release process work?",
                  "Who owns our services?",
                  "Summarize the key points in our runbooks.",
                ].map((prompt) => (
                  <button key={prompt} onClick={() => setQuestion(prompt)}>
                    {prompt}
                    <ArrowUp size={15} />
                  </button>
                ))}
              </div>
            </div>
          ) : detail.isPending && !busy && conversationId ? (
            <Loading label="Opening your conversation…" />
          ) : (
            messages.map((message, index) => (
              <article
                key={message.id}
                className={`conversation-message ${message.role}`}
              >
                <div className="message-avatar">
                  {message.role === "user" ? (
                    user.name.slice(0, 1)
                  ) : (
                    <Sparkles size={17} />
                  )}
                </div>
                <div className="message-body">
                  <div className="message-author">
                    {message.role === "user" ? user.name : "Atlas"}
                    {message.role === "assistant" &&
                      message.status === "unavailable" && (
                        <Badge>Evidence unavailable</Badge>
                      )}
                  </div>
                  {message.status === "unavailable" ? (
                    <div className="notice">
                      This answer is unavailable because its supporting
                      knowledge is no longer accessible.
                    </div>
                  ) : message.role === "user" ? (
                    <p className="user-question">{message.content}</p>
                  ) : (
                    <>
                      {!!message.content && (
                        <Answer message={message} onSource={setSource} />
                      )}
                      {message.metadata.query_status === "uncited" && (
                        <Notice>
                          The model did not provide valid citations. Verify this
                          answer against the source passages.
                        </Notice>
                      )}
                      {message.metadata.query_status === "abstained" && (
                        <div className="row">
                          <Button
                            variant="secondary"
                            onClick={() => setScopeOpen(true)}
                          >
                            Review knowledge scope
                          </Button>
                          <Link
                            className="text-link"
                            to={`/o/${tenantId}/library`}
                          >
                            Add or inspect knowledge
                          </Link>
                        </div>
                      )}
                      {message.status === "failed" && (
                        <Notice>
                          This attempt could not finish. Retrying starts a
                          separate, explicitly recorded attempt.
                        </Notice>
                      )}
                      {message.status === "cancelled" && (
                        <p className="muted text-small">
                          Stopped before completion. Any text above is a partial
                          answer.
                        </p>
                      )}
                      {!message.content &&
                        ["running", "queued"].includes(message.status) && (
                          <div className="answer-working">
                            <span className="pulse-dot" />
                            {busy
                              ? stage
                              : "An earlier attempt is still being reconciled."}
                          </div>
                        )}
                      {message.sources.length > 0 && (
                        <button
                          className="answer-sources-button"
                          onClick={() => {
                            setActiveAnswer(message.id);
                            setEvidenceOpen(true);
                          }}
                        >
                          <BookOpen size={14} />
                          {citedIndices(message.content).size}{" "}
                          {citedIndices(message.content).size === 1
                            ? "citation"
                            : "citations"}{" "}
                          · {message.sources.length}{" "}
                          {message.sources.length === 1
                            ? "passage"
                            : "passages"}
                        </button>
                      )}
                      {!busy && message.status !== "running" && (
                        <div className="message-actions">
                          <Button
                            variant="ghost"
                            aria-label="Copy answer"
                            onClick={async () => {
                              try {
                                await navigator.clipboard.writeText(
                                  message.content,
                                );
                                setCopied(message.id);
                              } catch {
                                setError(
                                  new Error(
                                    "Clipboard access is unavailable. Select the answer text to copy it.",
                                  ),
                                );
                              }
                            }}
                          >
                            {copied === message.id ? (
                              <Check size={14} />
                            ) : (
                              <Copy size={14} />
                            )}
                          </Button>
                          <Button
                            variant="ghost"
                            aria-label="Save answer"
                            onClick={() =>
                              mutate(async () => {
                                await api("/bookmarks", {
                                  tenantId,
                                  method: "POST",
                                  body: json({
                                    kind: "message",
                                    resource_id: message.id,
                                    title:
                                      detail.data?.conversation.title ||
                                      "Saved answer",
                                  }),
                                });
                                setNotice(
                                  "Answer saved. Access is checked whenever you open it.",
                                );
                              })
                            }
                          >
                            <Bookmark size={14} />
                          </Button>
                          <Button
                            variant="ghost"
                            aria-label="Helpful answer"
                            onClick={() => {
                              setFeedback({ message, rating: 1 });
                              setCorrection("");
                              setReason("helpful");
                            }}
                          >
                            <ThumbsUp size={14} />
                          </Button>
                          <Button
                            variant="ghost"
                            aria-label="Report answer problem"
                            onClick={() => {
                              setFeedback({ message, rating: -1 });
                              setCorrection("");
                              setReason("unsupported");
                            }}
                          >
                            <ThumbsDown size={14} />
                          </Button>
                          {!before && index === messages.length - 1 && (
                            <Button
                              variant="ghost"
                              onClick={() => {
                                const previous = [...messages.slice(0, index)]
                                  .reverse()
                                  .find((m) => m.role === "user");
                                if (previous) void send(previous.content, true);
                              }}
                            >
                              <RefreshCw size={14} />
                              {["failed", "cancelled"].includes(message.status)
                                ? "Retry"
                                : "Regenerate"}
                            </Button>
                          )}
                          <Badge>{message.status.replaceAll("_", " ")}</Badge>
                        </div>
                      )}
                      {!busy && message.status !== "running" && (
                        <RequestDiagnostics message={message} />
                      )}
                    </>
                  )}
                </div>
              </article>
            ))
          )}
          <div ref={bottom} />
        </div>
        <div className="composer-area">
          {before ? (
            <div className="row">
              <p className="muted">
                You’re reading an earlier page. Continue your conversation from
                the latest messages.
              </p>
              <Button
                variant="secondary"
                onClick={() => {
                  const next = new URLSearchParams(params);
                  next.delete("before");
                  setParams(next);
                }}
              >
                Return to latest
              </Button>
            </div>
          ) : (
            <>
              {summaryDocument && !conversationId && (
                <p className="muted text-small">
                  Generate a cited summary of the selected document. This
                  explicit action uses your workspace’s token budget.
                </p>
              )}
              <div className="composer-options">
                <Button
                  variant="ghost"
                  disabled={busy}
                  onClick={() => setScopeOpen(true)}
                >
                  <Filter size={14} />
                  {documentIds.length
                    ? `${documentIds.length} documents`
                    : spaceIds.length
                      ? `${spaceIds.length} spaces`
                      : "All permitted knowledge"}
                  <ChevronDown size={13} />
                </Button>
                <details className="advanced-options">
                  <summary>Advanced</summary>
                  <div className="form-stack">
                    <label className="field">
                      <span>Answer mode</span>
                      <select
                        value={mode}
                        disabled={busy}
                        onChange={(e) => setMode(e.target.value)}
                      >
                        <option value="hybrid">Documents</option>
                        <option value="agent">Documents & service tools</option>
                        <option value="vector">
                          Vector retrieval experiment
                        </option>
                      </select>
                    </label>
                    <Field
                      label="Passage limit"
                      type="number"
                      min={1}
                      max={10}
                      value={topK}
                      disabled={busy}
                      onChange={(e) => setTopK(Number(e.target.value))}
                    />
                    <label className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={rerank}
                        disabled={busy}
                        onChange={(e) => setRerank(e.target.checked)}
                      />
                      Optional reranker
                    </label>
                  </div>
                </details>
              </div>
              <form
                className="question-composer"
                onSubmit={(e) => {
                  e.preventDefault();
                  void send(question);
                }}
              >
                <textarea
                  aria-label="Your question"
                  value={question}
                  maxLength={2000}
                  placeholder={
                    messages.length
                      ? "Ask a follow-up question…"
                      : "Ask anything about your knowledge…"
                  }
                  onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      if (!busy) void send(question);
                    }
                  }}
                />
                {busy || pendingSaved ? (
                  <Button
                    variant="secondary"
                    aria-label="Stop generation"
                    onClick={stop}
                    type="button"
                  >
                    <Square size={16} />
                  </Button>
                ) : (
                  <Button
                    aria-label={
                      summaryDocument && !conversationId
                        ? "Generate summary"
                        : "Send question"
                    }
                    disabled={question.trim().length < 2 || !!detail.error}
                    type="submit"
                  >
                    <ArrowUp size={19} />
                  </Button>
                )}
              </form>
              <div className="composer-note">
                <span>
                  {busy
                    ? stage
                    : "Atlas can make mistakes. Check the supporting evidence."}
                </span>
                <span>Enter to send · Shift + Enter for a new line</span>
              </div>
            </>
          )}
        </div>
      </section>
      {evidenceOpen && (
        <aside className="evidence-panel" style={{ width: evidenceWidth }}>
          <div
            className="evidence-resizer"
            role="separator"
            aria-label="Resize evidence panel"
            aria-orientation="vertical"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "ArrowLeft")
                setEvidenceWidth((w) => Math.min(500, w + 20));
              if (e.key === "ArrowRight")
                setEvidenceWidth((w) => Math.max(260, w - 20));
            }}
            onPointerDown={(e) => {
              const startX = e.clientX;
              const startWidth = evidenceWidth;
              e.currentTarget.setPointerCapture(e.pointerId);
              const target = e.currentTarget;
              target.onpointermove = (event) =>
                setEvidenceWidth(
                  Math.min(
                    500,
                    Math.max(260, startWidth + startX - event.clientX),
                  ),
                );
              target.onpointerup = () => {
                target.onpointermove = null;
                target.onpointerup = null;
              };
            }}
          />
          <div className="row between">
            <h2>Supporting evidence</h2>
            <Button
              variant="ghost"
              aria-label="Close evidence panel"
              onClick={() => setEvidenceOpen(false)}
            >
              <X size={17} />
            </Button>
          </div>
          <p className="muted text-small">
            Inspect the passages behind this answer.
          </p>
          {evidenceMessage?.sources.length ? (
            <>
              <h3>Cited passages</h3>
              {evidenceMessage.sources.map((item, index) =>
                cited.has(index) ? (
                  <SourceCard
                    key={item.id}
                    source={item}
                    index={index}
                    onClick={() => setSource(item)}
                  />
                ) : null,
              )}
              {!cited.size && (
                <p className="muted text-small">
                  The answer has not cited a passage yet.
                </p>
              )}
              <details>
                <summary>
                  Additional retrieved passages (
                  {evidenceMessage.sources.length - cited.size})
                </summary>
                {evidenceMessage.sources.map((item, index) =>
                  !cited.has(index) ? (
                    <SourceCard
                      key={item.id}
                      source={item}
                      index={index}
                      onClick={() => setSource(item)}
                    />
                  ) : null,
                )}
              </details>
            </>
          ) : (
            <EmptyState
              icon={<BookOpen size={22} />}
              title="Evidence appears here"
              description="Ask a question to find supporting passages in your knowledge."
            />
          )}
        </aside>
      )}
      <Modal
        open={compact && evidenceOpen}
        onOpenChange={setEvidenceOpen}
        title="Supporting evidence"
        description="Cited sources appear first. Open a passage to inspect its original version."
      >
        {evidenceMessage?.sources.length ? (
          <>
            {evidenceMessage.sources.map((item, index) =>
              cited.has(index) ? (
                <SourceCard
                  key={item.id}
                  source={item}
                  index={index}
                  onClick={() => {
                    setEvidenceOpen(false);
                    setSource(item);
                  }}
                />
              ) : null,
            )}
            <details>
              <summary>Additional retrieved passages</summary>
              {evidenceMessage.sources.map((item, index) =>
                !cited.has(index) ? (
                  <SourceCard
                    key={item.id}
                    source={item}
                    index={index}
                    onClick={() => {
                      setEvidenceOpen(false);
                      setSource(item);
                    }}
                  />
                ) : null,
              )}
            </details>
          </>
        ) : (
          <EmptyState
            title="Evidence appears here"
            description="Ask a question to find supporting passages."
          />
        )}
      </Modal>
      <EvidenceDialog source={source} onClose={() => setSource(null)} />
      <Modal
        open={threadDrawer}
        onOpenChange={setThreadDrawer}
        title="Your conversations"
        description="Private to your account."
      >
        {threadList}
      </Modal>
      <Modal
        open={scopeOpen}
        onOpenChange={setScopeOpen}
        title="Choose your knowledge scope"
        description="Atlas always checks your access. These filters further narrow the evidence used."
      >
        <div className="form-stack">
          <ErrorNotice error={error} />
          <fieldset className="fieldset">
            <legend>Spaces</legend>
            {spaces.data?.map((space) => (
              <label className="checkbox-row" key={space.id}>
                <input
                  type="checkbox"
                  checked={spaceIds.includes(space.id)}
                  onChange={(e) =>
                    setSpaceIds(
                      e.target.checked
                        ? [...spaceIds, space.id]
                        : spaceIds.filter((id) => id !== space.id),
                    )
                  }
                />
                {space.name}
              </label>
            ))}
          </fieldset>
          <details>
            <summary>Choose specific documents</summary>
            <Field
              label="Find a document for this conversation"
              value={scopeSearch}
              onChange={(e) => setScopeSearch(e.target.value)}
              placeholder="Search document titles…"
            />
            <div className="member-checklist">
              {documents.data?.items
                .filter(
                  (d) => !spaceIds.length || spaceIds.includes(d.space_id),
                )
                .map((document) => (
                  <label className="checkbox-row" key={document.id}>
                    <input
                      type="checkbox"
                      checked={documentIds.includes(document.id)}
                      onChange={(e) =>
                        setDocumentIds(
                          e.target.checked
                            ? [...documentIds, document.id]
                            : documentIds.filter((id) => id !== document.id),
                        )
                      }
                    />
                    {document.title}
                  </label>
                ))}
            </div>
          </details>
          <div className="row">
            <Button
              busy={saving}
              onClick={() =>
                mutate(async () => {
                  if (conversationId)
                    await api(`/conversations/${conversationId}`, {
                      tenantId,
                      method: "PATCH",
                      body: json({
                        space_ids: spaceIds,
                        document_ids: documentIds,
                      }),
                    });
                  setScopeOpen(false);
                })
              }
            >
              Apply scope
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                setSpaceIds([]);
                setDocumentIds([]);
              }}
            >
              Clear selection
            </Button>
          </div>
        </div>
      </Modal>
      <Modal
        open={renameOpen}
        onOpenChange={setRenameOpen}
        title="Rename conversation"
      >
        <form
          className="form-stack"
          onSubmit={(e) => {
            e.preventDefault();
            void mutate(async () => {
              await api(`/conversations/${conversationId}`, {
                tenantId,
                method: "PATCH",
                body: json({ title }),
              });
              setRenameOpen(false);
            });
          }}
        >
          <ErrorNotice error={error} />
          <Field
            label="Conversation title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={150}
            required
          />
          <Button busy={saving}>Save title</Button>
        </form>
      </Modal>
      <Modal
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="Delete this conversation?"
        description="This removes your private conversation and its saved messages."
      >
        <ErrorNotice error={error} />
        <Button
          variant="danger"
          busy={saving}
          onClick={() =>
            mutate(async () => {
              controller.current?.abort();
              await api(`/conversations/${conversationId}`, {
                tenantId,
                method: "DELETE",
              });
              setDeleteOpen(false);
              navigate(`/o/${tenantId}/ask`);
            })
          }
        >
          Delete conversation
        </Button>
      </Modal>
      <Modal
        open={!!feedback}
        onOpenChange={(v) => !v && setFeedback(null)}
        title={
          feedback?.rating === 1
            ? "Share helpful feedback"
            : "Report an answer problem"
        }
        description="Feedback shares this question and selected evidence with company administrators. Your full private conversation is not shared."
      >
        <form
          className="form-stack"
          onSubmit={(e) => {
            e.preventDefault();
            void mutate(async () => {
              await api("/feedback", {
                tenantId,
                method: "POST",
                body: json({
                  message_id: feedback?.message.id,
                  rating: feedback?.rating,
                  reason,
                  correction,
                }),
              });
              setFeedback(null);
              setNotice("Feedback submitted for review.");
            });
          }}
        >
          <ErrorNotice error={error} />
          {feedback?.rating === -1 && (
            <label className="field">
              <span>What went wrong?</span>
              <select
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              >
                <option value="no_source">No source found</option>
                <option value="wrong_source">Wrong source</option>
                <option value="unsupported">Unsupported answer</option>
                <option value="stale">Stale knowledge</option>
                <option value="permission">Permission problem</option>
              </select>
            </label>
          )}
          <label className="field">
            <span>Correction or comment (optional)</span>
            <textarea
              value={correction}
              maxLength={2000}
              onChange={(e) => setCorrection(e.target.value)}
            />
          </label>
          <Button busy={saving}>Submit feedback</Button>
        </form>
      </Modal>
    </div>
  );
}
