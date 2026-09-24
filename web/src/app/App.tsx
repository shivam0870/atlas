import { useEffect, useState } from "react";
import {
  Navigate,
  Route,
  Routes,
  NavLink,
  useLocation,
  useNavigate,
  useParams,
  Link,
} from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import * as Dialog from "@radix-ui/react-dialog";
import {
  BarChart3,
  BookOpen,
  Bookmark,
  Building2,
  ChevronDown,
  FlaskConical,
  Home,
  KeyRound,
  Layers3,
  LogOut,
  Menu,
  Search,
  Settings,
  Sparkles,
  Users,
  X,
  Database,
} from "lucide-react";
import { api } from "../api/client";
import { useSession } from "../auth/session";
import {
  LoginPage,
  RegisterPage,
  VerifyPage,
  RecoveryPage,
} from "../auth/AuthPages";
import {
  OnboardingPage,
  InvitationPage,
  useOrganizations,
} from "../organizations/Onboarding";
import { AccountPage } from "../auth/AccountPage";
import {
  PeoplePage,
  TeamsPage,
  CompanyPage,
} from "../organizations/Management";
import { WorkspaceRoutes } from "../features/WorkspaceRoutes";
import { CommandPalette, NotificationButton } from "../features/Discovery";
import { WorkspaceContext } from "./context";
import { Button, ErrorNotice, Loading, initials } from "../components/ui";
import {
  ExperienceTools,
  ThemeToggle,
  readPreference,
} from "../components/experience";
function Theme() {
  const session = useSession();
  useEffect(() => {
    const choice = () =>
      readPreference("atlas-theme", session.data?.user.theme || "system");
    const media = matchMedia("(prefers-color-scheme: dark)");
    const apply = () =>
      (document.documentElement.dataset.theme =
        choice() === "system" ? (media.matches ? "dark" : "light") : choice());
    apply();
    media.addEventListener("change", apply);
    window.addEventListener("atlas:theme", apply);
    window.addEventListener("storage", apply);
    return () => {
      media.removeEventListener("change", apply);
      window.removeEventListener("atlas:theme", apply);
      window.removeEventListener("storage", apply);
    };
  }, [session.data?.user.theme]);
  return null;
}
function Entry() {
  const session = useSession();
  const orgs = useOrganizations();
  if (session.isPending || (session.data && orgs.isPending))
    return <Loading label="Opening your workspace…" />;
  if (session.error)
    return (
      <main id="main-content" tabIndex={-1} className="standalone">
        <ErrorNotice error={session.error} />
        <Button onClick={() => session.refetch()}>Try again</Button>
      </main>
    );
  if (!session.data) return <Navigate to="/login" replace />;
  if (orgs.error) return <ErrorNotice error={orgs.error} />;
  const remembered = localStorage.getItem(
    `atlas-workspace:${session.data.user.id}`,
  );
  const target =
    orgs.data?.items.find((org) => org.id === remembered) ||
    orgs.data?.items[0];
  return <Navigate to={target ? `/o/${target.id}` : "/onboarding"} replace />;
}
function WorkspaceShell() {
  const { tenantId = "" } = useParams();
  const session = useSession();
  const orgs = useOrganizations();
  const location = useLocation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [mobile, setMobile] = useState(false);
  const [commandOpen, setCommandOpen] = useState(false);
  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandOpen((value) => !value);
      }
    };
    window.addEventListener("keydown", keyboard);
    return () => window.removeEventListener("keydown", keyboard);
  }, []);
  const [error, setError] = useState<unknown>();
  const organization = orgs.data?.items.find((o) => o.id === tenantId);
  useEffect(() => {
    const page = location.pathname.split("/").slice(3).filter(Boolean);
    const label = (page[0] === "settings" ? page[1] : page[0]) || "Home";
    document.title = `${label.charAt(0).toUpperCase() + label.slice(1).replaceAll("-", " ")} · ${organization?.name || "Workspace"} · Atlas`;
  }, [location.pathname, organization?.name]);
  useEffect(() => {
    if (organization && session.data?.user.id)
      localStorage.setItem(
        `atlas-workspace:${session.data.user.id}`,
        organization.id,
      );
  }, [organization?.id, session.data?.user.id]);
  useEffect(() => {
    setMobile(false);
    return () => {
      void client.cancelQueries({
        queryKey: ["workspace", session.data?.user.id, tenantId],
      });
    };
  }, [tenantId, location.pathname, client, session.data?.user.id]);
  useEffect(() => {
    const expired = () => {
      client.clear();
      navigate(`/login?next=${encodeURIComponent(location.pathname)}`, {
        replace: true,
      });
    };
    window.addEventListener("atlas:session-expired", expired);
    return () => window.removeEventListener("atlas:session-expired", expired);
  }, [client, navigate, location.pathname]);
  useEffect(
    () => () => {
      void client.cancelQueries({
        queryKey: ["workspace", session.data?.user.id, tenantId],
      });
      client.removeQueries({
        queryKey: ["workspace", session.data?.user.id, tenantId],
      });
    },
    [client, session.data?.user.id, tenantId, organization?.auth_revision],
  );
  if (session.isPending || (session.data && orgs.isPending)) return <Loading />;
  if (!session.data)
    return (
      <Navigate
        to={`/login?next=${encodeURIComponent(location.pathname)}`}
        replace
      />
    );
  if (orgs.error) return <ErrorNotice error={orgs.error} />;
  if (!organization)
    return (
      <main id="main-content" tabIndex={-1} className="standalone">
        <h1>Workspace unavailable</h1>
        <p>
          Your membership may have changed. Choose a workspace you can access.
        </p>
        <Link to="/onboarding" className="button primary">
          Choose workspace
        </Link>
      </main>
    );
  const canManage = ["owner", "admin"].includes(organization.role);
  const canEdit = organization.role !== "viewer";
  const base = `/o/${tenantId}`;
  const links = [
    { to: "", label: "Home", icon: Home },
    { to: "/ask", label: "Ask Atlas", icon: Sparkles },
    { to: "/library", label: "Library", icon: BookOpen },
    { to: "/search", label: "Search", icon: Search },
    { to: "/saved", label: "Saved", icon: Bookmark },
    { to: "/inventory", label: "Inventory", icon: Database },
  ];
  const management = [
    { to: "/people", label: "People & teams", icon: Users },
    { to: "/quality", label: "Quality", icon: FlaskConical },
    { to: "/insights", label: "Insights", icon: BarChart3 },
    { to: "/integrations", label: "Integrations", icon: KeyRound },
    { to: "/administration", label: "Administration", icon: Settings },
  ];
  async function logout() {
    try {
      await api("/auth/logout", { method: "POST" });
      await client.cancelQueries();
      client.clear();
      navigate("/login");
    } catch (e) {
      setError(e);
    }
  }
  const sidebar = (
    <>
      <Link className="brand" to={base}>
        <span className="brand-mark">
          <Layers3 size={21} />
        </span>
        atlas<span className="brand-dot">.</span>
      </Link>
      <label className="org-selector">
        <span className="sr-only">Current workspace</span>
        <Building2 size={18} />
        <select
          value={tenantId}
          onChange={(e) => {
            void client.cancelQueries({ queryKey: ["workspace"] });
            client.removeQueries({ queryKey: ["workspace"] });
            navigate(`/o/${e.target.value}`);
          }}
        >
          {orgs.data?.items.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </select>
        <ChevronDown size={14} />
      </label>
      <nav aria-label="Main navigation">
        {links.map(({ to, label, icon: Icon }) => (
          <NavLink key={to} end={!to} to={base + to}>
            <Icon size={18} />
            {label}
          </NavLink>
        ))}
        {!canManage && organization.can_evaluate && (
          <NavLink to={`${base}/quality`}>
            <FlaskConical size={18} />
            Quality
          </NavLink>
        )}
        {canManage && (
          <>
            <div className="nav-label">MANAGE</div>
            {management.map(({ to, label, icon: Icon }) => (
              <NavLink key={to} to={base + to}>
                <Icon size={18} />
                {label}
              </NavLink>
            ))}
          </>
        )}
        <div className="nav-label">WORKSPACE</div>
        <NavLink to={`${base}/settings/company`}>
          <Settings size={18} />
          Workspace settings
        </NavLink>
        <Link to="/onboarding">
          <Building2 size={18} />
          Add workspace
        </Link>
      </nav>
      <div className="sidebar-bottom">
        <NavLink className="account-link" to={`${base}/settings/account`}>
          <span className="avatar">{initials(session.data.user.name)}</span>
          <span>
            <strong>{session.data.user.name}</strong>
            <small>My account</small>
          </span>
        </NavLink>
        <Button variant="ghost" aria-label="Sign out" onClick={logout}>
          <LogOut size={18} />
        </Button>
      </div>
    </>
  );
  return (
    <WorkspaceContext.Provider
      value={{
        organization,
        user: session.data.user,
        tenantId,
        canManage,
        canEdit,
      }}
    >
      <CommandPalette open={commandOpen} onOpenChange={setCommandOpen} />
      <div className="app-shell">
        <aside className="sidebar desktop-sidebar">{sidebar}</aside>
        <div className="main-column">
          <header className="topbar">
            <Dialog.Root open={mobile} onOpenChange={setMobile}>
              <Dialog.Trigger asChild>
                <Button
                  className="mobile-menu"
                  variant="ghost"
                  aria-label="Open navigation"
                >
                  <Menu size={21} />
                </Button>
              </Dialog.Trigger>
              <Dialog.Portal>
                <Dialog.Overlay className="modal-overlay" />
                <Dialog.Content
                  className="sidebar mobile-sidebar"
                  aria-describedby={undefined}
                >
                  <Dialog.Title className="sr-only">
                    Workspace navigation
                  </Dialog.Title>
                  <Dialog.Close
                    className="drawer-close"
                    aria-label="Close navigation"
                  >
                    <X size={20} />
                  </Dialog.Close>
                  {sidebar}
                </Dialog.Content>
              </Dialog.Portal>
            </Dialog.Root>
            <div className="breadcrumb">
              <span>{organization.name}</span>
              <span>/</span>
              <strong>
                {location.pathname.split("/")[3]?.replaceAll("-", " ") ||
                  "Home"}
              </strong>
            </div>
            <button
              type="button"
              className="topbar-search"
              onClick={() => setCommandOpen(true)}
              aria-label="Search and navigate Atlas"
            >
              <Search size={16} />
              <span>Search knowledge</span>
              <kbd>⌘ K</kbd>
            </button>
            <ThemeToggle />
            <NotificationButton />
            <span className="role-tag">{organization.role}</span>
          </header>
          <main
            id="main-content"
            tabIndex={-1}
            className="page-content"
            key={`${session.data.user.id}:${tenantId}:${organization.auth_revision}`}
          >
            <ErrorNotice error={error} />
            <Routes>
              <Route path="settings/account" element={<AccountPage />} />
              <Route path="settings/company" element={<CompanyPage />} />
              <Route path="people" element={<PeoplePage />} />
              <Route path="teams" element={<TeamsPage />} />
              <Route path="*" element={<WorkspaceRoutes />} />
            </Routes>
          </main>
        </div>
      </div>
    </WorkspaceContext.Provider>
  );
}
function StandaloneAccount() {
  const session = useSession();
  if (session.isPending) return <Loading />;
  if (!session.data) return <Navigate to="/login?next=/account" replace />;
  return (
    <main id="main-content" tabIndex={-1} className="standalone-account">
      <Link className="text-link" to="/onboarding">
        ← Your workspaces
      </Link>
      <AccountPage />
    </main>
  );
}
export function App() {
  return (
    <>
      <Theme />
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <ExperienceTools />
      <Routes>
        <Route path="/" element={<Entry />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/verify-email" element={<VerifyPage />} />
        <Route path="/verify-email-change" element={<VerifyPage />} />
        <Route path="/forgot-password" element={<RecoveryPage />} />
        <Route path="/reset-password" element={<RecoveryPage reset />} />
        <Route path="/onboarding" element={<OnboardingPage />} />
        <Route path="/account" element={<StandaloneAccount />} />
        <Route path="/invite" element={<InvitationPage />} />
        <Route path="/accept-invitation" element={<InvitationPage />} />
        <Route path="/o/:tenantId/*" element={<WorkspaceShell />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}
