"use client";

import {
  type InfiniteData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { apiFetch, request } from "@/lib/api";
import {
  type RowCorrection,
  type RowDetail,
  type RowPage,
  type RowSummary,
  rowDetailSchema,
  rowPageSchema,
} from "@/lib/schemas/rows";
import {
  applyApproval,
  applyFieldEdit,
  applyFieldEditToDetail,
  mapRowsInPages,
  replaceRowInPages,
} from "@/lib/rows-optimistic";
import {
  type RowFilters,
  ROWS_PAGE_SIZE,
  buildRowsQuery,
  normalizeRowFilters,
} from "@/lib/rows-query";

type RowInfiniteData = InfiniteData<RowPage, string | null>;

export const rowKeys = {
  all: ["rows"] as const,
  list: (batchId: string, filters: RowFilters) =>
    ["rows", "list", batchId, normalizeRowFilters(filters)] as const,
  detail: (rowId: string) => ["rows", "detail", rowId] as const,
  pageImage: (documentId: string, pageNumber: number) =>
    ["rows", "page-image", documentId, pageNumber] as const,
};

/**
 * One batch's rows, a page at a time.
 *
 * Cursor pagination rather than offsets, because a batch is still being written
 * while a reviewer works through it and `OFFSET` would silently skip or repeat
 * rows as earlier ones are inserted.
 */
export function useBatchRows(batchId: string, filters: RowFilters = {}) {
  return useInfiniteQuery({
    queryKey: rowKeys.list(batchId, filters),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      apiFetch(
        `/api/v1/batches/${batchId}/rows?${buildRowsQuery(filters, {
          limit: ROWS_PAGE_SIZE,
          cursor: pageParam,
          includeTotal: true,
        })}`,
        rowPageSchema,
      ),
    getNextPageParam: (lastPage) => lastPage.meta.next_cursor ?? null,
    enabled: Boolean(batchId),
  });
}

/** One row opened for review, with provenance for every field. */
export function useRow(rowId: string | null) {
  return useQuery({
    queryKey: rowKeys.detail(rowId ?? ""),
    queryFn: () => apiFetch(`/api/v1/rows/${rowId}`, rowDetailSchema),
    enabled: Boolean(rowId),
  });
}

/**
 * The page a value was read from, as a blob.
 *
 * Fetched through the API client rather than by pointing an `<img>` at the URL:
 * the client carries the session cookie and can refresh an expired one, which an
 * image element cannot - a silently broken image is the worst way to find out a
 * session lapsed.
 */
export function usePageImage(documentId: string | null, pageNumber: number | null) {
  return useQuery({
    queryKey: rowKeys.pageImage(documentId ?? "", pageNumber ?? 0),
    queryFn: async () => {
      const response = await request(
        `/api/v1/documents/${documentId}/pages/${pageNumber}/image`,
      );
      return response.blob();
    },
    enabled: Boolean(documentId) && typeof pageNumber === "number" && pageNumber > 0,
    // A page image is rendered once and then cached by the server; holding it for
    // a while means arrowing up and down a column does not refetch it.
    staleTime: 10 * 60_000,
    gcTime: 15 * 60_000,
  });
}

interface CorrectionVariables {
  rowId: string;
  fields: Record<string, string | null>;
  approve?: boolean;
}

interface CorrectionContext {
  list: RowInfiniteData | undefined;
  detail: RowDetail | undefined;
}

/**
 * Correct one row.
 *
 * The cell settles from the optimistic reducers before the request goes out, and
 * the pre-edit cache is kept so a failure puts the old value back rather than
 * leaving a reviewer believing a correction was saved.
 *
 * On success the server's own re-validated, re-scored row replaces the guess. The
 * list is deliberately not invalidated: a row that no longer matches the active
 * filter would vanish from under the cursor mid-review, so it stays until the next
 * fetch of that filter.
 */
export function useCorrectRow(batchId: string, filters: RowFilters = {}) {
  const queryClient = useQueryClient();
  const listKey = rowKeys.list(batchId, filters);

  return useMutation<RowDetail, Error, CorrectionVariables, CorrectionContext>({
    mutationFn: ({ rowId, fields, approve }) => {
      const body: RowCorrection = { fields, ...(approve ? { approve: true } : {}) };
      return apiFetch(`/api/v1/rows/${rowId}`, rowDetailSchema, { method: "PATCH", body });
    },
    onMutate: async ({ rowId, fields, approve }) => {
      await queryClient.cancelQueries({ queryKey: listKey });
      const list = queryClient.getQueryData<RowInfiniteData>(listKey);
      const detail = queryClient.getQueryData<RowDetail>(rowKeys.detail(rowId));
      const now = new Date().toISOString();

      if (list) {
        const edited = list.pages
          .flatMap((page) => page.items)
          .find((item) => item.id === rowId);
        if (edited) {
          let next: RowSummary = edited;
          for (const [name, value] of Object.entries(fields)) {
            next = applyFieldEdit(next, name, value, now);
          }
          if (approve) next = applyApproval(next, now);
          queryClient.setQueryData<RowInfiniteData>(listKey, replaceRowInPages(list, next));
        }
      }

      if (detail) {
        let next = detail;
        for (const [name, value] of Object.entries(fields)) {
          next = applyFieldEditToDetail(next, name, value, now);
        }
        if (approve) next = { ...next, ...applyApproval(next, now) };
        queryClient.setQueryData<RowDetail>(rowKeys.detail(rowId), next);
      }

      return { list, detail };
    },
    onError: (_error, variables, context) => {
      if (context?.list) queryClient.setQueryData(listKey, context.list);
      if (context?.detail) {
        queryClient.setQueryData(rowKeys.detail(variables.rowId), context.detail);
      }
    },
    onSuccess: (row) => {
      queryClient.setQueryData<RowDetail>(rowKeys.detail(row.id), row);
      const list = queryClient.getQueryData<RowInfiniteData>(listKey);
      if (list) queryClient.setQueryData<RowInfiniteData>(listKey, replaceRowInPages(list, row));
    },
  });
}

/** How many approvals are in flight at once. */
const APPROVE_CONCURRENCY = 4;

export interface BulkApproveResult {
  approved: string[];
  failed: { rowId: string; message: string }[];
}

/**
 * Approve several rows.
 *
 * There is no bulk endpoint, so this is one `PATCH` per row, a few at a time -
 * enough to be quick on a hundred rows without opening a hundred connections.
 * Partial failure is normal (one row may have been deleted, or the reviewer may
 * lack the role), so the result names what did not go through instead of failing
 * the whole action.
 */
export function useApproveRows(batchId: string, filters: RowFilters = {}) {
  const queryClient = useQueryClient();
  const listKey = rowKeys.list(batchId, filters);

  return useMutation<BulkApproveResult, Error, string[], CorrectionContext>({
    mutationFn: async (rowIds) => {
      const approved: string[] = [];
      const failed: BulkApproveResult["failed"] = [];
      const queue = [...rowIds];

      const workers = Array.from({ length: Math.min(APPROVE_CONCURRENCY, queue.length) }, () =>
        (async () => {
          for (;;) {
            const rowId = queue.shift();
            if (!rowId) return;
            try {
              const row = await apiFetch(`/api/v1/rows/${rowId}`, rowDetailSchema, {
                method: "PATCH",
                body: { fields: {}, approve: true } satisfies RowCorrection,
              });
              queryClient.setQueryData<RowDetail>(rowKeys.detail(row.id), row);
              approved.push(rowId);
            } catch (error) {
              failed.push({
                rowId,
                message: error instanceof Error ? error.message : "Could not approve this row.",
              });
            }
          }
        })(),
      );

      await Promise.all(workers);
      return { approved, failed };
    },
    onMutate: async (rowIds) => {
      await queryClient.cancelQueries({ queryKey: listKey });
      const list = queryClient.getQueryData<RowInfiniteData>(listKey);
      if (list) {
        const now = new Date().toISOString();
        queryClient.setQueryData<RowInfiniteData>(
          listKey,
          mapRowsInPages(list, new Set(rowIds), (row) => applyApproval(row, now)),
        );
      }
      return { list, detail: undefined };
    },
    onError: (_error, _rowIds, context) => {
      if (context?.list) queryClient.setQueryData(listKey, context.list);
    },
    onSuccess: (result, _rowIds, context) => {
      // Roll the failures back individually: the rows that did go through keep
      // their new status, so a reviewer does not re-approve work already done.
      if (result.failed.length === 0 || !context?.list) return;
      const stale = new Set(result.failed.map((entry) => entry.rowId));
      const original = new Map(
        context.list.pages.flatMap((page) => page.items).map((item) => [item.id, item]),
      );
      const current = queryClient.getQueryData<RowInfiniteData>(listKey);
      if (!current) return;
      queryClient.setQueryData<RowInfiniteData>(
        listKey,
        mapRowsInPages(current, stale, (row) => original.get(row.id) ?? row),
      );
    },
  });
}

/**
 * Read one certificate again.
 *
 * The server keeps values a person typed and re-runs the rest, so this is safe to
 * offer on a row that has already been corrected.
 */
export function useReprocessRows() {
  const queryClient = useQueryClient();

  return useMutation<BulkApproveResult, Error, string[]>({
    mutationFn: async (rowIds) => {
      const approved: string[] = [];
      const failed: BulkApproveResult["failed"] = [];
      for (const rowId of rowIds) {
        try {
          await apiFetch(`/api/v1/rows/${rowId}/reprocess`, rowDetailSchema, { method: "POST" });
          approved.push(rowId);
        } catch (error) {
          failed.push({
            rowId,
            message: error instanceof Error ? error.message : "Could not queue this certificate.",
          });
        }
      }
      return { approved, failed };
    },
    onSuccess: () => {
      // Reprocessing happens in a worker, so the row will change under us; drop
      // what is cached and let the next render fetch the real state.
      void queryClient.invalidateQueries({ queryKey: rowKeys.all });
    },
  });
}
