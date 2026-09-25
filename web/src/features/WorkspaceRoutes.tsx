import { lazy, Suspense } from "react";
import { Link, Route, Routes } from "react-router-dom";
import { useWorkspace } from "../app/context";
import { EmptyState, Loading } from "../components/ui";
import {
  HomePage,
  SearchPage,
  SavedPage,
  NotificationsPage,
} from "./Discovery";
const LibraryPage = lazy(() =>
  import("./Library").then((module) => ({ default: module.LibraryPage })),
);
const ConversationsPage = lazy(() =>
  import("./Conversations").then((module) => ({
    default: module.ConversationsPage,
  })),
);
const InventoryPage = lazy(() =>
  import("./AdminPages").then((module) => ({ default: module.InventoryPage })),
);
const IntegrationsPage = lazy(() =>
  import("./AdminPages").then((module) => ({
    default: module.IntegrationsPage,
  })),
);
const InsightsPage = lazy(() =>
  import("./AdminPages").then((module) => ({ default: module.InsightsPage })),
);
const QualityPage = lazy(() =>
  import("./AdminPages").then((module) => ({ default: module.QualityPage })),
);
const AdministrationPage = lazy(() =>
  import("./AdminPages").then((module) => ({
    default: module.AdministrationPage,
  })),
);
const SourcesPage = lazy(() =>
  import("./Sources").then((module) => ({ default: module.SourcesPage })),
);
const ComparePage = lazy(() =>
  import("./Compare").then((module) => ({ default: module.ComparePage })),
);
const KnowledgeMapPage = lazy(() =>
  import("./KnowledgeMap").then((module) => ({
    default: module.KnowledgeMapPage,
  })),
);
const PlaybooksPage = lazy(() =>
  import("./Playbooks").then((module) => ({ default: module.PlaybooksPage })),
);
const BriefingsPage = lazy(() =>
  import("./Briefings").then((module) => ({ default: module.BriefingsPage })),
);
export function WorkspaceRoutes() {
  const { tenantId, user, organization } = useWorkspace();
  return (
    <Suspense fallback={<Loading label="Opening your workspace…" />}>
      <Routes key={`${user.id}:${tenantId}:${organization.auth_revision}`}>
        <Route index element={<HomePage />} />
        <Route path="ask" element={<ConversationsPage />} />
        <Route path="ask/:conversationId" element={<ConversationsPage />} />
        <Route path="library" element={<LibraryPage />} />
        <Route path="library/:documentId" element={<LibraryPage />} />
        <Route path="search" element={<SearchPage />} />
        <Route path="saved" element={<SavedPage />} />
        <Route path="notifications" element={<NotificationsPage />} />
        <Route path="inventory" element={<InventoryPage />} />
        <Route path="sources" element={<SourcesPage />} />
        <Route path="compare" element={<ComparePage />} />
        <Route path="knowledge-map" element={<KnowledgeMapPage />} />
        <Route path="playbooks" element={<PlaybooksPage />} />
        <Route path="briefings" element={<BriefingsPage />} />
        <Route path="integrations" element={<IntegrationsPage />} />
        <Route path="insights" element={<InsightsPage />} />
        <Route path="quality" element={<QualityPage />} />
        <Route path="administration" element={<AdministrationPage />} />
        <Route
          path="*"
          element={
            <EmptyState
              title="This page is unavailable"
              description="The address may have changed. Your workspace and knowledge are available from the navigation."
              action={
                <Link className="button primary" to={`/o/${tenantId}`}>
                  Return home
                </Link>
              }
            />
          }
        />
      </Routes>
    </Suspense>
  );
}
