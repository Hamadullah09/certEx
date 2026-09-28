/**
 * The results grid's filter state and the query string it becomes.
 *
 * Kept free of React and the network so the rules - which filters exist, when a
 * total is worth asking for, what counts as "no filter" - are unit-testable, and
 * so the query key and the request are built from one function rather than two
 * that can drift.
 */

import type { CertificateType } from "@/lib/schemas/batches";
import type { ReviewStatus } from "@/lib/schemas/rows";

/** Rows per request. The server caps `limit` at 200. */
export const ROWS_PAGE_SIZE = 100;

export interface RowFilters {
  status?: ReviewStatus | undefined;
  type?: CertificateType | undefined;
  flag?: string | undefined;
  search?: string | undefined;
}

export interface RowsQueryOptions {
  limit?: number;
  cursor?: string | null;
  /** Ask the server to count the matches as well as return them. */
  includeTotal?: boolean;
}

/**
 * Drop empty filters and trim the text ones.
 *
 * Used for the query key as well as the request, so an empty search box and a
 * search box that was typed into and cleared share one cache entry instead of
 * refetching the same rows under two keys.
 */
export function normalizeRowFilters(filters: RowFilters): RowFilters {
  const normalized: RowFilters = {};
  if (filters.status) normalized.status = filters.status;
  if (filters.type) normalized.type = filters.type;
  const flag = filters.flag?.trim();
  if (flag) normalized.flag = flag;
  const search = filters.search?.trim();
  if (search) normalized.search = search;
  return normalized;
}

export function hasAnyRowFilter(filters: RowFilters): boolean {
  return Object.keys(normalizeRowFilters(filters)).length > 0;
}

/**
 * The query string for `GET /batches/{id}/rows`, without its leading `?`.
 *
 * `include_total` is only sent for the first page: the count cannot change
 * between pages of one filter set, and counting is the expensive half of the
 * query on a workspace holding millions of rows.
 */
export function buildRowsQuery(filters: RowFilters, options: RowsQueryOptions = {}): string {
  const normalized = normalizeRowFilters(filters);
  const params = new URLSearchParams();
  params.set("limit", String(options.limit ?? ROWS_PAGE_SIZE));
  if (options.cursor) params.set("cursor", options.cursor);
  if (normalized.status) params.set("filter[status]", normalized.status);
  if (normalized.type) params.set("filter[type]", normalized.type);
  if (normalized.flag) params.set("filter[flag]", normalized.flag);
  if (normalized.search) params.set("search", normalized.search);
  if (options.includeTotal && !options.cursor) params.set("include_total", "true");
  return params.toString();
}
