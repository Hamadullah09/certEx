"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { rowKeys } from "@/hooks/use-rows";
import { apiFetch } from "@/lib/api";
import {
  type UnitSummary,
  unitListSchema,
  unitPageSchema,
  unitSummarySchema,
} from "@/lib/schemas/units";

export const unitKeys = {
  all: ["units"] as const,
  list: (batchId: string) => ["units", "batch", batchId] as const,
};

export function useBatchUnits(batchId: string, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: unitKeys.list(batchId),
    queryFn: () => apiFetch(`/api/v1/units?batch_id=${batchId}&limit=200`, unitPageSchema),
    enabled: Boolean(batchId) && (options.enabled ?? true),
  });
}

/**
 * Split one certificate in two at a page.
 *
 * Both halves go back through classification server-side, so their type and
 * fields will change; every cached row is dropped rather than patched, because the
 * split replaced the row the reviewer was looking at with two new ones.
 */
export function useSplitUnit() {
  const queryClient = useQueryClient();
  return useMutation<UnitSummary[], Error, { unitId: string; atPage: number }>({
    mutationFn: ({ unitId, atPage }) =>
      apiFetch(`/api/v1/units/${unitId}/split`, unitListSchema, {
        method: "POST",
        body: { at_page: atPage },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: rowKeys.all });
      void queryClient.invalidateQueries({ queryKey: unitKeys.all });
    },
  });
}

export function useMergeUnits() {
  const queryClient = useQueryClient();
  return useMutation<UnitSummary, Error, string[]>({
    mutationFn: (unitIds) =>
      apiFetch("/api/v1/units/merge", unitSummarySchema, {
        method: "POST",
        body: { unit_ids: unitIds },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: rowKeys.all });
      void queryClient.invalidateQueries({ queryKey: unitKeys.all });
    },
  });
}
