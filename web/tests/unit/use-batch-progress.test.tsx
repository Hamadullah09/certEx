import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useBatchProgress } from "@/hooks/use-batch-progress";

const BATCH_ID = "11111111-1111-4111-8111-111111111111";

function snapshot(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    batch_id: BATCH_ID,
    status: "PROCESSING",
    file_count: 4,
    processed_count: 1,
    failed_count: 0,
    duplicate_count: 0,
    unit_count: 2,
    percent: 25,
    finished: false,
    ...overrides,
  });
}

/**
 * Stands in for the browser's EventSource at the network boundary, so the parts
 * that cannot be observed from the outside - that the stream is closed rather than
 * left to reconnect, and when the hook gives up and asks for polling - can be.
 */
class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly listeners = new Map<string, ((event: Event) => void)[]>();
  closed = false;

  constructor(
    readonly url: string,
    readonly init?: { withCredentials?: boolean },
  ) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, handler: (event: Event) => void): void {
    const existing = this.listeners.get(type) ?? [];
    existing.push(handler);
    this.listeners.set(type, existing);
  }

  close(): void {
    this.closed = true;
  }

  emit(type: string, data = "{}"): void {
    for (const handler of this.listeners.get(type) ?? []) {
      handler(new MessageEvent(type, { data }));
    }
  }
}

const originalEventSource = globalThis.EventSource;
const originalFetch = globalThis.fetch;

beforeEach(() => {
  FakeEventSource.instances = [];
  globalThis.EventSource = FakeEventSource as unknown as typeof EventSource;
  // The one refresh the hook attempts before giving up on the stream.
  globalThis.fetch = (async () => new Response(null, { status: 200 })) as typeof fetch;
});

afterEach(() => {
  globalThis.EventSource = originalEventSource;
  globalThis.fetch = originalFetch;
});

function latest(): FakeEventSource {
  const instance = FakeEventSource.instances.at(-1);
  if (!instance) throw new Error("no stream was opened");
  return instance;
}

describe("useBatchProgress", () => {
  it("opens the batch's progress stream with the session cookie", () => {
    renderHook(() => useBatchProgress(BATCH_ID));
    expect(latest().url).toBe(`http://localhost:8000/api/v1/batches/${BATCH_ID}/progress`);
    expect(latest().init?.withCredentials).toBe(true);
  });

  it("reports the snapshot from a progress event", () => {
    const { result } = renderHook(() => useBatchProgress(BATCH_ID));
    act(() => latest().emit("progress", snapshot()));

    expect(result.current.snapshot?.percent).toBe(25);
    expect(result.current.transport).toBe("stream");
    expect(result.current.lastEventAt).not.toBeNull();
  });

  it("ignores a malformed frame instead of tearing the page down", () => {
    const { result } = renderHook(() => useBatchProgress(BATCH_ID));
    act(() => latest().emit("progress", "not json"));
    expect(result.current.snapshot).toBeNull();
    expect(latest().closed).toBe(false);
  });

  it("notes a heartbeat without changing the numbers", () => {
    const { result } = renderHook(() => useBatchProgress(BATCH_ID));
    act(() => latest().emit("progress", snapshot()));
    const before = result.current.lastEventAt;
    act(() => latest().emit("heartbeat"));

    expect(result.current.snapshot?.percent).toBe(25);
    expect(result.current.lastEventAt).not.toBeNull();
    expect(result.current.lastEventAt).toBeGreaterThanOrEqual(before ?? 0);
  });

  it("closes the stream when the batch finishes, rather than letting it reconnect", () => {
    const { result } = renderHook(() => useBatchProgress(BATCH_ID));
    act(() => latest().emit("progress", snapshot({ status: "COMPLETED", percent: 100, finished: true })));

    expect(latest().closed).toBe(true);
    expect(result.current.transport).toBe("ended");
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("stops and says so when the batch is deleted underneath it", () => {
    const { result } = renderHook(() => useBatchProgress(BATCH_ID));
    act(() => latest().emit("gone", JSON.stringify({ batch_id: BATCH_ID })));

    expect(result.current.isGone).toBe(true);
    expect(result.current.transport).toBe("ended");
    expect(latest().closed).toBe(true);
  });

  it("refreshes the session and reopens the stream once, then falls back to polling", async () => {
    const { result } = renderHook(() => useBatchProgress(BATCH_ID));
    const first = latest();
    act(() => first.emit("error"));

    // The browser would reconnect on its own with the same stale cookie, so the hook
    // closes the stream and reopens it itself after rotating the session.
    expect(first.closed).toBe(true);
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2));

    act(() => latest().emit("error"));
    await waitFor(() => expect(result.current.transport).toBe("polling"));
    expect(FakeEventSource.instances).toHaveLength(2);
  });

  it("asks for polling outright where there is no EventSource at all", () => {
    const withoutStreams = globalThis.EventSource;
    // @ts-expect-error - deleting the global is how the absence is simulated.
    delete globalThis.EventSource;
    try {
      const { result } = renderHook(() => useBatchProgress(BATCH_ID));
      expect(result.current.transport).toBe("polling");
    } finally {
      globalThis.EventSource = withoutStreams;
    }
  });

  it("opens nothing when there is no batch to watch", () => {
    const { result } = renderHook(() => useBatchProgress(""));
    expect(FakeEventSource.instances).toHaveLength(0);
    expect(result.current.transport).toBe("ended");
  });

  it("closes the stream when the page goes away", () => {
    const { unmount } = renderHook(() => useBatchProgress(BATCH_ID));
    const stream = latest();
    unmount();
    expect(stream.closed).toBe(true);
  });
});
