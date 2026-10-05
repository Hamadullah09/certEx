"use client";

import { useQuery } from "@tanstack/react-query";

import { ApiError, apiFetch } from "@/lib/api";
import { type TemplateSummary, templatePageSchema } from "@/lib/schemas/templates";

export const templateKeys = {
  all: ["templates"] as const,
  list: () => ["templates", "list"] as const,
};

/**
 * What this workspace has learned.
 *
 * The two outcomes are kept apart on purpose. A route that answers 404 or 405 is an
 * API older than this build, and that is a different fact from "this workspace has
 * learned no templates yet" - a reviewer told the wrong one would either wait for
 * something that is never coming, or go correcting forms to fix a version mismatch.
 */
export type TemplatesResult =
  | { status: "ready"; items: TemplateSummary[]; total: number | null }
  | { status: "unavailable"; status_code: number };

const ROUTE_MISSING_STATUSES = new Set([404, 405, 501]);

export function useTemplates() {
  return useQuery<TemplatesResult>({
    queryKey: templateKeys.list(),
    queryFn: async () => {
      try {
        const page = await apiFetch("/api/v1/templates?limit=100&include_total=true", templatePageSchema);
        return { status: "ready", items: page.items, total: page.meta.total ?? null };
      } catch (error) {
        if (error instanceof ApiError && ROUTE_MISSING_STATUSES.has(error.status)) {
          return { status: "unavailable", status_code: error.status };
        }
        throw error;
      }
    },
    // Nothing is gained by asking a route that does not exist three more times.
    retry: false,
    staleTime: 60_000,
  });
}
