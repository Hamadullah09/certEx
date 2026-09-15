"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";

import { ApiError, apiFetch, apiVoid } from "@/lib/api";
import {
  type LoginRequest,
  type SessionResponse,
  sessionResponseSchema,
} from "@/lib/schemas/auth";

export const SESSION_QUERY_KEY = ["session"] as const;

/**
 * The current session, restored from the httpOnly cookie on boot.
 *
 * A 401 here is the normal signed-out state, not an error: it resolves to `null`
 * rather than throwing, so the caller can redirect instead of showing an error.
 */
export function useSession() {
  return useQuery<SessionResponse | null>({
    queryKey: SESSION_QUERY_KEY,
    queryFn: async () => {
      try {
        return await apiFetch("/api/v1/auth/me", sessionResponseSchema);
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    staleTime: 60_000,
    retry: false,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  const router = useRouter();

  return useMutation<SessionResponse, Error, LoginRequest>({
    mutationFn: (credentials) =>
      apiFetch("/api/v1/auth/login", sessionResponseSchema, {
        method: "POST",
        body: credentials,
        // A 401 here means "wrong password", so a transparent refresh would be
        // both pointless and confusing.
        retryOnUnauthorized: false,
      }),
    onSuccess: (session) => {
      queryClient.setQueryData(SESSION_QUERY_KEY, session);
      router.replace("/");
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  const router = useRouter();

  return useMutation<void, Error, void>({
    mutationFn: () =>
      apiVoid("/api/v1/auth/logout", { method: "POST", retryOnUnauthorized: false }),
    onSettled: () => {
      // Clear the cache even if the call failed: the cookies are gone either way,
      // and leaving another workspace's cached rows in memory would be worse.
      queryClient.clear();
      queryClient.setQueryData(SESSION_QUERY_KEY, null);
      router.replace("/login");
    },
  });
}
