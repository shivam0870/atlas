import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Laptop, ShieldCheck } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { api, json, type User } from "../api/client";
import {
  Button,
  ErrorNotice,
  Field,
  Modal,
  Notice,
  PageHeader,
  Badge,
} from "../components/ui";
import { sessionKey, useSession } from "./session";
type Session = {
  id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  user_agent: string;
  current: boolean;
};
export function AccountPage() {
  const user = useSession().data!.user;
  const client = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState(user.name);
  const [theme, setTheme] = useState(user.theme || "system");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [message, setMessage] = useState("");
  const [enrollment, setEnrollment] = useState<{
    secret: string;
    uri: string;
  } | null>(null);
  const [codes, setCodes] = useState<string[]>([]);
  const [security, setSecurity] = useState<
    "enroll" | "disable" | "recovery" | "password" | "email" | "delete" | null
  >(null);
  const sessions = useQuery({
    queryKey: ["account", user.id, "sessions"],
    queryFn: () => api<{ items: Session[] }>("/auth/sessions"),
  });
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setMessage("");
    try {
      await action();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function profile(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      await api<{ user: User }>("/auth/profile", {
        method: "PATCH",
        body: json({ name, theme }),
      });
      localStorage.setItem("atlas-theme", theme);
      await client.invalidateQueries({ queryKey: sessionKey });
      setMessage("Profile updated.");
    });
  }
  function open(value: typeof security) {
    setSecurity(value);
    setPassword("");
    setNewPassword("");
    setCode("");
    setError(null);
  }
  async function securitySubmit(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      if (security === "enroll") {
        await api("/auth/reauthenticate", {
          method: "POST",
          body: json({ password, code: code || undefined }),
        });
        setEnrollment(await api("/auth/mfa/enroll", { method: "POST" }));
        setSecurity(null);
      } else if (security === "disable") {
        await api("/auth/mfa/disable", {
          method: "POST",
          body: json({ password, code }),
        });
        setMessage("Two-step authentication disabled.");
      } else if (security === "recovery") {
        const r = await api<{ recovery_codes: string[] }>(
          "/auth/mfa/recovery-codes",
          { method: "POST", body: json({ password, code }) },
        );
        setCodes(r.recovery_codes);
      } else if (security === "password") {
        await api("/auth/password", {
          method: "POST",
          body: json({
            current_password: password,
            new_password: newPassword,
            code: code || undefined,
          }),
        });
        setMessage("Password changed.");
      } else if (security === "email") {
        await api("/auth/email", {
          method: "POST",
          body: json({ email, password, code: code || undefined }),
        });
        setMessage("Check the new email address for a verification link.");
      } else if (security === "delete") {
        await api("/auth/reauthenticate", {
          method: "POST",
          body: json({ password, code: code || undefined }),
        });
        await api("/auth/account", {
          method: "DELETE",
          body: json({ confirmation: "DELETE" }),
        });
        client.clear();
        navigate("/login");
      }
      setSecurity(null);
      setPassword("");
      setCode("");
      await client.invalidateQueries({ queryKey: sessionKey });
      await sessions.refetch();
    });
  }
  async function confirm(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      const result = await api<{ recovery_codes: string[] }>(
        "/auth/mfa/confirm",
        { method: "POST", body: json({ code }) },
      );
      setCodes(result.recovery_codes);
      setEnrollment(null);
      setCode("");
      await client.invalidateQueries({ queryKey: sessionKey });
    });
  }
  return (
    <>
      <PageHeader
        title="My account"
        description="Your profile, preferences and account security."
      />
      <ErrorNotice error={!security && !enrollment ? error : null} />
      {message && <Notice>{message}</Notice>}
      <div className="settings-grid">
        <section className="panel">
          <h2>Profile & appearance</h2>
          <p className="muted">
            Your account belongs to you across all your workspaces.
          </p>
          <form onSubmit={profile} className="form-stack">
            <Field
              label="Display name"
              value={name}
              maxLength={100}
              onChange={(e) => setName(e.target.value)}
              required
            />
            <Field label="Email" value={user.email} readOnly />
            <label className="field">
              <span>Theme</span>
              <select
                value={theme}
                onChange={(e) => setTheme(e.target.value as User["theme"])}
              >
                <option value="system">Use device setting</option>
                <option value="light">Light</option>
                <option value="dark">Dark</option>
              </select>
            </label>
            <div className="row">
              <Button busy={busy}>Save profile</Button>
              <Button
                type="button"
                variant="secondary"
                onClick={() => open("email")}
              >
                Change email
              </Button>
            </div>
          </form>
        </section>
        <section className="panel">
          <div className="row between">
            <h2>Account security</h2>
            <ShieldCheck size={22} />
          </div>
          <div className="setting-row">
            <div>
              <strong>Password</strong>
              <p>Use a long, unique password.</p>
            </div>
            <Button variant="secondary" onClick={() => open("password")}>
              Change
            </Button>
          </div>
          <div className="setting-row">
            <div>
              <strong>Two-step authentication</strong>
              <p>Add an authenticator app to protect sign-in.</p>
              <Badge tone={user.mfa_enabled ? "success" : "neutral"}>
                {user.mfa_enabled ? "Enabled" : "Not enabled"}
              </Badge>
            </div>
            <Button
              variant="secondary"
              onClick={() => open(user.mfa_enabled ? "disable" : "enroll")}
            >
              {user.mfa_enabled ? "Disable" : "Set up"}
            </Button>
          </div>
          {user.mfa_enabled && (
            <Button variant="secondary" onClick={() => open("recovery")}>
              Replace recovery codes
            </Button>
          )}
        </section>
      </div>
      <section className="panel">
        <div className="row between">
          <div>
            <h2>Active sessions</h2>
            <p className="muted">
              Revoke a session if you no longer recognize or use it.
            </p>
          </div>
          <Button
            variant="secondary"
            busy={busy}
            onClick={() =>
              run(async () => {
                await api("/auth/logout-all", { method: "POST" });
                client.clear();
                navigate("/login");
              })
            }
          >
            Sign out everywhere
          </Button>
        </div>
        <ErrorNotice error={sessions.error} />
        <div className="session-list">
          {sessions.data?.items.map((s) => (
            <div className="setting-row" key={s.id}>
              <Laptop size={20} />
              <div className="grow">
                <strong>{s.user_agent || "Unknown browser"}</strong>
                <p>Last active {new Date(s.last_seen_at).toLocaleString()}</p>
              </div>
              {s.current ? (
                <Badge>Current session</Badge>
              ) : (
                <Button
                  variant="secondary"
                  busy={busy}
                  onClick={() =>
                    run(async () => {
                      await api(`/auth/sessions/${s.id}`, { method: "DELETE" });
                      await sessions.refetch();
                    })
                  }
                >
                  Revoke
                </Button>
              )}
            </div>
          ))}
        </div>
      </section>
      <section className="panel danger-panel">
        <h2>Delete account</h2>
        <p className="muted">
          Transfer ownership or delete the workspaces you own before deleting
          your account. Access is revoked immediately. After 30 days, private
          conversations are erased and your profile is anonymized. Company
          records retain a deleted-member identifier.
        </p>
        <Button variant="danger" onClick={() => open("delete")}>
          Delete my account
        </Button>
      </section>
      <Modal
        open={!!security}
        onOpenChange={(v) => !v && setSecurity(null)}
        title={
          security === "enroll"
            ? "Set up two-step authentication"
            : security === "disable"
              ? "Disable two-step authentication"
              : security === "recovery"
                ? "Replace recovery codes"
                : security === "password"
                  ? "Change password"
                  : security === "email"
                    ? "Change email address"
                    : "Delete your account"
        }
        description={
          security === "delete"
            ? "Access is revoked now. Your private history and profile are erased or anonymized after 30 days; company records keep a deleted-member identifier. Confirm your password to continue."
            : "Confirm your current password to protect this change."
        }
      >
        <form className="form-stack" onSubmit={securitySubmit}>
          <ErrorNotice error={error} />
          {security === "email" && (
            <Field
              label="New email address"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          )}
          <Field
            label="Current password"
            autoComplete="current-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          {security === "password" && (
            <Field
              label="New password"
              autoComplete="new-password"
              type="password"
              minLength={15}
              maxLength={128}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
            />
          )}
          {user.mfa_enabled && (
            <Field
              label="Authenticator or recovery code"
              autoComplete="one-time-code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              required
            />
          )}
          <Button
            variant={security === "delete" ? "danger" : "primary"}
            busy={busy}
          >
            {security === "delete" ? "Delete account" : "Confirm"}
          </Button>
        </form>
      </Modal>
      <Modal
        open={!!enrollment}
        onOpenChange={(v) => !v && setEnrollment(null)}
        title="Connect your authenticator"
        description="Scan this QR code in your authenticator, then enter its current code."
      >
        {enrollment && (
          <form className="form-stack" onSubmit={confirm}>
            <ErrorNotice error={error} />
            <div className="qr-code">
              <QRCodeSVG value={enrollment.uri} size={180} />
            </div>
            <label className="field">
              <span>Manual setup key</span>
              <code className="secret-code">{enrollment.secret}</code>
            </label>
            <Field
              label="Six-digit code"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              required
            />
            <Button busy={busy}>Enable two-step authentication</Button>
          </form>
        )}
      </Modal>
      <Modal
        open={codes.length > 0}
        onOpenChange={(v) => !v && setCodes([])}
        title="Save your recovery codes"
        description="Each code works once. Keep these somewhere safe outside Atlas; they will not be shown again."
      >
        <div className="recovery-codes">
          {codes.map((c) => (
            <code key={c}>{c}</code>
          ))}
        </div>
        <Button
          onClick={() => {
            const a = document.createElement("a");
            a.href = URL.createObjectURL(
              new Blob([codes.join("\n")], { type: "text/plain" }),
            );
            a.download = "atlas-recovery-codes.txt";
            a.click();
            URL.revokeObjectURL(a.href);
          }}
        >
          Download recovery codes
        </Button>
      </Modal>
    </>
  );
}
