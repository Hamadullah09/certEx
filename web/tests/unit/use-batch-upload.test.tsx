import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useBatchUpload } from "@/hooks/use-batches";

interface SentRequest {
  method: string;
  url: string;
  body: unknown;
}

/**
 * Stands in for the browser's XMLHttpRequest at the network boundary, recording
 * what the upload client sends and answering like the API does.
 */
class RecordingXhr {
  static sent: SentRequest[] = [];
  static nextStatus = "QUEUED";

  upload: { onprogress: ((event: ProgressEvent) => void) | null } = { onprogress: null };
  status = 0;
  responseText = "";
  withCredentials = false;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  onabort: (() => void) | null = null;
  private method = "";
  private url = "";

  open(method: string, url: string): void {
    this.method = method;
    this.url = url;
  }

  setRequestHeader(): void {}

  abort(): void {}

  send(body: unknown): void {
    RecordingXhr.sent.push({ method: this.method, url: this.url, body });
    const files = body instanceof FormData ? body.getAll("files") : [];
    this.status = 201;
    this.responseText = JSON.stringify(
      files.map((file, index) => ({
        document_id: `00000000-0000-4000-8000-00000000000${index + 1}`,
        original_filename: file instanceof File ? file.name : "document",
        byte_size: file instanceof File ? file.size : 0,
        sha256: "0".repeat(64),
        mime_type: "application/pdf",
        status: RecordingXhr.nextStatus,
        is_duplicate: false,
        extracted_from_archive: false,
        children: [],
        ...(RecordingXhr.nextStatus === "FAILED"
          ? {
              error_code: "document_encrypted",
              error_message: "This PDF is password protected and could not be opened.",
              remediation: "Upload the file again with its password entered next to it.",
            }
          : {}),
      })),
    );
    setTimeout(() => this.onload?.(), 0);
  }
}

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const originalXhr = globalThis.XMLHttpRequest;

beforeEach(() => {
  RecordingXhr.sent = [];
  RecordingXhr.nextStatus = "QUEUED";
  globalThis.XMLHttpRequest = RecordingXhr as unknown as typeof XMLHttpRequest;
});

afterEach(() => {
  globalThis.XMLHttpRequest = originalXhr;
});

describe("useBatchUpload", () => {
  it("uploads on the first click, with a batch created in the same handler", async () => {
    // The hook is rendered before any batch exists - exactly the state the upload
    // page is in when the user clicks "Upload" for the first time.
    const { result } = renderHook(() => useBatchUpload(null), { wrapper });

    act(() => {
      result.current.addFiles([new File(["%PDF-1.4"], "certificate.pdf", { type: "application/pdf" })]);
    });

    // `start` comes from the render where batchId was still null. Before the fix
    // this sent nothing at all.
    const startFromStaleRender = result.current.start;
    await act(async () => {
      await startFromStaleRender("11111111-1111-4111-8111-111111111111");
    });

    expect(RecordingXhr.sent).toHaveLength(1);
    expect(RecordingXhr.sent[0]?.url).toContain(
      "/api/v1/batches/11111111-1111-4111-8111-111111111111/files",
    );
    await waitFor(() => expect(result.current.items[0]?.phase).toBe("done"));
  });

  it("falls back to the current batch id when none is passed", async () => {
    const { result, rerender } = renderHook(({ id }) => useBatchUpload(id), {
      wrapper,
      initialProps: { id: null as string | null },
    });
    act(() => {
      result.current.addFiles([new File(["%PDF-1.4"], "a.pdf", { type: "application/pdf" })]);
    });
    rerender({ id: "22222222-2222-4222-8222-222222222222" });

    await act(async () => {
      await result.current.start();
    });
    expect(RecordingXhr.sent[0]?.url).toContain("22222222-2222-4222-8222-222222222222");
  });

  it("does not upload twice when the same file is added twice", () => {
    const { result } = renderHook(() => useBatchUpload(null), { wrapper });
    const file = new File(["%PDF-1.4"], "a.pdf", { type: "application/pdf", lastModified: 5 });
    act(() => {
      result.current.addFiles([file]);
      result.current.addFiles([file]);
    });
    expect(result.current.items).toHaveLength(1);
  });

  it("surfaces the server's reason and remediation for a rejected file", async () => {
    RecordingXhr.nextStatus = "FAILED";
    const { result } = renderHook(() => useBatchUpload(null), { wrapper });
    act(() => {
      result.current.addFiles([new File(["%PDF-1.4"], "locked.pdf", { type: "application/pdf" })]);
    });
    await act(async () => {
      await result.current.start("33333333-3333-4333-8333-333333333333");
    });

    await waitFor(() => expect(result.current.items[0]?.phase).toBe("error"));
    const item = result.current.items[0];
    expect(item?.errorCode).toBe("document_encrypted");
    expect(item?.remediation).toContain("password");
  });

  it("sends a supplied password with its own file only", async () => {
    RecordingXhr.nextStatus = "FAILED";
    const { result, rerender } = renderHook(({ id }) => useBatchUpload(id), {
      wrapper,
      initialProps: { id: null as string | null },
    });
    act(() => {
      result.current.addFiles([new File(["%PDF-1.4"], "locked.pdf", { type: "application/pdf" })]);
    });
    const batchId = "44444444-4444-4444-8444-444444444444";
    await act(async () => {
      await result.current.start(batchId);
    });
    rerender({ id: batchId });
    await waitFor(() => expect(result.current.items[0]?.phase).toBe("error"));

    RecordingXhr.nextStatus = "QUEUED";
    // The retry first removes the rejected copy; answer that DELETE like the API.
    const originalFetch = globalThis.fetch;
    const deletes: string[] = [];
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      deletes.push(`${init?.method ?? "GET"} ${String(input)}`);
      return new Response(null, { status: 204 });
    }) as typeof fetch;
    try {
      const id = result.current.items[0]?.id ?? "";
      await act(async () => {
        await result.current.retryWithPassword(id, "letmein");
      });
    } finally {
      globalThis.fetch = originalFetch;
    }

    expect(deletes).toEqual([
      `DELETE http://localhost:8000/api/v1/batches/${batchId}/documents/00000000-0000-4000-8000-000000000001`,
    ]);
    const retry = RecordingXhr.sent[1]?.body;
    expect(retry).toBeInstanceOf(FormData);
    expect(JSON.parse(String((retry as FormData).get("passwords")))).toEqual({
      "locked.pdf": "letmein",
    });
    await waitFor(() => expect(result.current.items[0]?.phase).toBe("done"));
  });
});
