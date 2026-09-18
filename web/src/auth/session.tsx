import { useQuery } from "@tanstack/react-query";
import { api, ApiError, type AuthSession } from "../api/client";
export const sessionKey = ["auth", "session"];
export function useSession() {
  return useQuery({
    queryKey: sessionKey,
    queryFn: async () => {
      try {
        return await api<AuthSession>("/auth/me");
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    retry: false,
    staleTime: 30_000,
  });
}
