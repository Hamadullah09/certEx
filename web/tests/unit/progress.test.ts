import { describe, expect, it } from "vitest";

import { parseProgressEvent, snapshotFromBatch } from "@/lib/schemas/progress";
import type { BatchSummary } from "@/lib/schemas/batches";

function makeBatch(overrides: Partial<BatchSummary> = {}): BatchSummary {
  return {
    id: "44444444-4444-4444-8444-444444444444",
    name: "Births 1998",
    status: "PROCESSING",
    file_count: 8,
    unit_count: 12,
    processed_count: 3,
    failed_count: 1,
    duplicate_count: 0,
    total_bytes: 1024,
    created_at: "2026-01-01T00:00:00.000Z",
    ...overrides,
  };
}

const EVENT = JSON.stringify({
  batch_id: "44444444-4444-4444-8444-444444444444",
  status: "PROCESSING",
  file_count: 8,
  processed_count: 3,
  failed_count: 1,
  duplicate_count: 0,
  unit_count: 12,
  percent: 50,
  finished: false,
});

describe("parseProgressEvent", () => {
  it("parses a progress event from the stream", () => {
    const snapshot = parseProgressEvent(EVENT);
    expect(snapshot?.percent).toBe(50);
    expect(snapshot?.finished).toBe(false);
    expect(snapshot?.status).toBe("PROCESSING");
  });

  it("returns null rather than throwing on a payload it does not recognise", () => {
    // A stream is not a place to throw: one bad frame must not take the page down.
    expect(parseProgressEvent("not json")).toBeNull();
    expect(parseProgressEvent("{}")).toBeNull();
    expect(parseProgressEvent(JSON.stringify({ status: "NOT_A_STATUS" }))).toBeNull();
  });

  it("ignores the heartbeat's empty payload", () => {
    expect(parseProgressEvent("{}")).toBeNull();
  });
});

describe("snapshotFromBatch", () => {
  it("computes the same percentage the stream sends, so the bar does not jump", () => {
    const snapshot = snapshotFromBatch(makeBatch());
    expect(snapshot.percent).toBe(50);
    expect(parseProgressEvent(EVENT)?.percent).toBe(snapshot.percent);
  });

  it("counts duplicates and failures as dealt with", () => {
    expect(snapshotFromBatch(makeBatch({ processed_count: 0, failed_count: 4, duplicate_count: 4 })).percent).toBe(100);
  });

  it("rounds to one decimal, as the server does", () => {
    expect(
      snapshotFromBatch(makeBatch({ file_count: 3, processed_count: 1, failed_count: 0 })).percent,
    ).toBe(33.3);
  });

  it("is zero, not NaN, for a batch with no files", () => {
    expect(
      snapshotFromBatch(makeBatch({ file_count: 0, processed_count: 0, failed_count: 0 })).percent,
    ).toBe(0);
  });

  it("is finished for every terminal status and unfinished for the rest", () => {
    expect(snapshotFromBatch(makeBatch({ status: "COMPLETED" })).finished).toBe(true);
    expect(snapshotFromBatch(makeBatch({ status: "COMPLETED_WITH_ERRORS" })).finished).toBe(true);
    expect(snapshotFromBatch(makeBatch({ status: "FAILED" })).finished).toBe(true);
    expect(snapshotFromBatch(makeBatch({ status: "CANCELLED" })).finished).toBe(true);
    expect(snapshotFromBatch(makeBatch({ status: "QUEUED" })).finished).toBe(false);
    expect(snapshotFromBatch(makeBatch({ status: "UPLOADING" })).finished).toBe(false);
  });
});
