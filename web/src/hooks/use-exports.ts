"use client";

import { useMutation, useQuery } from "@tanstack/react-query";

import { apiFetch, request } from "@/lib/api";
import {
  type ExportFormat,
  type ExportPreview,
  exportPreviewSchema,
} from "@/lib/schemas/exports";
import type { RowFilters } from "@/lib/rows-query";

export const exportKeys = {
  all: ["exports"] as const,
  preview: (batchId: string, filters: RowFilters, options: ExportOptions) =>
    ["exports", "preview", batchId, filters, options] as const,
};

export interface ExportOptions {
  /** A confidence column beside each value, for an office that audits the reading. */
  includeConfidence?: boolean;
  /** The text as printed, beside the value as understood. */
  includeSnippet?: boolean;
  /** One file per certificate type, delivered as a zip. */
  perType?: boolean;
}

/**
 * The download is built from what is on screen.
 *
 * The filters the reviewer set are the filters the export uses, because anything else
 * is a trap: filtering to the twelve rows that failed a check and then downloading all
 * four hundred is not a surprise anybody recovers from quickly.
 */
function exportQuery(filters: RowFilters, options: ExportOptions): URLSearchParams {
  const query = new URLSearchParams();
  if (filters.status) query.set("filter[status]", filters.status);
  if (filters.type) query.set("filter[type]", filters.type);
  if (filters.flag) query.set("filter[flag]", filters.flag);
  if (filters.search) query.set("search", filters.search);
  if (options.includeConfidence) query.set("include_confidence", "true");
  if (options.includeSnippet) query.set("include_snippet", "true");
  return query;
}

export function useExportPreview(
  batchId: string,
  filters: RowFilters,
  options: ExportOptions = {},
) {
  return useQuery<ExportPreview>({
    queryKey: exportKeys.preview(batchId, filters, options),
    queryFn: () =>
      apiFetch(
        `/api/v1/batches/${batchId}/export/preview?${exportQuery(filters, options).toString()}`,
        exportPreviewSchema,
      ),
    enabled: Boolean(batchId),
    // Rows change as a reviewer corrects them, and a stale count on a download button
    // is worse than no count.
    staleTime: 10_000,
  });
}

/** The filename the server chose, or the one it already told us about. */
function filenameFrom(response: Response, fallback: string): string {
  const disposition = response.headers.get("content-disposition") ?? "";
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded);
    } catch {
      // A malformed header is not worth failing a download over.
    }
  }
  return /filename="([^"]+)"/i.exec(disposition)?.[1] ?? fallback;
}

export interface DownloadRequest {
  batchId: string;
  format: ExportFormat;
  filters: RowFilters;
  options?: ExportOptions;
  /** Used when the server sends no filename of its own. */
  fallbackName: string;
}

/**
 * Fetch the export and hand it to the browser as a file.
 *
 * Done through the API client rather than as a plain link so that an expired session
 * refreshes transparently and a refusal arrives as a message on screen. A link would
 * save the error page itself as "export.csv", which somebody would then email on.
 */
export function useDownloadExport() {
  return useMutation<string, Error, DownloadRequest>({
    mutationFn: async ({ batchId, format, filters, options = {}, fallbackName }) => {
      const query = exportQuery(filters, options);
      query.set("format", format);
      if (options.perType) query.set("per_type", "true");

      // `request` rather than `apiFetch`: the body is a file, not JSON, but it still
      // goes through the client that refreshes an expired session and turns a refusal
      // into an ApiError.
      const response = await request(
        `/api/v1/batches/${batchId}/export?${query.toString()}`,
      );

      const blob = await response.blob();
      const filename = filenameFrom(response, fallbackName);
      const url = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = filename;
        document.body.append(anchor);
        anchor.click();
        anchor.remove();
      } finally {
        // Revoked on the next tick: Safari has not finished reading the blob when
        // click() returns, and revoking immediately gives an empty file.
        setTimeout(() => URL.revokeObjectURL(url), 10_000);
      }
      return filename;
    },
  });
}
