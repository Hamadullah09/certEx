"use client";

import * as React from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  ApiError,
  apiBaseUrl,
  apiFetch,
  apiVoid,
  CSRF_COOKIE,
  CSRF_HEADER,
  NetworkError,
  refreshSession,
} from "@/lib/api";
import {
  type BatchDetail,
  type BatchSettings,
  type BatchStatus,
  type UploadedFile,
  batchDetailSchema,
  batchPageSchema,
  documentPageSchema,
  uploadResultSchema,
  uploadSessionStateSchema,
  uploadedFileSchema,
} from "@/lib/schemas/batches";
import {
  SMALL_FILE_GROUP_SIZE,
  clientFileId,
  isChunked,
  passwordsField,
  remainingChunks,
} from "@/lib/upload";

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

/**
 * Close a batch to uploads and queue its files for reading.
 *
 * Uploading a file does not start it; the processing view is where that decision
 * is made, so a batch left in draft is not a dead end.
 */
export function useStartBatch() {
  const queryClient = useQueryClient();
  return useMutation<BatchDetail, Error, string>({
    mutationFn: (id) =>
      apiFetch(`/api/v1/batches/${id}/start`, batchDetailSchema, { method: "POST" }),
    onSuccess: (batch) => {
      queryClient.setQueryData(batchKeys.detail(batch.id), batch);
      void queryClient.invalidateQueries({ queryKey: batchKeys.documents(batch.id) });
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
  /** Stable across a browser restart, so a large file resumes its upload. */
  id: string;
  file: File;
  phase: UploadPhase;
  progress: number;
  result?: UploadedFile | undefined;
  error?: string | undefined;
  errorCode?: string | null | undefined;
  remediation?: string | null | undefined;
  /** Password for an encrypted PDF. Sent once with the upload, never stored. */
  password?: string | undefined;
}

function readCsrfCookie(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp(`(?:^|; )${CSRF_COOKIE}=([^;]*)`));
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}

class UploadHttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "UploadHttpError";
  }
}

/**
 * Send one group of small files with byte-level progress.
 *
 * `fetch` cannot report upload progress, so this uses XMLHttpRequest, whose
 * `upload.onprogress` is the only browser API that does. Files go in small groups
 * rather than one giant request so progress is granular and a failure costs one
 * group, not the whole batch.
 */
function sendGroup(
  batchId: string,
  items: UploadItem[],
  onProgress: (fraction: number) => void,
  signal: AbortSignal,
): Promise<UploadedFile[]> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    for (const item of items) form.append("files", item.file, item.file.name);
    const passwords = passwordsField(
      items.map((item) => ({ name: item.file.name, password: item.password })),
    );
    if (passwords) form.append("passwords", passwords);

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
        reject(new UploadHttpError(request.status, "The server returned a malformed response."));
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
        new UploadHttpError(
          request.status,
          [problem.detail ?? problem.title ?? `Upload failed (${request.status})`, problem.remediation]
            .filter(Boolean)
            .join(" "),
        ),
      );
    };

    request.onerror = () => reject(new NetworkError(new Error("xhr error")));
    request.ontimeout = () => reject(new Error("The upload timed out."));
    request.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));

    signal.addEventListener("abort", () => request.abort(), { once: true });
    request.send(form);
  });
}

/** Retry a group once after refreshing an access token that expired mid-upload. */
async function uploadGroup(
  batchId: string,
  items: UploadItem[],
  onProgress: (fraction: number) => void,
  signal: AbortSignal,
): Promise<UploadedFile[]> {
  try {
    return await sendGroup(batchId, items, onProgress, signal);
  } catch (error) {
    if (error instanceof UploadHttpError && error.status === 401 && (await refreshSession())) {
      return sendGroup(batchId, items, onProgress, signal);
    }
    throw error;
  }
}

const CHUNK_ATTEMPTS = 4;

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("Upload cancelled", "AbortError"));
      },
      { once: true },
    );
  });
}

/**
 * Upload one large file through the resumable protocol.
 *
 * Announcing the file returns how much the server already holds, so a file
 * re-added after a crash continues from there. Transient failures - a dropped
 * connection, a 5xx - retry the same chunk with backoff; resending a chunk that
 * already arrived is harmless on the server.
 */
async function uploadChunked(
  batchId: string,
  item: UploadItem,
  onProgress: (fraction: number) => void,
  signal: AbortSignal,
): Promise<UploadedFile> {
  const base = `/api/v1/batches/${batchId}/uploads`;
  const state = await apiFetch(base, uploadSessionStateSchema, {
    method: "POST",
    body: {
      client_file_id: item.id,
      filename: item.file.name,
      size: item.file.size,
      content_type: item.file.type || null,
    },
    signal,
  });

  const total = item.file.size;
  onProgress(state.received_bytes / total);

  for (const range of remainingChunks(total, state.chunk_size, state.received_bytes)) {
    const chunk = item.file.slice(range.offset, range.end);
    for (let attempt = 1; ; attempt += 1) {
      try {
        const next = await apiFetch(
          `${base}/${state.upload_id}/chunks/${range.offset}`,
          uploadSessionStateSchema,
          { method: "PUT", body: chunk, signal },
        );
        onProgress(next.received_bytes / total);
        break;
      } catch (error) {
        const transient =
          error instanceof NetworkError || (error instanceof ApiError && error.isRetryable);
        if (!transient || attempt >= CHUNK_ATTEMPTS) throw error;
        await sleep(500 * 2 ** (attempt - 1), signal);
      }
    }
  }

  return apiFetch(`${base}/${state.upload_id}/complete`, uploadedFileSchema, {
    method: "POST",
    body: { password: item.password ?? null },
    signal,
  });
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.userMessage;
  if (error instanceof Error) return error.message;
  return "Upload failed.";
}

function applyResult(item: UploadItem, result: UploadedFile | undefined): UploadItem {
  const failed = result?.status === "FAILED";
  return {
    ...item,
    phase: failed ? "error" : "done",
    progress: 1,
    result,
    error: failed ? (result?.error_message ?? "Rejected by the server.") : undefined,
    errorCode: failed ? result?.error_code : undefined,
    remediation: failed ? result?.remediation : undefined,
  };
}

export function useBatchUpload(batchId: string | null) {
  const queryClient = useQueryClient();
  const [items, setItems] = React.useState<UploadItem[]>([]);
  const [isUploading, setIsUploading] = React.useState(false);
  const abortRef = React.useRef<AbortController | null>(null);

  // Refs, not closed-over state: an upload started in the same tick as the batch
  // was created must see that batch id and the current file list, not the values
  // from the render that created the click handler.
  const itemsRef = React.useRef<UploadItem[]>(items);
  const batchIdRef = React.useRef<string | null>(batchId);
  React.useEffect(() => {
    itemsRef.current = items;
  }, [items]);
  React.useEffect(() => {
    batchIdRef.current = batchId;
  }, [batchId]);

  const update = React.useCallback((ids: Set<string>, change: (item: UploadItem) => UploadItem) => {
    setItems((current) => {
      const next = current.map((item) => (ids.has(item.id) ? change(item) : item));
      itemsRef.current = next;
      return next;
    });
  }, []);

  const addFiles = React.useCallback((files: File[]) => {
    setItems((current) => {
      const known = new Set(current.map((item) => item.id));
      const additions: UploadItem[] = [];
      for (const file of files) {
        const id = clientFileId(file);
        if (known.has(id)) continue;
        known.add(id);
        additions.push({ id, file, phase: "pending", progress: 0 });
      }
      const next = [...current, ...additions];
      itemsRef.current = next;
      return next;
    });
  }, []);

  const removeFile = React.useCallback((id: string) => {
    setItems((current) => {
      const next = current.filter((item) => item.id !== id);
      itemsRef.current = next;
      return next;
    });
  }, []);

  const clear = React.useCallback(() => {
    itemsRef.current = [];
    setItems([]);
  }, []);

  const cancel = React.useCallback(() => {
    abortRef.current?.abort();
    setIsUploading(false);
  }, []);

  const uploadItems = React.useCallback(
    async (targetBatch: string, targets: UploadItem[], signal: AbortSignal) => {
      const small = targets.filter((item) => !isChunked(item.file.size));
      const large = targets.filter((item) => isChunked(item.file.size));

      for (let index = 0; index < small.length; index += SMALL_FILE_GROUP_SIZE) {
        const group = small.slice(index, index + SMALL_FILE_GROUP_SIZE);
        const ids = new Set(group.map((item) => item.id));
        update(ids, (item) => ({ ...item, phase: "uploading", progress: 0, error: undefined }));
        try {
          const results = await uploadGroup(
            targetBatch,
            group,
            (fraction) => update(ids, (item) => ({ ...item, progress: fraction })),
            signal,
          );
          setItems((current) => {
            const next = current.map((item) => {
              const position = group.findIndex((entry) => entry.id === item.id);
              return position === -1 ? item : applyResult(item, results[position]);
            });
            itemsRef.current = next;
            return next;
          });
        } catch (error) {
          if (error instanceof DOMException && error.name === "AbortError") throw error;
          const message = describeError(error);
          update(ids, (item) => ({ ...item, phase: "error", error: message }));
        }
      }

      for (const target of large) {
        const ids = new Set([target.id]);
        update(ids, (item) => ({ ...item, phase: "uploading", error: undefined }));
        try {
          const result = await uploadChunked(
            targetBatch,
            target,
            (fraction) => update(ids, (item) => ({ ...item, progress: fraction })),
            signal,
          );
          update(ids, (item) => applyResult(item, result));
        } catch (error) {
          if (error instanceof DOMException && error.name === "AbortError") throw error;
          const message = describeError(error);
          update(ids, (item) => ({ ...item, phase: "error", error: message }));
        }
      }
    },
    [update],
  );

  const run = React.useCallback(
    async (targetBatch: string, targets: UploadItem[]) => {
      if (targets.length === 0) return;
      const controller = new AbortController();
      abortRef.current = controller;
      setIsUploading(true);
      try {
        await uploadItems(targetBatch, targets, controller.signal);
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) throw error;
      } finally {
        setIsUploading(false);
        abortRef.current = null;
        void queryClient.invalidateQueries({ queryKey: batchKeys.all });
      }
    },
    [queryClient, uploadItems],
  );

  /**
   * Upload every file not yet done. Pass the batch id explicitly when the batch
   * was created in the same handler - state from that render is not visible yet.
   */
  const start = React.useCallback(
    async (batchIdOverride?: string): Promise<void> => {
      const targetBatch = batchIdOverride ?? batchIdRef.current;
      if (!targetBatch) return;
      const targets = itemsRef.current.filter((item) => item.phase !== "done");
      await run(targetBatch, targets);
    },
    [run],
  );

  /**
   * Retry an encrypted PDF with its password. The rejected copy is removed from
   * the batch first, so the batch does not keep a stale failure beside the good one.
   */
  const retryWithPassword = React.useCallback(
    async (id: string, password: string): Promise<void> => {
      const targetBatch = batchIdRef.current;
      const item = itemsRef.current.find((entry) => entry.id === id);
      if (!targetBatch || !item) return;

      const rejected = item.result?.status === "FAILED" ? item.result.document_id : null;
      if (rejected) {
        await apiVoid(`/api/v1/batches/${targetBatch}/documents/${rejected}`, {
          method: "DELETE",
        });
      }

      const retried: UploadItem = {
        ...item,
        password,
        phase: "pending",
        progress: 0,
        result: undefined,
        error: undefined,
        errorCode: undefined,
        remediation: undefined,
      };
      update(new Set([id]), () => retried);
      await run(targetBatch, [retried]);
    },
    [run, update],
  );

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

  return {
    items,
    addFiles,
    removeFile,
    clear,
    start,
    cancel,
    retryWithPassword,
    isUploading,
    stats,
  };
}
