"use client";

import * as React from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { ApiError, apiFetch, apiVoid, apiBaseUrl, CSRF_COOKIE, CSRF_HEADER } from "@/lib/api";
import {
  type BatchDetail,
  type BatchSettings,
  type BatchStatus,
  type UploadedFile,
  batchDetailSchema,
  batchPageSchema,
  documentPageSchema,
  uploadResultSchema,
} from "@/lib/schemas/batches";

export const batchKeys = {
  all: ["batches"] as const,
  list: (filters: { status?: BatchStatus; search?: string }) =>
    ["batches", "list", filters] as const,
  detail: (id: string) => ["batches", "detail", id] as const,
  documents: (id: string) => ["batches", id, "documents"] as const,
};

export function useBatchList(filters: { status?: BatchStatus; search?: string } = {}) {
  return useInfiniteQuery({
    queryKey: batchKeys.list(filters),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ limit: "50" });
      if (pageParam) params.set("cursor", pageParam);
      if (filters.status) params.set("filter[status]", filters.status);
      if (filters.search) params.set("search", filters.search);
      return apiFetch(`/api/v1/batches?${params.toString()}`, batchPageSchema);
    },
    getNextPageParam: (lastPage) => lastPage.meta.next_cursor ?? null,
  });
}

export function useBatch(id: string, options: { poll?: boolean } = {}) {
  return useQuery({
    queryKey: batchKeys.detail(id),
    queryFn: () => apiFetch(`/api/v1/batches/${id}`, batchDetailSchema),
    // While a batch is processing the client polls; once it reaches a terminal
    // state polling stops, so an idle dashboard costs nothing.
    refetchInterval: options.poll ? 3000 : false,
    enabled: Boolean(id),
  });
}

export function useBatchDocuments(id: string, options: { poll?: boolean } = {}) {
  return useQuery({
    queryKey: batchKeys.documents(id),
    queryFn: () => apiFetch(`/api/v1/batches/${id}/documents?limit=200`, documentPageSchema),
    refetchInterval: options.poll ? 3000 : false,
    enabled: Boolean(id),
  });
}

export function useCreateBatch() {
  const queryClient = useQueryClient();
  return useMutation<BatchDetail, Error, { name: string; settings: BatchSettings }>({
    mutationFn: (payload) =>
      apiFetch("/api/v1/batches", batchDetailSchema, { method: "POST", body: payload }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: batchKeys.all });
    },
  });
}

export function useDeleteBatch() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (id) => apiVoid(`/api/v1/batches/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: batchKeys.all });
    },
  });
}

// ---------------------------------------------------------------------------
// Upload
// ---------------------------------------------------------------------------
export type UploadPhase = "pending" | "uploading" | "done" | "error";

export interface UploadItem {
  id: string;
  file: File;
  phase: UploadPhase;
  progress: number;
  result?: UploadedFile;
  error?: string;
}

function readCsrfCookie(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp(`(?:^|; )${CSRF_COOKIE}=([^;]*)`));
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}

/**
 * Upload one group of files with real progress.
 *
 * `fetch` cannot report upload progress, so this uses XMLHttpRequest, whose
 * `upload.onprogress` is the only browser API that does. Files are sent in small
 * groups rather than one giant request so that progress is granular and a
 * failure costs one group, not the whole batch.
 */
function uploadGroup(
  batchId: string,
  files: File[],
  onProgress: (fraction: number) => void,
  signal: AbortSignal,
): Promise<UploadedFile[]> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    for (const file of files) form.append("files", file, file.name);

    const request = new XMLHttpRequest();
    request.open("POST", `${apiBaseUrl()}/api/v1/batches/${batchId}/files`);
    request.withCredentials = true;

    const csrf = readCsrfCookie();
    if (csrf) request.setRequestHeader(CSRF_HEADER, csrf);

    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };

    request.onload = () => {
      let payload: unknown;
      try {
        payload = JSON.parse(request.responseText);
      } catch {
        reject(new Error("The server returned a malformed response."));
        return;
      }

      if (request.status >= 200 && request.status < 300) {
        const parsed = uploadResultSchema.safeParse(payload);
        if (!parsed.success) {
          reject(new Error("The upload response did not match the expected shape."));
          return;
        }
        onProgress(1);
        resolve(parsed.data);
        return;
      }

      const problem = payload as { detail?: string; title?: string; remediation?: string };
      reject(
        new Error(
          [problem.detail ?? problem.title ?? `Upload failed (${request.status})`, problem.remediation]
            .filter(Boolean)
            .join(" "),
        ),
      );
    };

    request.onerror = () => reject(new Error("The network dropped during upload."));
    request.ontimeout = () => reject(new Error("The upload timed out."));
    request.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));

    signal.addEventListener("abort", () => request.abort(), { once: true });
    request.send(form);
  });
}

/** Files per request. Small enough for granular progress, large enough to avoid
 * a request per file on a 2,000-file batch. */
const GROUP_SIZE = 5;

export function useBatchUpload(batchId: string | null) {
  const queryClient = useQueryClient();
  const [items, setItems] = React.useState<UploadItem[]>([]);
  const [isUploading, setIsUploading] = React.useState(false);
  const abortRef = React.useRef<AbortController | null>(null);

  const addFiles = React.useCallback((files: File[]) => {
    setItems((current) => [
      ...current,
      ...files.map((file) => ({
        id: `${file.name}:${file.size}:${file.lastModified}`,
        file,
        phase: "pending" as UploadPhase,
        progress: 0,
      })),
    ]);
  }, []);

  const removeFile = React.useCallback((id: string) => {
    setItems((current) => current.filter((item) => item.id !== id));
  }, []);

  const clear = React.useCallback(() => setItems([]), []);

  const cancel = React.useCallback(() => {
    abortRef.current?.abort();
    setIsUploading(false);
  }, []);

  const start = React.useCallback(async (): Promise<void> => {
    if (!batchId || items.length === 0) return;

    const controller = new AbortController();
    abortRef.current = controller;
    setIsUploading(true);

    try {
      const pending = items.filter((item) => item.phase !== "done");
      for (let index = 0; index < pending.length; index += GROUP_SIZE) {
        const group = pending.slice(index, index + GROUP_SIZE);
        const ids = new Set(group.map((item) => item.id));

        setItems((current) =>
          current.map((item) =>
            ids.has(item.id) ? { ...item, phase: "uploading", progress: 0 } : item,
          ),
        );

        try {
          const results = await uploadGroup(
            batchId,
            group.map((item) => item.file),
            (fraction) => {
              setItems((current) =>
                current.map((item) =>
                  ids.has(item.id) ? { ...item, progress: fraction } : item,
                ),
              );
            },
            controller.signal,
          );

          setItems((current) =>
            current.map((item) => {
              if (!ids.has(item.id)) return item;
              const position = group.findIndex((entry) => entry.id === item.id);
              const result = results[position];
              return {
                ...item,
                phase: result?.status === "FAILED" ? "error" : "done",
                progress: 1,
                result,
                error: result?.status === "FAILED" ? "Rejected by the server." : undefined,
              };
            }),
          );
        } catch (error) {
          if (error instanceof DOMException && error.name === "AbortError") throw error;
          const message =
            error instanceof ApiError
              ? error.userMessage
              : error instanceof Error
                ? error.message
                : "Upload failed.";
          setItems((current) =>
            current.map((item) =>
              ids.has(item.id) ? { ...item, phase: "error", error: message } : item,
            ),
          );
        }
      }
    } finally {
      setIsUploading(false);
      abortRef.current = null;
      void queryClient.invalidateQueries({ queryKey: batchKeys.all });
    }
  }, [batchId, items, queryClient]);

  const stats = React.useMemo(() => {
    const done = items.filter((item) => item.phase === "done");
    return {
      total: items.length,
      done: done.length,
      failed: items.filter((item) => item.phase === "error").length,
      duplicates: done.filter((item) => item.result?.is_duplicate).length,
      bytes: items.reduce((sum, item) => sum + item.file.size, 0),
      units: done.reduce((sum, item) => sum + 1 + (item.result?.children.length ?? 0), 0),
    };
  }, [items]);

  return { items, addFiles, removeFile, clear, start, cancel, isUploading, stats };
}
