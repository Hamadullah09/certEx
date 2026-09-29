"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { registerKeys } from "@/hooks/use-register";
import { apiFetch } from "@/lib/api";
import {
  type CertificateDetail,
  type CertificateSummary,
  certificateDetailSchema,
  certificateSummarySchema,
} from "@/lib/schemas/certificates";
import {
  type ReviewSettings,
  type ReviewSummary,
  type WorkspaceSettings,
  reviewSummarySchema,
  revisionListSchema,
  workspaceSettingsSchema,
} from "@/lib/schemas/review";
import { z } from "zod";

/**
 * Acting on a register entry.
 *
 * Every one of these is a person's decision about a historical record, so none of
 * them is optimistic: the screen waits for the server, because showing an entry as
 * approved before it is would be worse than a moment's delay.
 */

export const reviewKeys = {
  summary: ["register", "review-summary"] as const,
  history: (id: string) => ["register", "certificate", id, "history"] as const,
  settings: ["workspace", "settings"] as const,
};

export function useReviewSummary() {
  return useQuery<ReviewSummary>({
    queryKey: reviewKeys.summary,
    queryFn: () => apiFetch("/api/v1/certificates/review-summary", reviewSummarySchema),
    // The queue grows while a batch is processing, so the badge refreshes on its own.
    refetchInterval: 60 * 1000,
  });
}

export function useCertificateHistory(id: string) {
  return useQuery({
    queryKey: reviewKeys.history(id),
    queryFn: () => apiFetch(`/api/v1/certificates/${id}/history`, revisionListSchema),
    enabled: Boolean(id),
  });
}

/** Invalidate everything a decision about one entry could have changed. */
function useRefreshAfterDecision(id: string) {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: registerKeys.certificate(id) });
    void queryClient.invalidateQueries({ queryKey: reviewKeys.history(id) });
    void queryClient.invalidateQueries({ queryKey: reviewKeys.summary });
    // A correction can change a name or a number, so any cached search is stale.
    void queryClient.invalidateQueries({ queryKey: registerKeys.all });
  };
}

export function useApproveCertificate(id: string) {
  const refresh = useRefreshAfterDecision(id);
  return useMutation<CertificateDetail, Error, { note?: string }>({
    mutationFn: (payload) =>
      apiFetch(`/api/v1/certificates/${id}/approve`, certificateDetailSchema, {
        method: "POST",
        body: { note: payload.note ?? null },
      }),
    onSuccess: refresh,
  });
}

export function useCorrectCertificate(id: string) {
  const refresh = useRefreshAfterDecision(id);
  return useMutation<
    CertificateDetail,
    Error,
    { values: Record<string, string>; note: string; approve?: boolean }
  >({
    mutationFn: (payload) =>
      apiFetch(`/api/v1/certificates/${id}`, certificateDetailSchema, {
        method: "PATCH",
        body: payload,
      }),
    onSuccess: refresh,
  });
}

export function useResolveDuplicate(id: string) {
  const refresh = useRefreshAfterDecision(id);
  return useMutation<
    CertificateSummary[],
    Error,
    { otherId: string; sameCertificate: boolean; note?: string }
  >({
    mutationFn: (payload) =>
      apiFetch(
        `/api/v1/certificates/${id}/resolve-duplicate`,
        z.array(certificateSummarySchema),
        {
          method: "POST",
          body: {
            other_id: payload.otherId,
            same_certificate: payload.sameCertificate,
            note: payload.note ?? null,
          },
        },
      ),
    onSuccess: refresh,
  });
}

export function useVoidCertificate(id: string) {
  const refresh = useRefreshAfterDecision(id);
  return useMutation<CertificateDetail, Error, { note: string }>({
    mutationFn: (payload) =>
      apiFetch(`/api/v1/certificates/${id}/void`, certificateDetailSchema, {
        method: "POST",
        body: payload,
      }),
    onSuccess: refresh,
  });
}

export function useWorkspaceSettings() {
  return useQuery<WorkspaceSettings>({
    queryKey: reviewKeys.settings,
    queryFn: () => apiFetch("/api/v1/workspace/settings", workspaceSettingsSchema),
  });
}

export function useSaveWorkspaceSettings() {
  const queryClient = useQueryClient();
  return useMutation<WorkspaceSettings, Error, { review: ReviewSettings }>({
    mutationFn: (payload) =>
      apiFetch("/api/v1/workspace/settings", workspaceSettingsSchema, {
        method: "PUT",
        body: payload,
      }),
    onSuccess: (settings) => {
      queryClient.setQueryData(reviewKeys.settings, settings);
      // The thresholds decide what queues, so the badge may be wrong now.
      void queryClient.invalidateQueries({ queryKey: reviewKeys.summary });
    },
  });
}
