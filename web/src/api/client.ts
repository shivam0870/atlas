export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
  }
}
export type ApiOptions = RequestInit & { tenantId?: string };
export async function api<T>(
  path: string,
  options: ApiOptions = {},
): Promise<T> {
  const { tenantId, ...init } = options;
  const headers = new Headers(init.headers);
  headers.set("X-Atlas-Client", "console");
  if (tenantId) headers.set("X-Atlas-Tenant", tenantId);
  if (init.body && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch(`/api${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  if (!response.ok) {
    const body = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    const detail = body.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((e: { msg?: string }) => e.msg).join("; ")
          : detail?.message ||
            body.message ||
            "The request could not be completed.";
    if (response.status === 401 && !path.startsWith("/auth/"))
      window.dispatchEvent(new Event("atlas:session-expired"));
    throw new ApiError(response.status, message, detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
export const json = (value: unknown) => JSON.stringify(value);
export type User = {
  id: string;
  email: string;
  name: string;
  email_verified: boolean;
  mfa_enabled: boolean;
  theme: "light" | "dark" | "system";
};
export type Organization = {
  id: string;
  name: string;
  slug: string;
  role: "owner" | "admin" | "editor" | "viewer";
  auth_revision: number;
  can_evaluate?: boolean;
  kind: "company" | "personal";
  description?: string;
  website?: string;
};
export type AuthSession = { user: User; session_id: string };
