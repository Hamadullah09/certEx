"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { SESSION_QUERY_KEY } from "@/hooks/use-session";
import { apiFetch, apiVoid } from "@/lib/api";
import type { UserRole } from "@/lib/schemas/auth";
import { type UserSummary, userListSchema, userSummarySchema } from "@/lib/schemas/users";

export const userKeys = {
  all: ["users"] as const,
  list: () => ["users", "list"] as const,
};

export function useUsers(options: { enabled?: boolean } = {}) {
  return useQuery<UserSummary[]>({
    queryKey: userKeys.list(),
    queryFn: () => apiFetch("/api/v1/users", userListSchema),
    enabled: options.enabled ?? true,
    staleTime: 30_000,
  });
}

export interface NewUser {
  email: string;
  full_name?: string;
  role: UserRole;
  password: string;
}

export function useCreateUser() {
  const queryClient = useQueryClient();
  return useMutation<UserSummary, Error, NewUser>({
    mutationFn: (payload) =>
      apiFetch("/api/v1/users", userSummarySchema, { method: "POST", body: payload }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: userKeys.all });
    },
  });
}

export interface UserChange {
  id: string;
  full_name?: string;
  role?: UserRole;
  is_active?: boolean;
}

export function useUpdateUser() {
  const queryClient = useQueryClient();
  return useMutation<UserSummary, Error, UserChange>({
    mutationFn: ({ id, ...change }) =>
      apiFetch(`/api/v1/users/${id}`, userSummarySchema, { method: "PATCH", body: change }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: userKeys.all });
      // A change to your own name shows in the header, which reads the session.
      void queryClient.invalidateQueries({ queryKey: SESSION_QUERY_KEY });
    },
  });
}

/**
 * An administrator setting somebody's password after being asked in person.
 *
 * Ends every session of theirs server-side. If the reason for the reset is that
 * somebody else knew the old password, leaving those alive would defeat it.
 */
export function useSetPassword() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, { id: string; password: string }>({
    mutationFn: ({ id, password }) =>
      apiVoid(`/api/v1/users/${id}/password`, { method: "POST", body: { password } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: userKeys.all });
    },
  });
}

/** Changing your own, which needs the current one. */
export function useChangeOwnPassword() {
  return useMutation<void, Error, { current_password: string; new_password: string }>({
    mutationFn: (body) => apiVoid("/api/v1/users/me/password", { method: "POST", body }),
  });
}

/**
 * Say you have forgotten your password, from the sign-in screen.
 *
 * Always resolves, whatever address is given - the server answers identically for one
 * that exists and one that does not, because anything else would make this a list of
 * who works here.
 */
export function useForgotPassword() {
  return useMutation<void, Error, string>({
    mutationFn: (email) =>
      apiVoid("/api/v1/auth/forgot-password", {
        method: "POST",
        body: { email },
        retryOnUnauthorized: false,
      }),
  });
}
