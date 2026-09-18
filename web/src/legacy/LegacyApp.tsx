import React, { useEffect, useRef, useState } from "react";

import {
  Activity,
  ArrowUp,
  ArrowUpRight,
  BookOpen,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  Compass,
  Copy,
  Database,
  ExternalLink,
  FileText,
  FlaskConical,
  KeyRound,
  Layers3,
  LoaderCircle,
  LogOut,
  PanelLeft,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Square,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./legacy.css";

type Row = Record<string, any>;
type Page =
  "overview" | "ask" | "documents" | "evaluate" | "activity" | "settings";
const nav: { id: Page; label: string; icon: React.ElementType }[] = [
  { id: "overview", label: "Overview", icon: Layers3 },
  { id: "ask", label: "Ask Atlas", icon: Sparkles },
  { id: "documents", label: "Documents", icon: BookOpen },
  { id: "evaluate", label: "Evaluation", icon: FlaskConical },
  { id: "activity", label: "Activity", icon: Activity },
  { id: "settings", label: "Settings", icon: Settings2 },
];
const fmt = (n: any) =>
  n === null || n === undefined
    ? "—"
    : Number(n).toLocaleString(undefined, { maximumFractionDigits: 1 });
const ago = (date: string) => {
  const mins = Math.max(
    0,
    Math.floor((Date.now() - new Date(date).getTime()) / 60000),
  );
  return mins < 1
    ? "Just now"
    : mins < 60
      ? `${mins}m ago`
      : mins < 1440
        ? `${Math.floor(mins / 60)}h ago`
        : new Date(date).toLocaleDateString(undefined, {
            month: "short",
            day: "numeric",
          });
};
async function api(path: string, options: RequestInit = {}) {
  const response = await fetch("/api" + path, {
    ...options,
    headers: {
      "X-Atlas-Client": "console",
      ...(options.body && !(options.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    let message = "Something went wrong";
    try {
      const data = await response.json();
      message =
        typeof data.detail === "string"
          ? data.detail
          : JSON.stringify(data.detail);
    } catch {}
    throw new Error(message);
  }
  return response.json();
}
function Badge({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: string;
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
function Empty({
  icon: Icon = BookOpen,
  title,
  description,
  action,
}: {
  icon?: React.ElementType;
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Icon size={25} />
      </div>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
function Modal({
  title,
  subtitle,
  children,
  close,
  wide = false,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  close: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      if (e.key === "Tab") {
        const elements = ref.current?.querySelectorAll<HTMLElement>(
          "button,input,textarea,select,a[href]",
        );
        if (!elements?.length) return;
        const first = elements[0],
          last = elements[elements.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", handler);
    ref.current?.querySelector<HTMLElement>("input,textarea,button")?.focus();
    return () => {
      document.removeEventListener("keydown", handler);
      prev?.focus();
    };
  }, []);
  return (
    <div
      className="modal-overlay"
      onMouseDown={(e) => e.target === e.currentTarget && close()}
    >
      <div
        ref={ref}
        className={`modal ${wide ? "wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header>
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button
            className="icon-btn"
            aria-label="Close dialog"
            onClick={close}
          >
            <X size={20} />
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}
function App() {
  const [me, setMe] = useState<Row | null>(null),
    [workspaces, setWorkspaces] = useState<Row[]>([]),
    [boot, setBoot] = useState(true),
    [page, setPage] = useState<Page>("overview"),
    [mobile, setMobile] = useState(false);
  const [docs, setDocs] = useState<Row[]>([]),
    [queries, setQueries] = useState<Row[]>([]),
    [overview, setOverview] = useState<Row>({}),
    [system, setSystem] = useState<Row>({}),
    [labels, setLabels] = useState<Row[]>([]),
    [runs, setRuns] = useState<Row[]>([]),
    [keys, setKeys] = useState<Row[]>([]),
    [budget, setBudget] = useState<Row>({}),
    [jobs, setJobs] = useState<Row[]>([]),
    [evalBusy, setEvalBusy] = useState(false);
  const [toast, setToast] = useState(""),
    [error, setError] = useState(""),
    [keyInput, setKeyInput] = useState(""),
    [connecting, setConnecting] = useState(false),
    [upload, setUpload] = useState(false),
    [docSearch, setDocSearch] = useState(""),
    [source, setSource] = useState<Row | null>(null),
    [workspaceMenu, setWorkspaceMenu] = useState(false);
  const [question, setQuestion] = useState(""),
    [answer, setAnswer] = useState(""),
    [sources, setSources] = useState<Row[]>([]),
    [asked, setAsked] = useState(""),
    [busy, setBusy] = useState(false),
    [stage, setStage] = useState(""),
    [queryInfo, setQueryInfo] = useState<Row>({}),
    [mode, setMode] = useState("hybrid"),
    [rerank, setRerank] = useState(false),
    [queryError, setQueryError] = useState(""),
    [traceSteps, setTraceSteps] = useState<Row[]>([]);
  const [review, setReview] = useState<Row | null>(null),
    [newKey, setNewKey] = useState(false),
    [showKey, setShowKey] = useState(""),
    [keyLabel, setKeyLabel] = useState(""),
    [confirmDelete, setConfirmDelete] = useState<Row | null>(null);
  const epoch = useRef(0);
  const abort = useRef<AbortController | null>(null),
    questionRef = useRef<HTMLTextAreaElement>(null),
    answerRef = useRef<HTMLDivElement>(null);
  const notify = (text: string) => {
    setToast(text);
    setTimeout(() => setToast(""), 4500);
  };
  async function load() {
    const version = epoch.current;
    const results = await Promise.allSettled([
      api("/documents"),
      api("/queries"),
      api("/overview"),
      api("/system"),
      api("/budget"),
      api("/jobs"),
    ]);
    if (version !== epoch.current) return;
    const setters = [
      setDocs,
      setQueries,
      setOverview,
      setSystem,
      setBudget,
      setJobs,
    ];
    results.forEach((r, i) => {
      if (r.status === "fulfilled") setters[i](r.value);
      else if (i === 0) setError(r.reason.message);
    });
  }
  useEffect(() => {
    (async () => {
      try {
        setWorkspaces(await api("/local/workspaces"));
      } catch {}
      try {
        setMe(await api("/me"));
      } catch {}
      setBoot(false);
    })();
  }, []);
  useEffect(() => {
    if (me) {
      load();
      const interval = setInterval(load, 5000);
      return () => clearInterval(interval);
    }
  }, [me]);
  useEffect(() => {
    if (!me) return;
    const version = epoch.current;
    if (page === "evaluate") {
      Promise.all([api("/evaluation/labels"), api("/evaluation/runs")])
        .then(([l, r]) => {
          if (version !== epoch.current) return;
          setLabels(l);
          setRuns(r);
        })
        .catch((e) => setError(e.message));
    }
    if (page === "settings")
      api("/keys")
        .then((value) => {
          if (version === epoch.current) setKeys(value);
        })
        .catch((e) => {
          if (version === epoch.current) setError(e.message);
        });
    setError("");
  }, [page, me]);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setPage("ask");
        setTimeout(() => questionRef.current?.focus(), 50);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);
  const go = (next: Page) => {
    setPage(next);
    setMobile(false);
    setError("");
  };
  async function connect(id?: string) {
    epoch.current++;
    abort.current?.abort();
    setBusy(false);
    setDocs([]);
    setQueries([]);
    setOverview({});
    setSystem({});
    setLabels([]);
    setRuns([]);
    setKeys([]);
    setBudget({});
    setJobs([]);
    setSource(null);
    setShowKey("");
    setReview(null);
    setQuestion("");
    setQueryError("");
    setTraceSteps([]);
    setDocSearch("");
    setConnecting(true);
    setError("");
    try {
      if (id) await api("/local/connect/" + id, { method: "POST" });
      else
        await api("/session", {
          method: "POST",
          headers: { Authorization: "Bearer " + keyInput.trim() },
        });
      setMe(await api("/me"));
      setWorkspaceMenu(false);
      setAnswer("");
      setAsked("");
      setSources([]);
      setPage("overview");
      setKeyInput("");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setConnecting(false);
    }
  }
  async function signout() {
    epoch.current++;
    abort.current?.abort();
    await api("/session", { method: "DELETE" });
    setMe(null);
    setAnswer("");
    setAsked("");
  }
  async function openSource(row: Row) {
    const version = epoch.current;
    if (row.kind === "structured") {
      setSource({
        ...row,
        metadata: { category: "Structured service inventory" },
      });
      return;
    }
    try {
      const detail = await api("/documents/" + (row.document_id || row.id));
      if (version !== epoch.current) return;
      setSource({
        ...detail,
        highlight:
          row.start_offset === undefined
            ? null
            : { start: row.start_offset, end: row.end_offset },
      });
    } catch (e: any) {
      notify(e.message);
    }
  }
  async function ask(text = question) {
    if (!text.trim() || busy) return;
    go("ask");
    setAsked(text.trim());
    setQuestion("");
    setAnswer("");
    setSources([]);
    setQueryError("");
    setQueryInfo({});
    setTraceSteps([]);
    setBusy(true);
    setStage("Finding relevant passages");
    const controller = new AbortController();
    const version = epoch.current;
    abort.current = controller;
    try {
      const response = await fetch("/api/query", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Atlas-Client": "console",
        },
        body: JSON.stringify({ question: text.trim(), mode, rerank }),
        signal: controller.signal,
      });
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || "Request failed");
      }
      const reader = response.body!.getReader(),
        decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done || version !== epoch.current) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const name = frame.match(/^event: (.+)$/m)?.[1],
            dataLine = frame.match(/^data: (.+)$/m)?.[1];
          if (!dataLine) continue;
          const data = JSON.parse(dataLine);
          if (name === "delta") setAnswer((v) => v + data.text);
          if (name === "sources") setSources(data.sources);
          if (name === "metadata")
            setStage(
              data.stage === "generating"
                ? "Writing an evidence-backed answer"
                : data.stage === "agent"
                  ? "Working through your question"
                  : "Finding relevant passages",
            );
          if (name === "step") setTraceSteps((v) => [...v, data]);
          if (name === "error" || name === "warning")
            setQueryError(data.message);
          if (name === "done") setQueryInfo(data);
        }
      }
    } catch (e: any) {
      if (version !== epoch.current) return;
      if (e.name === "AbortError")
        setQueryError(
          "Stopped. You can ask another question whenever you’re ready.",
        );
      else setQueryError(e.message);
    } finally {
      if (version === epoch.current) {
        setBusy(false);
        setStage("");
        load();
      }
    }
  }
  function previous(row: Row) {
    go("ask");
    setAsked(row.question);
    setAnswer(row.answer);
    setSources(row.sources || []);
    setQueryInfo(row);
    setQueryError(
      row.status === "failed"
        ? "This request did not finish successfully. Try asking again."
        : "",
    );
  }
  const suggestions = [
    "What is a Kubernetes namespace?",
    "How do readiness and liveness probes differ?",
    "What is the checkout release approval process?",
  ];
  if (boot)
    return (
      <div className="boot">
        <div className="brand-symbol">
          A<span>·</span>
        </div>
        <LoaderCircle className="spin" size={22} />
      </div>
    );
  if (!me)
    return (
      <div className="login">
        <div className="login-art">
          <div className="brand">
            <div className="brand-symbol">
              A<span>·</span>
            </div>
            <span>atlas</span>
          </div>
          <div>
            <span className="eyebrow">YOUR KNOWLEDGE, CONNECTED</span>
            <h1>
              Great answers
              <br />
              start with
              <br />
              <em>your knowledge.</em>
            </h1>
            <p>
              A private place for your documents, your questions,
              <br />
              and the connections between them.
            </p>
          </div>
          <div className="login-foot">
            <ShieldCheck size={17} /> Private by design. Powered by local
            intelligence.
          </div>
          <div className="orb one" />
          <div className="orb two" />
        </div>
        <main className="login-form">
          <div className="small-mark">
            <Compass size={27} />
          </div>
          <h2>Welcome to your workspace</h2>
          <p>
            Choose a local workspace to get started, or connect with an API key.
          </p>
          {error && <div className="alert error">{error}</div>}
          <div className="workspace-options">
            {workspaces.map((w, i) => (
              <button
                key={w.id}
                onClick={() => connect(w.id)}
                disabled={connecting}
              >
                <span className={`avatar ${i ? "lavender" : ""}`}>
                  {w.name.slice(0, 1)}
                </span>
                <span>
                  <strong>{w.name}</strong>
                  <small>Local workspace</small>
                </span>
                <ChevronRight size={18} />
              </button>
            ))}
          </div>
          {workspaces.length > 0 && (
            <div className="divider">
              <span>or use an API key</span>
            </div>
          )}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              connect();
            }}
          >
            <label htmlFor="api-key">Workspace API key</label>
            <input
              id="api-key"
              type="password"
              value={keyInput}
              onChange={(e) => setKeyInput(e.target.value)}
              placeholder="atl_…"
              autoComplete="off"
            />
            <button
              className="btn primary full"
              disabled={connecting || !keyInput.trim()}
            >
              {connecting ? (
                <LoaderCircle className="spin" size={16} />
              ) : (
                <KeyRound size={16} />
              )}
              Connect to workspace
            </button>
          </form>
          <p className="login-note">
            <ShieldCheck size={14} /> Your key stays in a secure, HTTP-only
            session cookie.
          </p>
        </main>
      </div>
    );
  return (
    <div className="app-shell">
      {mobile && (
        <div className="mobile-scrim" onClick={() => setMobile(false)} />
      )}
      <aside className={`sidebar ${mobile ? "open" : ""}`}>
        <a
          href="#"
          className="brand"
          onClick={(e) => {
            e.preventDefault();
            go("overview");
          }}
        >
          <div className="brand-symbol">
            A<span>·</span>
          </div>
          <span>atlas</span>
          <small>WORKSPACE</small>
        </a>
        <div className="workspace-switch">
          <button
            onClick={() => setWorkspaceMenu(!workspaceMenu)}
            aria-expanded={workspaceMenu}
          >
            <span className="avatar">{me.name[0]}</span>
            <span>
              <strong>{me.name}</strong>
              <small>Local workspace</small>
            </span>
            <ChevronDown size={15} />
          </button>
          {workspaceMenu && (
            <div className="workspace-menu">
              {workspaces.map((w) => (
                <button key={w.id} onClick={() => connect(w.id)}>
                  {w.name}
                  {w.id === me.tenant_id && <Check size={14} />}
                </button>
              ))}
              <button onClick={signout}>
                <LogOut size={14} />
                Sign out
              </button>
            </div>
          )}
        </div>
        <span className="nav-label">WORKSPACE</span>
        <nav>
          {nav.slice(0, 3).map((n) => (
            <button
              key={n.id}
              onClick={() => go(n.id)}
              className={page === n.id ? "active" : ""}
            >
              <n.icon size={18} />
              {n.label}
              {n.id === "documents" && (
                <span className="nav-count">{docs.length}</span>
              )}
              {n.id === "ask" && (
                <span aria-hidden="true" className="kbd">
                  ⌘ K
                </span>
              )}
            </button>
          ))}
        </nav>
        <span className="nav-label">OBSERVE & MANAGE</span>
        <nav>
          {nav.slice(3).map((n) => (
            <button
              key={n.id}
              onClick={() => go(n.id)}
              className={page === n.id ? "active" : ""}
            >
              <n.icon size={18} />
              {n.label}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-card">
            <div>
              <span
                className={`status-dot ${system.generation?.ready ? "" : "offline"}`}
              />
              <strong>
                {system.generation?.ready
                  ? "Local intelligence online"
                  : "Checking local intelligence"}
              </strong>
            </div>
            <p>Your knowledge stays in your workspace.</p>
            <button onClick={() => go("settings")}>
              View system status <ArrowUpRight size={13} />
            </button>
          </div>
          <button
            className="profile"
            onClick={() => setWorkspaceMenu(!workspaceMenu)}
          >
            <span className="avatar outline">SK</span>
            <span>
              <strong>Workspace operator</strong>
              <small>Local session</small>
            </span>
            <Settings2 size={16} />
          </button>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumbs">
            <button
              className="icon-btn mobile-toggle"
              aria-label="Open navigation"
              onClick={() => setMobile(true)}
            >
              <PanelLeft size={20} />
            </button>
            <span>Workspace</span>
            <ChevronRight size={13} />
            <strong>{nav.find((n) => n.id === page)?.label}</strong>
          </div>
          <div className="top-actions">
            <span className="private-label">
              <ShieldCheck size={14} />
              Private workspace
            </span>
            <span className="top-divider" />
            <button
              className="icon-btn"
              aria-label="Refresh workspace"
              onClick={() => {
                load();
                notify("Workspace refreshed");
              }}
            >
              <RefreshCw size={16} />
            </button>
            <span className="avatar small">SK</span>
          </div>
        </header>
        <main className={`content ${page === "ask" ? "ask-content" : ""}`}>
          {error && (
            <div className="alert error" role="alert">
              {error}
              <button onClick={() => setError("")} aria-label="Dismiss error">
                <X size={15} />
              </button>
            </div>
          )}
          {page === "overview" && (
            <>
              <div className="page-heading">
                <div>
                  <span className="eyebrow">THE WORKSPACE AT A GLANCE</span>
                  <h1>
                    A little more clarity.
                    <br />
                    <em>A lot more possibility.</em>
                  </h1>
                  <p>
                    Everything your team knows. One place to put it to work.
                  </p>
                </div>
                <button className="btn primary" onClick={() => setUpload(true)}>
                  <Plus size={17} />
                  Add knowledge
                </button>
              </div>
              <div className="stats-grid">
                {[
                  {
                    label: "Documents",
                    value: fmt(overview.documents),
                    note: "In your knowledge library",
                    icon: BookOpen,
                  },
                  {
                    label: "Searchable passages",
                    value: fmt(overview.chunks),
                    note: "Connected to their sources",
                    icon: Layers3,
                  },
                  {
                    label: "Questions asked",
                    value: fmt(overview.queries),
                    note: "Across this workspace",
                    icon: Sparkles,
                  },
                  {
                    label: "External API spend",
                    value:
                      overview.api_spend !== undefined
                        ? "$" + fmt(overview.api_spend)
                        : "—",
                    note: "Models run on your machine",
                    icon: ShieldCheck,
                  },
                ].map((s, i) => (
                  <div
                    className={`stat-card ${i === 3 ? "mint" : ""}`}
                    key={s.label}
                  >
                    <div>
                      <span>{s.label}</span>
                      <s.icon size={17} />
                    </div>
                    <strong>{s.value}</strong>
                    <small>{s.note}</small>
                  </div>
                ))}
              </div>
              <section className="ask-hero">
                <div className="hero-copy">
                  <div className="icon-tile">
                    <Sparkles size={23} />
                  </div>
                  <div>
                    <span className="eyebrow">MAKE CONNECTIONS</span>
                    <h2>There’s an answer in here.</h2>
                    <p>Ask a question. Atlas finds the passages that matter.</p>
                  </div>
                </div>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    ask();
                  }}
                  className="hero-search"
                >
                  <Search size={20} />
                  <input
                    aria-label="Ask your knowledge"
                    placeholder="What would you like to know?"
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                  />
                  <button
                    type="submit"
                    className="send-btn"
                    aria-label="Ask Atlas"
                    disabled={!question.trim()}
                  >
                    <ArrowUp size={19} />
                  </button>
                </form>
                <div className="suggestion-chips">
                  <span>Try asking</span>
                  {suggestions.slice(0, 2).map((s) => (
                    <button key={s} onClick={() => ask(s)}>
                      {s}
                      <ArrowUpRight size={13} />
                    </button>
                  ))}
                </div>
                <div className="hero-decoration" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </div>
              </section>
              <div className="dashboard-columns">
                <section className="panel">
                  <div className="section-heading">
                    <div>
                      <h2>Recently added</h2>
                      <p>The latest in your knowledge library</p>
                    </div>
                    <button
                      className="text-btn"
                      onClick={() => go("documents")}
                    >
                      View library
                      <ArrowUpRight size={15} />
                    </button>
                  </div>
                  {docs.length ? (
                    <div className="recent-docs">
                      {docs.slice(0, 5).map((d) => (
                        <button key={d.id} onClick={() => openSource(d)}>
                          <div className="file-icon">
                            <FileText size={21} />
                          </div>
                          <div>
                            <strong>{d.title}</strong>
                            <small>
                              {d.metadata?.category || "Workspace document"}
                              <span>·</span>
                              {fmt(d.chunks)} passages
                            </small>
                          </div>
                          <Badge
                            tone={d.status === "ready" ? "green" : "amber"}
                          >
                            {d.status === "ready" ? "Ready" : d.status}
                          </Badge>
                          <ChevronRight size={15} />
                        </button>
                      ))}
                    </div>
                  ) : (
                    <Empty
                      title="Start your library"
                      description="Upload your first document to make your workspace searchable."
                      action={
                        <button
                          className="btn secondary"
                          onClick={() => setUpload(true)}
                        >
                          Add a document
                        </button>
                      }
                    />
                  )}
                </section>
                <section className="panel recent-questions">
                  <div className="section-heading">
                    <div>
                      <h2>Recent questions</h2>
                      <p>Pick up where you left off</p>
                    </div>
                    <Clock3 size={18} />
                  </div>
                  {queries.length ? (
                    queries.slice(0, 4).map((q) => (
                      <button
                        className="recent-question"
                        key={q.id}
                        onClick={() => previous(q)}
                      >
                        <span className="question-mini">
                          <Sparkles size={14} />
                        </span>
                        <div>
                          <strong>{q.question}</strong>
                          <small>
                            {ago(q.created_at)}
                            <span>·</span>
                            {q.status}
                          </small>
                        </div>
                        <ArrowUpRight size={15} />
                      </button>
                    ))
                  ) : (
                    <Empty
                      icon={Sparkles}
                      title="Curiosity starts here"
                      description="Your questions and their sources will appear here."
                    />
                  )}
                  <div className="quiet-tip">
                    <ShieldCheck size={16} />
                    <p>
                      Answers are grounded in your documents, with sources you
                      can inspect.
                    </p>
                  </div>
                </section>
              </div>
              <footer className="page-foot">
                <span>
                  <span className="status-dot" />
                  Built for thoughtful answers
                </span>
                <span>
                  Local models · Tenant-isolated data · Source citations
                </span>
              </footer>
            </>
          )}
          {page === "ask" && (
            <div className="ask-layout">
              <div className="conversation">
                <div className="section-heading query-heading">
                  <div>
                    <span className="eyebrow">ASK YOUR KNOWLEDGE</span>
                    <h1>
                      Ask Atlas<span className="serif-dot">.</span>
                    </h1>
                  </div>
                  {asked && (
                    <button
                      className="btn secondary small"
                      disabled={busy}
                      onClick={() => {
                        setAsked("");
                        setAnswer("");
                        setSources([]);
                        setQueryError("");
                        setQueryInfo({});
                      }}
                    >
                      <Plus size={15} />
                      New question
                    </button>
                  )}
                </div>
                {!asked ? (
                  <div className="query-empty">
                    <div className="spark-orbit">
                      <Sparkles size={32} />
                    </div>
                    <h2>
                      Find the thread.
                      <br />
                      <em>Follow your curiosity.</em>
                    </h2>
                    <p>
                      Ask about your documents, connect ideas, or get a clear
                      answer with the evidence behind it.
                    </p>
                    <div className="prompt-grid">
                      {suggestions.map((s, i) => (
                        <button key={s} onClick={() => ask(s)}>
                          <span>
                            {
                              [
                                "Understand a concept",
                                "Compare two ideas",
                                "Find a team process",
                              ][i]
                            }
                          </span>
                          <strong>{s}</strong>
                          <ArrowUpRight size={17} />
                        </button>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="conversation-body" ref={answerRef}>
                    <div className="question-message">
                      <span className="avatar small">You</span>
                      <h2>{asked}</h2>
                    </div>
                    <div className="answer-message">
                      <div className="answer-byline">
                        <div className="atlas-mini">
                          <Sparkles size={16} />
                        </div>
                        <strong>Atlas</strong>
                        <Badge tone="green">Your knowledge</Badge>
                        {busy && (
                          <span className="thinking">
                            <LoaderCircle className="spin" size={13} />
                            {stage}
                          </span>
                        )}
                      </div>
                      {traceSteps.length > 0 && (
                        <div className="agent-steps">
                          {traceSteps.map((s, i) => (
                            <span key={i}>
                              <CheckCircle2 size={13} />
                              {s.tool || s.stage}
                            </span>
                          ))}
                        </div>
                      )}
                      {!answer && busy && (
                        <div className="skeleton-lines">
                          <span />
                          <span />
                          <span />
                        </div>
                      )}
                      <div
                        className={`prose answer-text ${busy ? "streaming" : ""}`}
                      >
                        <ReactMarkdown
                          remarkPlugins={[remarkGfm]}
                          components={{
                            img: ({ alt }) => <span>{alt || "Image"}</span>,
                            a: ({ href, children }) =>
                              href?.startsWith("#source-") ? (
                                <button
                                  className="citation"
                                  onClick={() => {
                                    const item =
                                      sources[Number(href.slice(8)) - 1];
                                    if (item) openSource(item);
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
                          {answer.replace(
                            /\[(\d+)\](?!\()/g,
                            "[$1](#source-$1)",
                          )}
                        </ReactMarkdown>
                      </div>
                      {queryError && (
                        <div className="alert amber" role="status">
                          {queryError}
                        </div>
                      )}
                      {!busy && answer && (
                        <div className="answer-tools">
                          <button
                            className="text-btn"
                            onClick={() =>
                              navigator.clipboard
                                .writeText(answer)
                                .then(() => notify("Answer copied"))
                            }
                          >
                            <Copy size={14} />
                            Copy answer
                          </button>
                          <span>
                            {queryInfo.status === "abstained"
                              ? "No relevant evidence found"
                              : queryInfo.status === "completed"
                                ? "Answer complete"
                                : queryInfo.status || ""}
                          </span>
                          {queryInfo.duration_ms !== undefined && (
                            <span>
                              {(queryInfo.duration_ms / 1000).toFixed(1)}s
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                )}
                <form
                  className="composer"
                  onSubmit={(e) => {
                    e.preventDefault();
                    ask();
                  }}
                >
                  <textarea
                    ref={questionRef}
                    aria-label="Your question"
                    placeholder="Ask a question about your workspace…"
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        ask();
                      }
                    }}
                    maxLength={2000}
                    rows={2}
                  />
                  <div className="composer-bottom">
                    <div>
                      <select
                        aria-label="Query mode"
                        value={mode}
                        onChange={(e) => setMode(e.target.value)}
                        disabled={busy}
                      >
                        <option value="hybrid">Hybrid search</option>
                        <option value="vector">Semantic search</option>
                        <option value="agent">Agent + tools</option>
                      </select>
                      <label className="check-label">
                        <input
                          type="checkbox"
                          checked={rerank}
                          onChange={(e) => setRerank(e.target.checked)}
                          disabled={busy}
                        />
                        Rerank
                      </label>
                    </div>
                    {busy ? (
                      <button
                        type="button"
                        className="send-btn stop"
                        aria-label="Stop generation"
                        onClick={() => abort.current?.abort()}
                      >
                        <Square size={15} />
                      </button>
                    ) : (
                      <button
                        className="send-btn"
                        aria-label="Send question"
                        disabled={!question.trim()}
                      >
                        <ArrowUp size={20} />
                      </button>
                    )}
                  </div>
                </form>
                <p className="composer-note">
                  <ShieldCheck size={12} />
                  Local inference. Check source passages for important details.
                </p>
              </div>
              <aside className="sources-panel">
                <div className="section-heading">
                  <h3>Sources</h3>
                  <Badge>{sources.length}</Badge>
                </div>
                <p className="source-intro">The evidence behind your answer.</p>
                {sources.length ? (
                  sources.map((s, i) => (
                    <button
                      className="source-card"
                      key={s.id}
                      onClick={() => openSource(s)}
                    >
                      <div>
                        <span className="source-number">{i + 1}</span>
                        <FileText size={15} />
                        <ArrowUpRight size={15} />
                      </div>
                      <h4>{s.title}</h4>
                      <p>{s.content.slice(0, 200)}…</p>
                      <footer>
                        View source passage
                        <ChevronRight size={13} />
                      </footer>
                    </button>
                  ))
                ) : (
                  <div className="source-placeholder">
                    <BookOpen size={26} />
                    <p>
                      Relevant passages will appear here when you ask a
                      question.
                    </p>
                  </div>
                )}
                <div className="source-foot">
                  <ShieldCheck size={15} />
                  Only this workspace’s knowledge is searched.
                </div>
              </aside>
            </div>
          )}
          {page === "documents" && (
            <>
              <div className="page-heading compact">
                <div>
                  <span className="eyebrow">YOUR KNOWLEDGE LIBRARY</span>
                  <h1>A home for what you know.</h1>
                  <p>
                    Add documents, keep context, and make every passage
                    discoverable.
                  </p>
                </div>
                <button className="btn primary" onClick={() => setUpload(true)}>
                  <Plus size={17} />
                  Add documents
                </button>
              </div>
              <div className="library-summary">
                <BookOpen size={19} />
                <strong>{docs.length} documents</strong>
                <span>{fmt(overview.chunks)} searchable passages</span>
                <div />
                <Badge tone="green">
                  <ShieldCheck size={12} />
                  Tenant isolated
                </Badge>
              </div>
              <section className="panel library">
                <div className="table-toolbar">
                  <div className="filter-input">
                    <Search size={17} />
                    <input
                      aria-label="Search documents"
                      placeholder="Search your documents…"
                      value={docSearch}
                      onChange={(e) => setDocSearch(e.target.value)}
                    />
                  </div>
                  <span>
                    {
                      docs.filter((d) =>
                        d.title.toLowerCase().includes(docSearch.toLowerCase()),
                      ).length
                    }{" "}
                    results
                  </span>
                </div>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>DOCUMENT</th>
                        <th>STATUS</th>
                        <th>PASSAGES</th>
                        <th>ADDED</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {docs
                        .filter((d) =>
                          d.title
                            .toLowerCase()
                            .includes(docSearch.toLowerCase()),
                        )
                        .map((d) => (
                          <tr key={d.id}>
                            <td>
                              <button
                                className="document-name"
                                onClick={() => openSource(d)}
                              >
                                <span className="file-icon">
                                  <FileText size={21} />
                                </span>
                                <span>
                                  <strong>{d.title}</strong>
                                  <small>
                                    {d.metadata?.category ||
                                      "Workspace document"}
                                  </small>
                                </span>
                              </button>
                            </td>
                            <td>
                              <Badge
                                tone={
                                  d.status === "ready"
                                    ? "green"
                                    : d.status === "failed"
                                      ? "red"
                                      : "amber"
                                }
                              >
                                <span className="tiny-dot" />
                                {d.status}
                              </Badge>
                            </td>
                            <td>{fmt(d.chunks)}</td>
                            <td>{ago(d.created_at)}</td>
                            <td>
                              <button
                                className="icon-btn danger"
                                aria-label={"Delete " + d.title}
                                onClick={() => setConfirmDelete(d)}
                              >
                                <Trash2 size={16} />
                              </button>
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
                {!docs.length && (
                  <Empty
                    title="Your library is ready for its first document"
                    description="Add Markdown or plain-text files. Atlas will index them automatically."
                    action={
                      <button
                        className="btn secondary"
                        onClick={() => setUpload(true)}
                      >
                        Add documents
                      </button>
                    }
                  />
                )}
                <div className="table-footer">
                  <ShieldCheck size={14} />
                  <span>Documents are searchable only within {me.name}.</span>
                </div>
              </section>
              <div className="upload-banner">
                <div className="icon-tile">
                  <Upload size={22} />
                </div>
                <div>
                  <h3>Good knowledge deserves a good home.</h3>
                  <p>
                    Upload a runbook, a technical guide, or the notes your team
                    keeps coming back to.
                  </p>
                </div>
                <button
                  className="btn secondary"
                  onClick={() => setUpload(true)}
                >
                  Add knowledge
                  <ArrowUpRight size={16} />
                </button>
              </div>
            </>
          )}
          {page === "evaluate" && (
            <>
              <div className="page-heading compact">
                <div>
                  <span className="eyebrow">QUALITY YOU CAN EXPLAIN</span>
                  <h1>Evidence over assumptions.</h1>
                  <p>
                    Review the ground truth, then measure how well your
                    retrieval works.
                  </p>
                </div>
                <button
                  className="btn primary"
                  disabled={
                    evalBusy ||
                    labels.length < 40 ||
                    labels.some((l) => !l.reviewed)
                  }
                  onClick={async () => {
                    setEvalBusy(true);
                    try {
                      const run = await api("/evaluation/run", {
                        method: "POST",
                        body: JSON.stringify({}),
                      });
                      notify("Evaluation finished");
                      setRuns([run, ...runs]);
                    } catch (e: any) {
                      notify(e.message);
                    } finally {
                      setEvalBusy(false);
                    }
                  }}
                >
                  <FlaskConical size={16} />
                  {evalBusy ? "Evaluating…" : "Run evaluation"}
                </button>
              </div>
              <div className="evaluation-banner">
                <ShieldCheck size={25} />
                <div>
                  <h3>Human-reviewed ground truth comes first.</h3>
                  <p>
                    Labels are excluded from quality measurements until you
                    verify their question and supporting passage. No fabricated
                    scores.
                  </p>
                </div>
                <Badge tone="amber">
                  {labels.filter((l) => l.reviewed).length} / {labels.length}{" "}
                  reviewed
                </Badge>
              </div>
              <div className="stats-grid eval-stats">
                {["Recall@5", "MRR", "nDCG@5", "Faithfulness"].map((m, i) => (
                  <div className="stat-card" key={m}>
                    <div>
                      <span>{m}</span>
                      <FlaskConical size={16} />
                    </div>
                    <strong>
                      {runs[0]?.metrics?.[
                        ["recall_at_5", "mrr", "ndcg_at_5", "faithfulness"][i]
                      ] !== undefined
                        ? fmt(
                            runs[0].metrics[
                              [
                                "recall_at_5",
                                "mrr",
                                "ndcg_at_5",
                                "faithfulness",
                              ][i]
                            ],
                          )
                        : "—"}
                    </strong>
                    <small>
                      {i === 3
                        ? "Requires calibrated judge"
                        : "From reviewed labels only"}
                    </small>
                  </div>
                ))}
              </div>
              <section className="panel">
                <div className="section-heading">
                  <div>
                    <h2>Review queue</h2>
                    <p>
                      Check each question against the evidence before accepting
                      its label.
                    </p>
                  </div>
                  <Badge>
                    {labels.filter((l) => !l.reviewed).length} pending
                  </Badge>
                </div>
                {labels.length ? (
                  <div className="review-list">
                    {labels.map((l, i) => (
                      <button key={l.id} onClick={() => setReview(l)}>
                        <span className="review-number">
                          {String(i + 1).padStart(2, "0")}
                        </span>
                        <div>
                          <strong>{l.question}</strong>
                          <small>
                            {l.source_title || "Source document"} ·{" "}
                            {l.split || "development"}
                          </small>
                        </div>
                        <Badge tone={l.reviewed ? "green" : "amber"}>
                          {l.reviewed ? "Reviewed" : "Needs review"}
                        </Badge>
                        <ChevronRight size={17} />
                      </button>
                    ))}
                  </div>
                ) : (
                  <Empty
                    icon={FlaskConical}
                    title="No review candidates yet"
                    description="Prepare labels from your indexed documents to begin measuring retrieval."
                  />
                )}
              </section>
              {runs.length > 0 && (
                <section className="panel runs-panel">
                  <div className="section-heading">
                    <h2>Evaluation runs</h2>
                  </div>
                  {runs.map((r) => (
                    <div className="run-row" key={r.id}>
                      <CheckCircle2 size={17} />
                      <strong>{r.label_count} reviewed questions</strong>
                      <Badge>{r.mode || "hybrid"}</Badge>
                      <span>{ago(r.created_at)}</span>
                      <span>Recall@5 {fmt(r.metrics?.recall_at_5)}</span>
                    </div>
                  ))}
                </section>
              )}
            </>
          )}
          {page === "activity" && (
            <>
              <div className="page-heading compact">
                <div>
                  <span className="eyebrow">A CLEARER PICTURE</span>
                  <h1>Every question leaves a trail.</h1>
                  <p>
                    Inspect requests, latency, and the evidence behind each
                    response.
                  </p>
                </div>
                <button className="btn secondary" onClick={load}>
                  <RefreshCw size={15} />
                  Refresh
                </button>
              </div>
              <div className="stats-grid">
                <div className="stat-card">
                  <div>
                    <span>Total requests</span>
                    <Activity size={17} />
                  </div>
                  <strong>{queries.length}</strong>
                  <small>Most recent requests, up to 100</small>
                </div>
                <div className="stat-card">
                  <div>
                    <span>Completed</span>
                    <CheckCircle2 size={17} />
                  </div>
                  <strong>
                    {queries.filter((q) => q.status === "completed").length}
                  </strong>
                  <small>Answers with valid citation markers</small>
                </div>
                <div className="stat-card">
                  <div>
                    <span>Average latency</span>
                    <Clock3 size={17} />
                  </div>
                  <strong>
                    {overview.avg_latency_ms != null
                      ? (overview.avg_latency_ms / 1000).toFixed(1) + "s"
                      : "—"}
                  </strong>
                  <small>Complete successful responses</small>
                </div>
                <div className="stat-card mint">
                  <div>
                    <span>API spend</span>
                    <ShieldCheck size={17} />
                  </div>
                  <strong>${fmt(overview.api_spend || 0)}</strong>
                  <small>Local hardware costs excluded</small>
                </div>
              </div>
              <section className="panel">
                <div className="section-heading">
                  <h2>Request history</h2>
                  <Badge>Live workspace data</Badge>
                </div>
                {queries.length ? (
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>QUESTION</th>
                          <th>RESULT</th>
                          <th>LATENCY</th>
                          <th>WHEN</th>
                        </tr>
                      </thead>
                      <tbody>
                        {queries.map((q) => (
                          <tr key={q.id}>
                            <td>
                              <button
                                className="history-question"
                                onClick={() => previous(q)}
                              >
                                <Sparkles size={16} />
                                {q.question}
                              </button>
                            </td>
                            <td>
                              <Badge
                                tone={
                                  q.status === "completed"
                                    ? "green"
                                    : q.status === "failed"
                                      ? "red"
                                      : "amber"
                                }
                              >
                                {q.cached ? "Cached" : q.status}
                              </Badge>
                            </td>
                            <td>{(q.duration_ms / 1000).toFixed(1)}s</td>
                            <td>{ago(q.created_at)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <Empty
                    icon={Activity}
                    title="Your activity will appear here"
                    description="Ask your first question to see its result, timing, and sources."
                  />
                )}
              </section>
            </>
          )}
          {page === "settings" && (
            <>
              <div className="page-heading compact">
                <div>
                  <span className="eyebrow">WORKSPACE SETTINGS</span>
                  <h1>Private. Local. In your control.</h1>
                  <p>
                    Manage workspace access and check the services behind your
                    answers.
                  </p>
                </div>
              </div>
              <section className="panel settings-panel">
                <div className="section-heading">
                  <div>
                    <h2>Local intelligence</h2>
                    <p>No paid model APIs are enabled.</p>
                  </div>
                  <ShieldCheck size={21} />
                </div>
                {[
                  {
                    label: "Generation model",
                    description: system.generation?.model,
                    ready: system.generation?.ready,
                  },
                  {
                    label: "Embedding model",
                    description: system.embeddings?.model,
                    ready: system.embeddings?.ready,
                  },
                  {
                    label: "Cross-encoder reranker",
                    description: "MiniLM · local CPU inference",
                    ready: system.reranker?.ready,
                  },
                ].map((s) => (
                  <div className="setting-row" key={s.label}>
                    <div className="icon-tile">
                      <Database size={19} />
                    </div>
                    <div>
                      <strong>{s.label}</strong>
                      <small>{s.description}</small>
                    </div>
                    <Badge tone={s.ready ? "green" : "amber"}>
                      {s.ready ? "Available" : "Not available"}
                    </Badge>
                  </div>
                ))}
              </section>
              <section className="panel settings-panel">
                <div className="section-heading">
                  <div>
                    <h2>API keys</h2>
                    <p>Give integrations only the permissions they need.</p>
                  </div>
                  <button
                    className="btn secondary small"
                    onClick={() => setNewKey(true)}
                  >
                    <Plus size={15} />
                    Create key
                  </button>
                </div>
                {keys.map((k) => (
                  <div className="setting-row" key={k.id}>
                    <div className="icon-tile">
                      <KeyRound size={19} />
                    </div>
                    <div>
                      <strong>{k.label}</strong>
                      <small>
                        {k.prefix}•••••• · {k.scopes.join(", ")}
                      </small>
                    </div>
                    <Badge tone={k.revoked_at ? "neutral" : "green"}>
                      {k.revoked_at ? "Revoked" : "Active"}
                    </Badge>
                    {!k.revoked_at && (
                      <button
                        className="icon-btn danger"
                        aria-label={"Revoke " + k.label}
                        onClick={async () => {
                          try {
                            await api("/keys/" + k.id, { method: "DELETE" });
                            setKeys(await api("/keys"));
                            notify("API key revoked");
                          } catch (e: any) {
                            notify(e.message);
                          }
                        }}
                      >
                        <Trash2 size={16} />
                      </button>
                    )}
                  </div>
                ))}
              </section>
              <section className="panel settings-panel">
                <div className="section-heading">
                  <div>
                    <h2>Workspace limits</h2>
                    <p>
                      Local inference has no API charge. Token budgets keep
                      resource use bounded.
                    </p>
                  </div>
                </div>
                <div className="setting-row">
                  <div>
                    <strong>
                      {fmt(budget.usage?.used_tokens || 0)} tokens used this
                      month
                    </strong>
                    <small>
                      {fmt(budget.usage?.reserved_tokens || 0)} reserved ·{" "}
                      {fmt(budget.limits?.monthly_tokens)} monthly limit
                    </small>
                  </div>
                  <Badge>
                    {fmt(budget.limits?.requests_per_minute)} requests / min
                  </Badge>
                </div>
              </section>
              <section className="panel settings-panel">
                <div className="section-heading">
                  <div>
                    <h2>Background ingestion</h2>
                    <p>
                      Documents are indexed with durable retries and recovery.
                    </p>
                  </div>
                </div>
                {jobs.length ? (
                  jobs.slice(0, 10).map((j) => (
                    <div className="setting-row" key={j.id}>
                      <div>
                        <strong>
                          {docs.find((d) => d.id === j.document_id)?.title ||
                            "Removed document"}
                        </strong>
                        <small>
                          {j.attempts} attempts{" "}
                          {j.error_code ? "· " + j.error_code : ""}
                        </small>
                      </div>
                      <Badge
                        tone={
                          j.status === "completed"
                            ? "green"
                            : j.status === "dead"
                              ? "red"
                              : "amber"
                        }
                      >
                        {j.status}
                      </Badge>
                      {j.status === "dead" && (
                        <button
                          className="btn secondary small"
                          onClick={async () => {
                            try {
                              await api("/jobs/" + j.id + "/retry", {
                                method: "POST",
                              });
                              load();
                              notify("Retry queued");
                            } catch (e: any) {
                              notify(e.message);
                            }
                          }}
                        >
                          Retry
                        </button>
                      )}
                    </div>
                  ))
                ) : (
                  <div className="setting-row">No background jobs yet.</div>
                )}
              </section>
              <section className="security-note">
                <ShieldCheck size={23} />
                <div>
                  <h3>Isolation is enforced in two layers.</h3>
                  <p>
                    Application queries scope every request to your workspace.
                    PostgreSQL row-level security independently enforces tenant
                    ownership.
                  </p>
                </div>
              </section>
            </>
          )}
        </main>
      </div>
      {toast && (
        <div className="toast" role="status">
          <CheckCircle2 size={17} />
          {toast}
          <button
            aria-label="Dismiss notification"
            onClick={() => setToast("")}
          >
            <X size={15} />
          </button>
        </div>
      )}
      {upload && (
        <UploadModal
          close={() => setUpload(false)}
          complete={() => {
            setUpload(false);
            load();
            notify(
              "Document received. Indexing will finish in the background.",
            );
          }}
        />
      )}
      {source && (
        <Modal
          title={source.title}
          subtitle="Original document · workspace-scoped access"
          wide
          close={() => setSource(null)}
        >
          <div className="source-modal-meta">
            <Badge tone="green">{source.status}</Badge>
            <span>{fmt(source.content.length)} characters</span>
            {source.metadata?.source_url && (
              <a
                href={source.metadata.source_url}
                target="_blank"
                rel="noreferrer"
              >
                Original source
                <ExternalLink size={13} />
              </a>
            )}
          </div>
          {source.highlight && (
            <div className="highlight-panel">
              <span className="eyebrow">
                CITED PASSAGE · CHARACTERS {source.highlight.start}–
                {source.highlight.end}
              </span>
              <p>
                {source.content.slice(
                  source.highlight.start,
                  source.highlight.end,
                )}
              </p>
            </div>
          )}
          <div className="prose document-preview">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {source.content}
            </ReactMarkdown>
          </div>
        </Modal>
      )}
      {review && (
        <ReviewModal
          label={review}
          close={() => setReview(null)}
          complete={async () => {
            setReview(null);
            setLabels(await api("/evaluation/labels"));
            notify("Review recorded");
          }}
        />
      )}
      {confirmDelete && (
        <Modal
          title="Remove this document?"
          subtitle="It will stop appearing in search and its cached answers will be invalidated."
          close={() => setConfirmDelete(null)}
        >
          <div className="confirm-body">
            <FileText size={23} />
            <strong>{confirmDelete.title}</strong>
          </div>
          <div className="modal-actions">
            <button
              className="btn secondary"
              onClick={() => setConfirmDelete(null)}
            >
              Keep document
            </button>
            <button
              className="btn destructive"
              onClick={async () => {
                try {
                  await api("/documents/" + confirmDelete.id, {
                    method: "DELETE",
                  });
                  setConfirmDelete(null);
                  load();
                  notify("Document removed");
                } catch (e: any) {
                  notify(e.message);
                }
              }}
            >
              Remove document
            </button>
          </div>
        </Modal>
      )}
      {newKey && (
        <Modal
          title={showKey ? "Your new API key" : "Create an API key"}
          subtitle={
            showKey
              ? "Copy it now. The full key is only shown once."
              : "This key can read documents and query this workspace."
          }
          close={() => {
            setNewKey(false);
            setShowKey("");
            setKeyLabel("");
          }}
        >
          {showKey ? (
            <div className="key-result">
              <code>{showKey}</code>
              <button
                className="btn secondary"
                onClick={() =>
                  navigator.clipboard
                    .writeText(showKey)
                    .then(() => notify("Key copied"))
                }
              >
                <Copy size={15} />
                Copy key
              </button>
            </div>
          ) : (
            <form
              className="modal-form"
              onSubmit={async (e) => {
                e.preventDefault();
                try {
                  const result = await api("/keys", {
                    method: "POST",
                    body: JSON.stringify({ label: keyLabel }),
                  });
                  setShowKey(result.key);
                  setKeys(await api("/keys"));
                } catch (e: any) {
                  notify(e.message);
                }
              }}
            >
              <label htmlFor="key-label">Key name</label>
              <input
                id="key-label"
                value={keyLabel}
                onChange={(e) => setKeyLabel(e.target.value)}
                placeholder="e.g. Local integration"
                required
                maxLength={80}
              />
              <button className="btn primary" disabled={!keyLabel.trim()}>
                Create key
              </button>
            </form>
          )}
        </Modal>
      )}
    </div>
  );
}
function UploadModal({
  close,
  complete,
}: {
  close: () => void;
  complete: () => void;
}) {
  const [tab, setTab] = useState("file"),
    [file, setFile] = useState<File | null>(null),
    [title, setTitle] = useState(""),
    [text, setText] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (tab === "file") {
        if (!file) throw new Error("Choose a file first");
        const body = new FormData();
        body.append("file", file);
        await api("/documents/upload", { method: "POST", body });
      } else
        await api("/documents/text", {
          method: "POST",
          body: JSON.stringify({ title, content: text }),
        });
      complete();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="Add a little knowledge"
      subtitle="Your documents stay private to this workspace."
      close={() => !busy && close()}
    >
      <div className="modal-tabs">
        <button
          className={tab === "file" ? "selected" : ""}
          disabled={busy}
          onClick={() => setTab("file")}
        >
          Upload a file
        </button>
        <button
          className={tab === "text" ? "selected" : ""}
          disabled={busy}
          onClick={() => setTab("text")}
        >
          Paste text
        </button>
      </div>
      <form className="modal-form" onSubmit={submit}>
        {tab === "file" ? (
          <>
            <input
              ref={input}
              className="visually-hidden"
              type="file"
              aria-label="Choose document file"
              accept=".txt,.md,.markdown"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
            <button
              type="button"
              className={`dropzone ${drag ? "drag" : ""}`}
              onClick={() => input.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setDrag(true);
              }}
              onDragLeave={() => setDrag(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDrag(false);
                setFile(e.dataTransfer.files[0]);
              }}
            >
              <div className="empty-icon">
                <Upload size={25} />
              </div>
              <strong>{file ? file.name : "Drop a document here"}</strong>
              <span>
                {file
                  ? `${fmt(file.size / 1024)} KB · ready to upload`
                  : "or click to browse your files"}
              </span>
              <small>Markdown and plain text · up to 2 MB</small>
            </button>
          </>
        ) : (
          <>
            <label htmlFor="doc-title">Document title</label>
            <input
              id="doc-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Release process"
              required
              maxLength={200}
            />
            <label htmlFor="doc-text">Content</label>
            <textarea
              id="doc-text"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Paste the knowledge you’d like Atlas to search…"
              required
              rows={9}
            />
          </>
        )}
        {error && (
          <div className="alert error" role="alert">
            {error}
          </div>
        )}
        <div className="upload-privacy">
          <ShieldCheck size={15} />
          Indexed locally. Accessible only to this workspace.
        </div>
        <div className="modal-actions">
          <button
            type="button"
            className="btn secondary"
            onClick={close}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            className="btn primary"
            disabled={
              busy || (tab === "file" ? !file : !title.trim() || !text.trim())
            }
          >
            {busy ? (
              <>
                <LoaderCircle className="spin" size={16} />
                Adding knowledge…
              </>
            ) : (
              <>
                <Plus size={16} />
                Add to workspace
              </>
            )}
          </button>
        </div>
      </form>
    </Modal>
  );
}
function ReviewModal({
  label,
  close,
  complete,
}: {
  label: Row;
  close: () => void;
  complete: () => void;
}) {
  const [question, setQuestion] = useState(label.question),
    [start, setStart] = useState(label.start_offset),
    [end, setEnd] = useState(label.end_offset),
    [full, setFull] = useState(""),
    [checked, setChecked] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    api("/documents/" + label.document_id)
      .then((d) => setFull(d.content))
      .catch((e) => setError(e.message));
  }, [label.document_id]);
  return (
    <Modal
      title="Review the evidence"
      subtitle="Confirm that this passage actually supports the question."
      wide
      close={close}
    >
      <form
        className="modal-form"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await api("/evaluation/labels/" + label.id, {
              method: "PUT",
              body: JSON.stringify({
                question,
                reviewed: checked,
                start_offset: start,
                end_offset: end,
              }),
            });
            complete();
          } catch (e: any) {
            setError(e.message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label htmlFor="review-question">Question</label>
        <textarea
          id="review-question"
          rows={2}
          value={question}
          onChange={(e) => {
            setQuestion(e.target.value);
            setChecked(false);
          }}
        />
        <div className="review-evidence">
          <span className="eyebrow">
            SOURCE EVIDENCE · {label.source_title}
          </span>
          <p>{full ? full.slice(start, end) : label.evidence}</p>
        </div>
        <label>
          Evidence start offset
          <input
            aria-label="Evidence start offset"
            type="number"
            min={0}
            max={end - 1}
            value={start}
            onChange={(e) => {
              setStart(Number(e.target.value));
              setChecked(false);
            }}
          />
        </label>
        <label>
          Evidence end offset
          <input
            aria-label="Evidence end offset"
            type="number"
            min={start + 1}
            max={full.length || undefined}
            value={end}
            onChange={(e) => {
              setEnd(Number(e.target.value));
              setChecked(false);
            }}
          />
        </label>
        <details>
          <summary>Read full source</summary>
          <pre
            style={{ whiteSpace: "pre-wrap", maxHeight: 260, overflow: "auto" }}
          >
            {full}
          </pre>
        </details>
        <label className="review-confirm">
          <input
            type="checkbox"
            checked={checked}
            onChange={(e) => setChecked(e.target.checked)}
          />
          I checked the question and source passage. This is valid evidence.
        </label>
        {error && <div className="alert error">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="btn secondary" onClick={close}>
            Review later
          </button>
          <button className="btn primary" disabled={!checked || busy}>
            {busy ? (
              <LoaderCircle className="spin" size={16} />
            ) : (
              <Check size={16} />
            )}
            Save reviewed label
          </button>
        </div>
      </form>
    </Modal>
  );
}
