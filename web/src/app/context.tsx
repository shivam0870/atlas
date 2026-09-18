import { createContext, useContext } from "react";
import type { Organization, User } from "../api/client";
export type WorkspaceContextValue = {
  organization: Organization;
  user: User;
  tenantId: string;
  canManage: boolean;
  canEdit: boolean;
};
export const WorkspaceContext = createContext<WorkspaceContextValue | null>(
  null,
);
export function useWorkspace() {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error("Workspace context is unavailable");
  return context;
}
export function useScopedQueryKey(...parts: unknown[]) {
  const { user, organization } = useWorkspace();
  return [
    "workspace",
    user.id,
    organization.id,
    organization.auth_revision,
    ...parts,
  ];
}
