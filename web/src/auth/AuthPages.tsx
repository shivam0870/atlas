import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  BookOpen,
  Check,
  Layers3,
  ShieldCheck,
  FileText,
  Sparkles,
} from "lucide-react";
import { api, json, type AuthSession } from "../api/client";
import { Button, ErrorNotice, Field, Notice } from "../components/ui";
import { sessionKey } from "./session";
export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="auth-layout">
      <aside className="auth-story">
        <Link className="brand" to="/">
          <span className="brand-mark">
            <Layers3 size={23} />
          </span>
          atlas<span className="brand-dot">.</span>
        </Link>
        <div className="auth-story-copy">
          <span className="eyebrow">YOUR KNOWLEDGE, CONNECTED</span>
          <h1>
            Less searching.
            <br />
            More
            <br />
            <em>understanding.</em>
          </h1>
          <p>
            Bring your documents, people and everyday questions together in one
            private workspace.
          </p>
          <div className="story-points">
            <span>
              <BookOpen size={18} /> Sources you can inspect
            </span>
            <span>
              <ShieldCheck size={18} /> Access your team controls
            </span>
            <span>
              <Check size={18} /> Local models. Your knowledge stays yours.
            </span>
          </div>
        </div>
        <div
          className="knowledge-illustration"
          aria-label="Illustration of the document-to-answer workflow"
        >
          <div className="illustration-label">
            FROM INFORMATION TO UNDERSTANDING
          </div>
          <div className="illustration-docs">
            <span>
              <FileText size={18} /> Runbooks
            </span>
            <span>
              <FileText size={18} /> Team guides
            </span>
            <span>
              <FileText size={18} /> Documents
            </span>
          </div>
          <div className="illustration-connection" aria-hidden="true">
            <span />
            <Layers3 size={24} />
            <span />
          </div>
          <div className="illustration-answer">
            <Sparkles size={20} />
            <div>
              <strong>An answer. With a source.</strong>
              <p>Ask → understand → verify</p>
            </div>
            <span className="illustration-citation">[1]</span>
          </div>
        </div>
        <span className="auth-foot">
          <span className="status-dot" /> Private conversations. Verifiable
          sources.
        </span>
      </aside>
      <main id="main-content" tabIndex={-1} className="auth-main">
        <div className="auth-mobile-brand">atlas.</div>
        <div className="auth-card">{children}</div>
        <p className="auth-privacy">Your conversations are private to you.</p>
      </main>
    </div>
  );
}
function safeNext(value: string | null) {
  return value?.startsWith("/") &&
    !value.startsWith("//") &&
    !value.includes("\\") &&
    !/[\u0000-\u001f]/.test(value) &&
    !value.startsWith("/login")
    ? value
    : "/onboarding";
}
export function LoginPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [challenge, setChallenge] = useState("");
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const data = await api<
        AuthSession | { mfa_required: true; challenge: string }
      >(challenge ? "/auth/mfa/login" : "/auth/login", {
        method: "POST",
        body: json(challenge ? { challenge, code } : { email, password }),
      });
      if ("mfa_required" in data) {
        setChallenge(data.challenge);
        setPassword("");
        return;
      }
      client.clear();
      client.setQueryData(sessionKey, data);
      navigate(safeNext(params.get("next")), { replace: true });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <AuthLayout>
      <span className="eyebrow">WELCOME BACK</span>
      <h1>{challenge ? "A quick security check" : "Sign in to Atlas"}</h1>
      <p className="muted">
        {challenge
          ? "Enter the code from your authenticator, or a recovery code."
          : "Your knowledge, conversations and team are right here."}
      </p>
      <form onSubmit={submit} className="form-stack">
        <ErrorNotice error={error} />
        {!!error && (
          <Link
            className="text-link"
            to={`/verify-email?email=${encodeURIComponent(email)}`}
          >
            Need to verify your email?
          </Link>
        )}
        {challenge ? (
          <Field
            label="Authentication or recovery code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            autoComplete="one-time-code"
            autoFocus
            required
          />
        ) : (
          <>
            <Field
              label="Email address"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
            <Field
              label="Password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
            <Link className="text-link align-end" to="/forgot-password">
              Forgot your password?
            </Link>
          </>
        )}
        <Button busy={busy} type="submit">
          {challenge ? "Verify and sign in" : "Sign in"}
          <ArrowRight size={17} />
        </Button>
        {challenge && (
          <Button
            variant="ghost"
            onClick={() => {
              setChallenge("");
              setCode("");
            }}
            type="button"
          >
            Back to sign in
          </Button>
        )}
      </form>
      <p className="auth-switch">
        New here?{" "}
        <Link
          to={`/register${params.get("next") ? `?next=${encodeURIComponent(params.get("next")!)}` : ""}`}
        >
          Create an account
        </Link>
      </p>
    </AuthLayout>
  );
}
export function RegisterPage() {
  const [params] = useSearchParams();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/auth/register", {
        method: "POST",
        body: json({ name, email, password }),
      });
      setSent(true);
      setPassword("");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <AuthLayout>
      <span className="eyebrow">MAKE ROOM FOR KNOWLEDGE</span>
      <h1>{sent ? "Check your inbox" : "Create your account"}</h1>
      <p className="muted">
        {sent
          ? "If registration is available for this address, a verification message is on its way. Open the link to continue."
          : "Start a personal workspace or bring your company together."}
      </p>
      {sent ? (
        <div className="form-stack">
          <Notice>
            Verification links expire. You can request another below.
          </Notice>
          <Link
            className="button secondary"
            to={`/verify-email?email=${encodeURIComponent(email)}`}
          >
            Resend verification
          </Link>
          <Link className="text-link" to="/login">
            Continue to sign in
          </Link>
        </div>
      ) : (
        <form onSubmit={submit} className="form-stack">
          <ErrorNotice error={error} />
          <Field
            label="Your name"
            autoComplete="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={100}
            required
          />
          <Field
            label="Email address"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <Field
            label="Password"
            type="password"
            autoComplete="new-password"
            minLength={15}
            maxLength={128}
            hint="Use at least 15 characters. A memorable phrase works well."
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          <Button type="submit" busy={busy}>
            Create account
            <ArrowRight size={17} />
          </Button>
        </form>
      )}
      <p className="auth-switch">
        Already have an account?{" "}
        <Link
          to={`/login${params.get("next") ? `?next=${encodeURIComponent(params.get("next")!)}` : ""}`}
        >
          Sign in
        </Link>
      </p>
    </AuthLayout>
  );
}
export function VerifyPage() {
  const [params] = useSearchParams();
  const token = params.get("token");
  const [email, setEmail] = useState(params.get("email") || "");
  const [message, setMessage] = useState("");
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  useEffect(() => {
    if (!cooldown) return;
    const timer = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);
  async function verify() {
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ message: string }>(
        params.get("change") === "1" ||
          window.location.pathname === "/verify-email-change"
          ? "/auth/verify-email-change"
          : "/auth/verify",
        { method: "POST", body: json({ token }) },
      );
      setMessage(r.message || "Your email is verified. You can now sign in.");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function resend(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ message: string }>("/auth/resend-verification", {
        method: "POST",
        body: json({ email }),
      });
      setMessage(r.message);
      setCooldown(60);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <AuthLayout>
      <span className="eyebrow">ONE LAST CHECK</span>
      <h1>Verify your email</h1>
      <p className="muted">
        Verification confirms that this address belongs to you.
      </p>
      <div className="form-stack">
        <ErrorNotice error={error} />
        {!!error && token && (
          <Link className="text-link" to="/verify-email">
            Request a new verification link
          </Link>
        )}
        {message && <Notice>{message}</Notice>}
        {token && !message ? (
          <Button busy={busy} onClick={verify}>
            Verify email address
          </Button>
        ) : !token ? (
          <form className="form-stack" onSubmit={resend}>
            <Field
              label="Email address"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <Button busy={busy} disabled={cooldown > 0}>
              {cooldown ? `Resend in ${cooldown}s` : "Send verification email"}
            </Button>
          </form>
        ) : null}
        <Link className="text-link" to="/login">
          Continue to sign in
        </Link>
      </div>
    </AuthLayout>
  );
}
export function RecoveryPage({ reset = false }: { reset?: boolean }) {
  const [params] = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ message: string }>(
        reset ? "/auth/reset-password" : "/auth/forgot-password",
        {
          method: "POST",
          body: json(
            reset ? { token: params.get("token"), password } : { email },
          ),
        },
      );
      setMessage(r.message || "Check your inbox for the next step.");
      setPassword("");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <AuthLayout>
      <span className="eyebrow">ACCOUNT RECOVERY</span>
      <h1>{reset ? "Choose a new password" : "Forgot your password?"}</h1>
      <p className="muted">
        {reset
          ? "Resetting your password signs out existing sessions."
          : "Enter your email and we’ll send recovery instructions if an account is eligible."}
      </p>
      <form onSubmit={submit} className="form-stack">
        <ErrorNotice error={error} />
        {!!error && reset && (
          <Link className="text-link" to="/forgot-password">
            Request a new recovery link
          </Link>
        )}
        {message ? (
          <Notice>{message}</Notice>
        ) : (
          <>
            {reset ? (
              <Field
                label="New password"
                type="password"
                autoComplete="new-password"
                minLength={15}
                maxLength={128}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            ) : (
              <Field
                label="Email address"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            )}
            <Button busy={busy}>
              {reset ? "Reset password" : "Send recovery instructions"}
            </Button>
          </>
        )}
        <Link className="text-link" to="/login">
          Back to sign in
        </Link>
      </form>
    </AuthLayout>
  );
}
