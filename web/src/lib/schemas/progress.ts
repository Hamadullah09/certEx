import { z } from "zod";

import { type BatchSummary, batchStatusSchema } from "@/lib/schemas/batches";

/**
 * The payload of a `progress` server-sent event, and the same shape derived from
 * a polled batch.
 *
 * Both transports produce one snapshot type so the processing view renders the
 * identical bar whether the stream is up or the client fell back to polling - the
 * fallback must not look like a different feature.
 */
export const progressSnapshotSchema = z.object({
  batch_id: z.string().uuid().nullish(),
  status: batchStatusSchema,
  file_count: z.number().int(),
  processed_count: z.number().int(),
  failed_count: z.number().int(),
  duplicate_count: z.number().int(),
  unit_count: z.number().int(),
  /** Computed server-side so every watcher agrees on the number. */
  percent: z.number(),
  finished: z.boolean(),
});
export type ProgressSnapshot = z.infer<typeof progressSnapshotSchema>;

/** Parse one SSE `data` payload, returning null rather than throwing on junk. */
export function parseProgressEvent(data: string): ProgressSnapshot | null {
  let payload: unknown;
  try {
    payload = JSON.parse(data);
  } catch {
    return null;
  }
  const parsed = progressSnapshotSchema.safeParse(payload);
  return parsed.success ? parsed.data : null;
}

const TERMINAL_STATUSES = new Set(["COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"]);

/**
 * The same snapshot from a polled batch.
 *
 * `percent` is recomputed here with the server's formula - processed plus failed
 * plus duplicate over the file count - so the bar does not jump when the stream
 * drops and polling takes over.
 */
export function snapshotFromBatch(batch: BatchSummary): ProgressSnapshot {
  const total = batch.file_count || 0;
  const done = batch.processed_count + batch.failed_count + batch.duplicate_count;
  return {
    batch_id: batch.id,
    status: batch.status,
    file_count: total,
    processed_count: batch.processed_count,
    failed_count: batch.failed_count,
    duplicate_count: batch.duplicate_count,
    unit_count: batch.unit_count,
    percent: total > 0 ? Math.round((1000 * done) / total) / 10 : 0,
    finished: TERMINAL_STATUSES.has(batch.status),
  };
}
