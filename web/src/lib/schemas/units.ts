import { z } from "zod";

import { certificateTypeSchema, pageSchema } from "@/lib/schemas/batches";

/** Where a certificate's page range came from. */
export const boundaryMethodSchema = z.enum([
  "single_document",
  "bookmark",
  "content_header",
  "serial_number",
  "fixed_stride",
  "uniform_page_count",
  "manual",
]);
export type BoundaryMethod = z.infer<typeof boundaryMethodSchema>;

export const unitStatusSchema = z.enum([
  "PENDING",
  "TEXT_READY",
  "CLASSIFIED",
  "EXTRACTED",
  "VALIDATED",
  "COMPLETED",
  "FAILED",
]);
export type UnitStatus = z.infer<typeof unitStatusSchema>;

export const unitSummarySchema = z.object({
  id: z.string().uuid(),
  document_id: z.string().uuid(),
  batch_id: z.string().uuid(),
  ordinal: z.number().int(),
  page_start: z.number().int(),
  page_end: z.number().int(),
  boundary_method: boundaryMethodSchema,
  boundary_confidence: z.number().min(0).max(1),
  certificate_type: certificateTypeSchema,
  type_confidence: z.number().min(0).max(1),
  // Left as a string: the server's method vocabulary grows with the pipeline, and
  // nothing in the review UI branches on it.
  classification_method: z.string(),
  status: unitStatusSchema,
  error_code: z.string().nullish(),
  error_message: z.string().nullish(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type UnitSummary = z.infer<typeof unitSummarySchema>;

export const unitPageSchema = pageSchema(unitSummarySchema);

/** A split returns both halves; a merge returns the single surviving unit. */
export const unitListSchema = z.array(unitSummarySchema);
