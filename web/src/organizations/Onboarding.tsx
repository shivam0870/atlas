import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Building2, UserRound } from "lucide-react";
import { api, json, type Organization } from "../api/client";
import { AuthLayout } from "../auth/AuthPages";
import { useSession } from "../auth/session";
import { Button, ErrorNotice, Field, Loading } from "../components/ui";
export function useOrganizations() {
  const session = useSession();
  return useQuery({
    queryKey: ["organizations", session.data?.user.id],
    queryFn: () => api<{ items: Organization[] }>("/organizations"),
    enabled: !!session.data,
    staleTime: 10_000,
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  });
}
export function OnboardingPage() {
  const session = useSession();
  const orgs = useOrganizations();
  const invitations = useQuery({
    queryKey: ["pending-invitations", session.data?.user.id],
    queryFn: ({ signal }) =>
      api<{
        items: {
          id: string;
          tenant_id: string;
          name: string;
          slug: string;
          role: string;
          expires_at: string;
        }[];
      }>("/organizations/pending-invitations", { signal }),
    enabled: !!session.data,
    staleTime: 0,
  });
  const navigate = useNavigate();
  const client = useQueryClient();
  const [kind, setKind] = useState<"company" | "personal">("company");
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);
  const [description, setDescription] = useState("");
  const [website, setWebsite] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  if (session.isPending) return <Loading />;
  if (!session.data) return <Navigate to="/login?next=/onboarding" replace />;
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api<{ organization: Organization }>(
        "/organizations",
        {
          method: "POST",
          body: json({ name, slug, kind, description, website }),
        },
      );
      await client.invalidateQueries({ queryKey: ["organizations"] });
      navigate(`/o/${result.organization.id}`);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <AuthLayout>
      <span className="eyebrow">YOUR NEXT CHAPTER</span>
      <h1>A place for your knowledge</h1>
      <p className="muted">
        Create a workspace, or open one you already belong to.
      </p>
      <ErrorNotice error={error || invitations.error} />
      {!!invitations.data?.items.length && (
        <section
          className="panel form-stack"
          aria-label="Your company invitations"
        >
          <h2>You’re invited</h2>
          {invitations.data.items.map((invitation) => (
            <div className="workspace-option" key={invitation.id}>
              <Building2 size={20} />
              <span>
                <strong>{invitation.name}</strong>
                <small>
                  {invitation.role} · Expires{" "}
                  {new Date(invitation.expires_at).toLocaleDateString()}
                </small>
              </span>
              <Button
                type="button"
                aria-label={`Join ${invitation.name}`}
                busy={busy}
                onClick={async () => {
                  setBusy(true);
                  setError(null);
                  try {
                    const result = await api<{ organization: Organization }>(
                      `/organizations/pending-invitations/${invitation.id}/accept`,
                      { method: "POST" },
                    );
                    await client.invalidateQueries({
                      queryKey: ["organizations"],
                    });
                    await client.invalidateQueries({
                      queryKey: ["pending-invitations"],
                    });
                    navigate(`/o/${result.organization.id}`);
                  } catch (error) {
                    setError(error);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Join company
              </Button>
            </div>
          ))}
        </section>
      )}
      {!!orgs.data?.items.length && (
        <div className="workspace-options">
          {orgs.data.items.map((org) => (
            <Link className="workspace-option" key={org.id} to={`/o/${org.id}`}>
              <Building2 size={20} />
              <span>
                <strong>{org.name}</strong>
                <small>{org.role}</small>
              </span>
              <ArrowRight size={18} />
            </Link>
          ))}
        </div>
      )}
      <div className="segmented" aria-label="Workspace type">
        <button
          aria-pressed={kind === "company"}
          onClick={() => setKind("company")}
        >
          <Building2 size={16} />
          Company
        </button>
        <button
          aria-pressed={kind === "personal"}
          onClick={() => setKind("personal")}
        >
          <UserRound size={16} />
          Personal
        </button>
      </div>
      <form className="form-stack" onSubmit={submit}>
        <Field
          label={kind === "company" ? "Company name" : "Workspace name"}
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            if (!slugEdited)
              setSlug(
                e.target.value
                  .toLowerCase()
                  .replace(/[^a-z0-9]+/g, "-")
                  .replace(/^-|-$/g, ""),
              );
          }}
          required
          maxLength={120}
        />
        <Field
          label="Workspace address"
          hint="Lowercase letters, numbers and hyphens. This must be unique."
          pattern="[a-z0-9][a-z0-9-]*"
          value={slug}
          onChange={(e) => {
            setSlugEdited(true);
            setSlug(e.target.value.toLowerCase());
          }}
          required
        />
        <Field
          label="Description (optional)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        {kind === "company" && (
          <Field
            label="Website (optional)"
            type="url"
            value={website}
            onChange={(e) => setWebsite(e.target.value)}
          />
        )}
        <Button busy={busy}>
          Create {kind === "company" ? "company" : "workspace"}
          <ArrowRight size={17} />
        </Button>
      </form>
      <p className="muted text-small">
        Joining a company? Open the invitation link sent to your email.
      </p>
      <Link className="text-link" to="/account">
        Manage my account
      </Link>
    </AuthLayout>
  );
}
export function InvitationPage() {
  const [params] = useSearchParams();
  const token = params.get("token");
  const session = useSession();
  const client = useQueryClient();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  async function accept() {
    setBusy(true);
    setError(null);
    try {
      const result = await api<{ organization: Organization }>(
        "/organizations/invitations/accept",
        { method: "POST", body: json({ token }) },
      );
      await client.invalidateQueries({ queryKey: ["organizations"] });
      navigate(`/o/${result.organization.id}`);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <AuthLayout>
      <span className="eyebrow">YOU’RE INVITED</span>
      <h1>Join your team on Atlas</h1>
      <p className="muted">
        Sign in with the verified email address that received this invitation.
      </p>
      <div className="form-stack">
        <ErrorNotice error={error} />
        {!token ? (
          <ErrorNotice error="This invitation link is missing its token. Ask the sender for a new invitation." />
        ) : session.isPending ? (
          <Loading />
        ) : session.data ? (
          <>
            <p>
              Signed in as <strong>{session.data.user.email}</strong>
            </p>
            <Button busy={busy} onClick={accept}>
              Accept invitation
              <ArrowRight size={17} />
            </Button>
            <Button
              variant="secondary"
              busy={busy}
              onClick={async () => {
                setBusy(true);
                setError(null);
                try {
                  await api("/auth/logout", { method: "POST" });
                  await client.cancelQueries();
                  client.clear();
                  navigate(
                    `/login?next=${encodeURIComponent(`/invite?token=${token}`)}`,
                    { replace: true },
                  );
                } catch (error) {
                  setError(error);
                } finally {
                  setBusy(false);
                }
              }}
            >
              Use another account
            </Button>
          </>
        ) : (
          <>
            <Link
              className="button primary"
              to={`/login?next=${encodeURIComponent(`/invite?token=${token}`)}`}
            >
              Sign in to accept
            </Link>
            <Link
              className="button secondary"
              to={`/register?next=${encodeURIComponent(`/invite?token=${token}`)}`}
            >
              Create an account
            </Link>
          </>
        )}
      </div>
    </AuthLayout>
  );
}
