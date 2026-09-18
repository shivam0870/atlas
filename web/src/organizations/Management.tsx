import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Mail, Plus, Users } from "lucide-react";
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
  Notice,
  PageHeader,
} from "../components/ui";
type Member = {
  user_id: string;
  name: string;
  email: string;
  role: string;
  status: string;
};
type Invite = {
  id: string;
  email: string;
  role: string;
  status?: string;
  expires_at: string;
  accepted_at?: string;
  revoked_at?: string;
};
type Team = { id: string; name: string; members: string[] };
function Forbidden() {
  return (
    <EmptyState
      title="Company management is restricted"
      description="An owner or administrator can manage company settings, members and teams."
    />
  );
}
function ManagementTabs() {
  const { tenantId } = useWorkspace();
  return (
    <div className="page-tabs">
      <Link to={`/o/${tenantId}/people`}>People & invitations</Link>
      <Link to={`/o/${tenantId}/teams`}>Teams</Link>
    </div>
  );
}
export function PeoplePage() {
  const { tenantId, organization, user, canManage } = useWorkspace();
  const client = useQueryClient();
  const key = useScopedQueryKey("members");
  const members = useQuery({
    queryKey: key,
    queryFn: () =>
      api<{ items: Member[] }>(`/organizations/${tenantId}/members`),
    enabled: canManage,
  });
  const invitations = useQuery({
    queryKey: useScopedQueryKey("invitations"),
    queryFn: () =>
      api<{ items: Invite[] }>(`/organizations/${tenantId}/invitations`),
    enabled: canManage,
  });
  const [invite, setInvite] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("viewer");
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [message, setMessage] = useState("");
  const [confirm, setConfirm] = useState<{
    title: string;
    description: string;
    action: () => Promise<unknown>;
    reauth?: boolean;
  } | null>(null);
  const [reauthPassword, setReauthPassword] = useState("");
  const [reauthCode, setReauthCode] = useState("");
  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await client.invalidateQueries({
        queryKey: ["workspace", user.id, tenantId],
      });
      await client.invalidateQueries({ queryKey: ["organizations"] });
      setConfirm(null);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function send(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      await api(`/organizations/${tenantId}/invitations`, {
        method: "POST",
        body: json({ email, role }),
      });
      setInvite(false);
      setEmail("");
      setMessage(
        "Invitation sent. The recipient must verify this email address to join.",
      );
    });
  }
  if (!canManage) return <Forbidden />;
  return (
    <>
      <PageHeader
        title="People & teams"
        description="Bring the right people together, with clear access boundaries."
        action={
          <Button
            onClick={() => {
              setError(null);
              setInvite(true);
            }}
          >
            <Plus size={17} />
            Invite someone
          </Button>
        }
      />
      <ManagementTabs />
      <ErrorNotice
        error={
          !invite && !confirm
            ? error || members.error || invitations.error
            : null
        }
      />
      {message && <Notice>{message}</Notice>}
      <section className="panel">
        <div className="row between">
          <h2>
            Members{" "}
            <span className="muted">{members.data?.items.length || 0}</span>
          </h2>
          <input
            aria-label="Search members"
            placeholder="Search people…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        {members.isPending ? (
          <Loading />
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Person</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {members.data?.items
                  .filter((m) =>
                    `${m.name} ${m.email}`
                      .toLowerCase()
                      .includes(search.toLowerCase()),
                  )
                  .map((m) => (
                    <tr key={m.user_id}>
                      <td>
                        <strong>
                          {m.name}
                          {m.user_id === user.id ? " (you)" : ""}
                        </strong>
                        <small>{m.email}</small>
                      </td>
                      <td>
                        <select
                          aria-label={`Role for ${m.name}`}
                          value={m.role}
                          disabled={
                            busy ||
                            (organization.role !== "owner" &&
                              ["owner", "admin"].includes(m.role))
                          }
                          onChange={(e) => {
                            const next = e.target.value;
                            setConfirm({
                              title: "Change member role?",
                              reauth: next === "owner" || m.role === "owner",
                              description: `${m.name} will become ${next}. Their access will update immediately.`,
                              action: () =>
                                api(
                                  `/organizations/${tenantId}/members/${m.user_id}`,
                                  {
                                    method: "PATCH",
                                    body: json({ role: next }),
                                  },
                                ),
                            });
                          }}
                        >
                          {["owner", "admin", "editor", "viewer"].map((r) => (
                            <option
                              key={r}
                              disabled={
                                organization.role !== "owner" &&
                                ["owner", "admin"].includes(r)
                              }
                            >
                              {r}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <Badge
                          tone={m.status === "active" ? "success" : "neutral"}
                        >
                          {m.status}
                        </Badge>
                      </td>
                      <td>
                        <div className="row">
                          {m.user_id !== user.id && m.role !== "owner" && (
                            <>
                              <Button
                                variant="ghost"
                                disabled={busy}
                                onClick={() =>
                                  setConfirm({
                                    title:
                                      m.status === "active"
                                        ? "Suspend member?"
                                        : "Restore member?",
                                    description: `Update ${m.name}’s access to ${organization.name}.`,
                                    action: () =>
                                      api(
                                        `/organizations/${tenantId}/members/${m.user_id}`,
                                        {
                                          method: "PATCH",
                                          body: json({
                                            status:
                                              m.status === "active"
                                                ? "suspended"
                                                : "active",
                                          }),
                                        },
                                      ),
                                  })
                                }
                              >
                                {m.status === "active" ? "Suspend" : "Restore"}
                              </Button>
                              <Button
                                variant="ghost"
                                disabled={busy}
                                onClick={() =>
                                  setConfirm({
                                    title: "Remove member?",
                                    description: `${m.name} will lose access to this company.`,
                                    action: () =>
                                      api(
                                        `/organizations/${tenantId}/members/${m.user_id}`,
                                        { method: "DELETE" },
                                      ),
                                  })
                                }
                              >
                                Remove
                              </Button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className="panel">
        <h2>Invitations</h2>
        {!invitations.data?.items.length ? (
          <EmptyState
            icon={<Mail size={24} />}
            title="No invitations yet"
            description="Invite colleagues by email. They can join only with the matching verified account."
          />
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {invitations.data.items.map((i) => {
                  const status =
                    i.status ||
                    (i.accepted_at
                      ? "accepted"
                      : i.revoked_at
                        ? "revoked"
                        : new Date(i.expires_at) < new Date()
                          ? "expired"
                          : "pending");
                  return (
                    <tr key={i.id}>
                      <td>{i.email}</td>
                      <td>{i.role}</td>
                      <td>
                        <Badge>{status}</Badge>
                      </td>
                      <td>
                        {!["accepted", "revoked"].includes(status) && (
                          <div className="row">
                            <Button
                              variant="ghost"
                              disabled={busy}
                              onClick={() =>
                                run(() =>
                                  api(
                                    `/organizations/${tenantId}/invitations/${i.id}/resend`,
                                    { method: "POST" },
                                  ),
                                )
                              }
                            >
                              Resend
                            </Button>
                            <Button
                              variant="ghost"
                              disabled={busy}
                              onClick={() =>
                                setConfirm({
                                  title: "Revoke invitation?",
                                  description: `The invitation for ${i.email} will no longer work.`,
                                  action: () =>
                                    api(
                                      `/organizations/${tenantId}/invitations/${i.id}`,
                                      { method: "DELETE" },
                                    ),
                                })
                              }
                            >
                              Revoke
                            </Button>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <Modal
        open={invite}
        onOpenChange={setInvite}
        title="Invite a colleague"
        description="An invitation grants company membership. Restricted spaces may need additional access."
      >
        <form onSubmit={send} className="form-stack">
          <ErrorNotice error={error} />
          <Field
            label="Email address"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <label className="field">
            <span>Company role</span>
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              {[
                "viewer",
                "editor",
                ...(organization.role === "owner" ? ["admin"] : []),
              ].map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
          </label>
          <p className="muted text-small">
            Viewers can ask and read permitted knowledge. Editors can also
            maintain it. Administrators can manage company knowledge and
            members.
          </p>
          <Button busy={busy}>Send invitation</Button>
        </form>
      </Modal>
      <Modal
        open={!!confirm}
        onOpenChange={(v) => !v && setConfirm(null)}
        title={confirm?.title || "Confirm change"}
        description={confirm?.description}
      >
        <ErrorNotice error={error} />
        {confirm?.reauth && (
          <div className="form-stack">
            <Field
              label="Current password"
              type="password"
              autoComplete="current-password"
              value={reauthPassword}
              onChange={(e) => setReauthPassword(e.target.value)}
              required
            />
            {user.mfa_enabled && (
              <Field
                label="Authentication or recovery code"
                value={reauthCode}
                onChange={(e) => setReauthCode(e.target.value)}
                autoComplete="one-time-code"
                required
              />
            )}
          </div>
        )}
        <div className="modal-actions">
          <Button
            variant="secondary"
            onClick={() => {
              setConfirm(null);
              setReauthPassword("");
              setReauthCode("");
            }}
          >
            Cancel
          </Button>
          <Button
            busy={busy}
            onClick={() =>
              confirm &&
              run(async () => {
                if (confirm.reauth)
                  await api("/auth/reauthenticate", {
                    method: "POST",
                    body: json({
                      password: reauthPassword,
                      code: reauthCode || undefined,
                    }),
                  });
                await confirm.action();
                setReauthPassword("");
                setReauthCode("");
              })
            }
          >
            Confirm change
          </Button>
        </div>
      </Modal>
    </>
  );
}
export function TeamsPage() {
  const { tenantId, user, canManage } = useWorkspace();
  const client = useQueryClient();
  const teams = useQuery({
    queryKey: useScopedQueryKey("teams"),
    queryFn: () => api<{ items: Team[] }>(`/organizations/${tenantId}/teams`),
    enabled: canManage,
  });
  const members = useQuery({
    queryKey: useScopedQueryKey("members"),
    queryFn: () =>
      api<{ items: Member[] }>(`/organizations/${tenantId}/members`),
    enabled: canManage,
  });
  const [edit, setEdit] = useState<
    Team | { id?: string; name: string; members: string[] } | null
  >(null);
  const [remove, setRemove] = useState<Team | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await client.invalidateQueries({
        queryKey: ["workspace", user.id, tenantId],
      });
      await client.invalidateQueries({ queryKey: ["organizations"] });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  if (!canManage) return <Forbidden />;
  return (
    <>
      <PageHeader
        title="Teams"
        description="Group colleagues to manage knowledge access together."
        action={
          <Button
            onClick={() => {
              setName("");
              setEdit({ name: "", members: [] });
              setError(null);
            }}
          >
            <Plus size={17} />
            Create team
          </Button>
        }
      />
      <ManagementTabs />
      <ErrorNotice error={!edit && !remove ? error || teams.error : null} />
      {teams.isPending ? (
        <Loading />
      ) : !teams.data?.items.length ? (
        <EmptyState
          icon={<Users />}
          title="Bring your teams together"
          description="Create teams like Platform or Support, then give them access to the spaces they need."
        />
      ) : (
        <div className="card-grid">
          {teams.data.items.map((t) => (
            <section className="panel" key={t.id}>
              <div className="row between">
                <h2>{t.name}</h2>
                <Badge>{t.members.length} members</Badge>
              </div>
              <p className="muted">
                {t.members
                  .map(
                    (id) =>
                      members.data?.items.find((m) => m.user_id === id)?.name,
                  )
                  .filter(Boolean)
                  .join(", ") || "No members yet"}
              </p>
              <div className="row">
                <Button
                  variant="secondary"
                  onClick={() => {
                    setName(t.name);
                    setEdit(t);
                    setError(null);
                  }}
                >
                  Manage team
                </Button>
                <Button variant="ghost" onClick={() => setRemove(t)}>
                  Delete
                </Button>
              </div>
            </section>
          ))}
        </div>
      )}
      <Modal
        open={!!edit}
        onOpenChange={(v) => !v && setEdit(null)}
        title={edit?.id ? "Manage team" : "Create team"}
        description="Team changes update access to spaces granted to this team."
      >
        <ErrorNotice error={error} />
        <form
          className="form-stack"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              if (edit?.id) {
                await api(`/organizations/${tenantId}/teams/${edit.id}`, {
                  method: "PATCH",
                  body: json({ name }),
                });
                setEdit(null);
              } else {
                const r = await api<{ team: Team }>(
                  `/organizations/${tenantId}/teams`,
                  { method: "POST", body: json({ name }) },
                );
                setEdit({ ...r.team, members: r.team.members || [] });
              }
            });
          }}
        >
          <Field
            label="Team name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <Button busy={busy}>
            {edit?.id ? "Save team name" : "Create team"}
          </Button>
        </form>
        {edit?.id && (
          <div className="member-checklist">
            <h3>Members</h3>
            {members.data?.items
              .filter((m) => m.status === "active")
              .map((m) => (
                <label className="checkbox-row" key={m.user_id}>
                  <input
                    type="checkbox"
                    checked={edit.members.includes(m.user_id)}
                    disabled={busy}
                    onChange={(e) => {
                      const checked = e.target.checked;
                      void run(async () => {
                        await api(
                          `/organizations/${tenantId}/teams/${edit.id}/members/${m.user_id}`,
                          { method: checked ? "PUT" : "DELETE" },
                        );
                        setEdit({
                          ...edit,
                          members: checked
                            ? [...edit.members, m.user_id]
                            : edit.members.filter((id) => id !== m.user_id),
                        });
                      });
                    }}
                  />
                  <span>
                    {m.name}
                    <small>{m.email}</small>
                  </span>
                </label>
              ))}
          </div>
        )}
      </Modal>
      <Modal
        open={!!remove}
        onOpenChange={(v) => !v && setRemove(null)}
        title="Delete team?"
        description="Members remain in the company, but lose access granted through this team."
      >
        <ErrorNotice error={error} />
        <Button
          variant="danger"
          busy={busy}
          onClick={() =>
            run(async () => {
              await api(`/organizations/${tenantId}/teams/${remove?.id}`, {
                method: "DELETE",
              });
              setRemove(null);
            })
          }
        >
          Delete team
        </Button>
      </Modal>
    </>
  );
}
export function CompanyPage() {
  const { organization, tenantId, canManage, user } = useWorkspace();
  const client = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState(organization.name);
  const [description, setDescription] = useState(
    organization.description || "",
  );
  const [website, setWebsite] = useState(organization.website || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [message, setMessage] = useState("");
  const [action, setAction] = useState<"leave" | "transfer" | "delete" | null>(
    null,
  );
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [recipient, setRecipient] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const members = useQuery({
    queryKey: useScopedQueryKey("members"),
    queryFn: () =>
      api<{ items: Member[] }>(`/organizations/${tenantId}/members`),
    enabled: canManage,
  });
  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await client.invalidateQueries({ queryKey: ["organizations"] });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function perform(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      if (action === "leave") {
        if (organization.role === "owner")
          await api("/auth/reauthenticate", {
            method: "POST",
            body: json({ password, code: code || undefined }),
          });
        await api(`/organizations/${tenantId}/leave`, { method: "POST" });
      } else {
        await api("/auth/reauthenticate", {
          method: "POST",
          body: json({ password, code: code || undefined }),
        });
        if (action === "transfer")
          await api(`/organizations/${tenantId}/transfer-ownership`, {
            method: "POST",
            body: json({ user_id: recipient }),
          });
        else
          await api(`/organizations/${tenantId}`, {
            method: "DELETE",
            body: json({ confirmation }),
          });
      }
      setAction(null);
      client.removeQueries({ queryKey: ["workspace"] });
      navigate("/onboarding");
    });
  }
  return (
    <>
      <PageHeader
        title="Company settings"
        description="Manage your workspace identity and ownership."
      />
      <ErrorNotice error={!action ? error : null} />
      {message && <Notice>{message}</Notice>}
      {canManage && (
        <section className="panel narrow-panel">
          <h2>Workspace details</h2>
          <form
            className="form-stack"
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                await api(`/organizations/${tenantId}`, {
                  method: "PATCH",
                  body: json({ name, description, website }),
                });
                setMessage("Company details updated.");
              });
            }}
          >
            <Field
              label="Company name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
            <Field
              label="Workspace address"
              value={organization.slug}
              readOnly
            />
            <Field
              label="Description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
            <Field
              label="Website"
              type="url"
              value={website}
              onChange={(e) => setWebsite(e.target.value)}
            />
            <Button busy={busy}>Save changes</Button>
          </form>
        </section>
      )}
      <section className="panel">
        <h2>Access & privacy</h2>
        <p className="muted">
          Company owners and administrators can inspect company-owned knowledge.
          Private conversations remain accessible only to their creator.
        </p>
        <div className="row">
          {organization.role === "owner" && (
            <Button
              variant="secondary"
              onClick={() => {
                setAction("transfer");
                setError(null);
              }}
            >
              Transfer ownership
            </Button>
          )}
          <Button
            variant="secondary"
            onClick={() => {
              setAction("leave");
              setError(null);
            }}
          >
            Leave company
          </Button>
        </div>
      </section>
      {organization.role === "owner" && (
        <section className="panel danger-panel">
          <h2>Delete workspace</h2>
          <p className="muted">
            Access is revoked for everyone immediately. Workspace records, files
            and cached content are permanently purged after 30 days. Export what
            you need first.
          </p>
          <Button
            variant="danger"
            onClick={() => {
              setAction("delete");
              setError(null);
            }}
          >
            Delete workspace
          </Button>
        </section>
      )}
      <Modal
        open={!!action}
        onOpenChange={(v) => {
          if (!v) {
            setAction(null);
            setPassword("");
            setCode("");
          }
        }}
        title={
          action === "leave"
            ? "Leave this company?"
            : action === "transfer"
              ? "Transfer ownership"
              : "Disable and delete workspace?"
        }
        description={
          action === "leave"
            ? "You will lose access. The last owner must transfer ownership first."
            : action === "delete"
              ? "Access is revoked immediately. Workspace records, files and caches are permanently purged after 30 days. Reauthenticate to confirm."
              : "Reauthenticate to confirm this sensitive change."
        }
      >
        <form className="form-stack" onSubmit={perform}>
          <ErrorNotice error={error} />
          {action === "transfer" && (
            <label className="field">
              <span>New owner</span>
              <select
                required
                value={recipient}
                onChange={(e) => setRecipient(e.target.value)}
              >
                <option value="">Choose a member</option>
                {members.data?.items
                  .filter((m) => m.user_id !== user.id && m.status === "active")
                  .map((m) => (
                    <option key={m.user_id} value={m.user_id}>
                      {m.name} — {m.email}
                    </option>
                  ))}
              </select>
            </label>
          )}
          {action === "delete" && (
            <Field
              label={`Type ${organization.slug} to confirm`}
              value={confirmation}
              onChange={(e) => setConfirmation(e.target.value)}
              required
            />
          )}
          {(action !== "leave" || organization.role === "owner") && (
            <>
              <Field
                label="Current password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
              {user.mfa_enabled && (
                <Field
                  label="Authentication code"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  autoComplete="one-time-code"
                  required
                />
              )}
            </>
          )}
          <Button
            busy={busy}
            variant={action === "delete" ? "danger" : "primary"}
            disabled={action === "delete" && confirmation !== organization.slug}
          >
            {action === "leave"
              ? "Leave company"
              : action === "delete"
                ? "Delete workspace"
                : "Transfer ownership"}
          </Button>
        </form>
      </Modal>
    </>
  );
}
