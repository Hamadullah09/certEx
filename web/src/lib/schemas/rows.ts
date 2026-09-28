import { z } from "zod";

import { certificateTypeSchema, pageSchema } from "@/lib/schemas/batches";

/**
 * Client-side mirrors of the server's row models - one row is one certificate.
 *
 * Same contract as the batch schemas: a response is parsed here before any
 * component sees it, so a client/server divergence fails at the boundary instead
 * of putting `undefined` in front of a reviewer as if it were a certificate value.
 */

export const reviewStatusSchema = z.enum([
  "AUTO_APPROVED",
  "NEEDS_REVIEW",
  "FAILED",
  "MANUALLY_APPROVED",
]);
export type ReviewStatus = z.infer<typeof reviewStatusSchema>;

/** Where a value sits on its page, as fractions of the page image. */
export const bboxSchema = z.object({
  x0: z.number(),
  y0: z.number(),
  x1: z.number(),
  y1: z.number(),
});
export type Bbox = z.infer<typeof bboxSchema>;

export const fieldValueSchema = z.object({
  name: z.string(),
  value: z.string().nullish(),
  confidence: z.number().min(0).max(1).nullish(),
  /** Which layer read it: template, rule or manual. */
  method: z.string().nullish(),
  page_number: z.number().int().nullish(),
  bbox: bboxSchema.nullish(),
  snippet: z.string().nullish(),
  label: z.string().nullish(),
  flags: z.array(z.string()).default([]),
  issues: z.array(z.string()).default([]),
});
export type FieldValue = z.infer<typeof fieldValueSchema>;

export const rowSummarySchema = z.object({
  id: z.string().uuid(),
  unit_id: z.string().uuid(),
  document_id: z.string().uuid(),
  // The grid keys off `id`, and the page already knows which batch it is showing,
  // so this one is accepted without being required.
  batch_id: z.string().uuid().nullish(),
  serial_no: z.number().int(),
  file_name: z.string(),
  page_start: z.number().int(),
  page_end: z.number().int(),
  certificate_type: certificateTypeSchema,
  review_status: reviewStatusSchema,
  row_confidence: z.number().min(0).max(1),
  flags: z.array(z.string()).default([]),
  fields: z.record(z.string().nullable()).default({}),
  field_confidences: z.record(z.number()).default({}),
  extra_fields: z.record(z.string()).default({}),
  ocr_used: z.boolean().default(false),
  detected_language: z.string().nullish(),
  reviewed_at: z.string().nullish(),
  updated_at: z.string(),
});
export type RowSummary = z.infer<typeof rowSummarySchema>;

export const rowDetailSchema = rowSummarySchema.extend({
  /** Every field of this certificate type, whether or not a value was found. */
  values: z.array(fieldValueSchema).default([]),
  issues: z.array(z.record(z.string())).default([]),
  page_numbers: z.array(z.number().int()).default([]),
});
export type RowDetail = z.infer<typeof rowDetailSchema>;

export const rowPageSchema = pageSchema(rowSummarySchema);
export type RowPage = z.infer<typeof rowPageSchema>;

/**
 * A reviewer's edits to one row.
 *
 * Only the fields being changed are sent, and a field set to `null` is being
 * cleared - which the server treats differently from not mentioning it at all.
 */
export interface RowCorrection {
  fields: Record<string, string | null>;
  approve?: boolean;
}

/** Review status as a clerk would say it, never as the enum spells it. */
export const REVIEW_STATUS_LABEL: Record<ReviewStatus, string> = {
  AUTO_APPROVED: "Accepted by the app",
  NEEDS_REVIEW: "Needs checking",
  FAILED: "Could not be read",
  MANUALLY_APPROVED: "Checked by a person",
};

export function isApproved(status: ReviewStatus): boolean {
  return status === "AUTO_APPROVED" || status === "MANUALLY_APPROVED";
}

/**
 * Validation flags in plainer words.
 *
 * Only the flags the server can raise today are listed; anything unlisted falls
 * back to its own name rather than being hidden, because a flag a reviewer cannot
 * see is a flag that never gets fixed.
 */
const FLAG_LABEL: Record<string, string> = {
  MISSING_REQUIRED: "A required field is empty",
  NO_FIELDS_EXTRACTED: "Nothing could be read",
  DATE_AMBIGUOUS: "Date could be read two ways",
  DATE_UNPARSEABLE: "Date not understood",
  DATE_IMPOSSIBLE: "Date cannot exist",
  DATE_IN_FUTURE: "Date is in the future",
  DOD_BEFORE_DOB: "Died before born",
  MARRIAGE_BEFORE_BIRTH: "Married before born",
  REGISTRATION_BEFORE_EVENT: "Registered before the event",
  AGE_INCONSISTENT: "Age does not match the dates",
  ISSUE_BEFORE_REGISTRATION: "Issued before registration",
  CNIC_INVALID: "CNIC is not valid",
  CNIC_CHECKSUM_FAILED: "CNIC check digit is wrong",
  ID_FORMAT_UNKNOWN: "ID number format not recognised",
  DUPLICATE_ID_IN_ROW: "The same ID appears twice",
  SEX_UNRECOGNISED: "Sex not recognised",
  VALUE_TRUNCATED: "Value looks cut off",
  LOW_OCR_CONFIDENCE: "Scan was hard to read",
  VALUE_UNVERIFIED: "Value not found in the page text",
  LOW_FIELD_CONFIDENCE: "Low confidence in a field",
  LOW_TYPE_CONFIDENCE: "Unsure which certificate this is",
  LOW_BOUNDARY_CONFIDENCE: "Unsure where this certificate starts",
};

export function flagLabel(flag: string): string {
  return FLAG_LABEL[flag] ?? flag;
}
