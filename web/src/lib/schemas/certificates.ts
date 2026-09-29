import { z } from "zod";

import { pageSchema } from "@/lib/schemas/batches";
import { fieldRoleSchema } from "@/lib/schemas/registry";

/**
 * Register entries, and what a search says about them.
 *
 * A summary is deliberately schema-independent: every field on it is filled by role
 * server-side, so one result list renders a birth, a marriage and a type invented this
 * morning. The detail carries `values` keyed by field name, and the schema version says
 * what those names mean.
 */

export const certificateStatusSchema = z.enum(["ACTIVE", "SUPERSEDED", "VOID"]);
export type CertificateStatus = z.infer<typeof certificateStatusSchema>;

export const duplicateStatusSchema = z.enum(["NONE", "SUSPECTED", "CONFIRMED", "DISTINCT"]);
export type DuplicateStatus = z.infer<typeof duplicateStatusSchema>;

export const certificateSourceSchema = z.enum(["EXTRACTION", "IMPORT", "MANUAL"]);
export type CertificateSource = z.infer<typeof certificateSourceSchema>;

export const documentLinkKindSchema = z.enum(["PRIMARY", "SUPPORTING", "SUPERSEDED"]);
export type DocumentLinkKind = z.infer<typeof documentLinkKindSchema>;

export const certificateSummarySchema = z.object({
  id: z.string().uuid(),
  certificate_type_id: z.string().uuid(),
  certificate_number: z.string(),
  registration_number: z.string().nullish(),
  primary_name: z.string().nullish(),
  secondary_name: z.string().nullish(),
  event_date: z.string().nullish(),
  event_date_role: fieldRoleSchema.nullish(),
  registration_date: z.string().nullish(),
  issue_date: z.string().nullish(),
  issuing_authority: z.string().nullish(),
  status: certificateStatusSchema,
  duplicate_status: duplicateStatusSchema,
  needs_review: z.boolean(),
  row_confidence: z.number(),
  source: certificateSourceSchema,
  created_at: z.string(),
});
export type CertificateSummary = z.infer<typeof certificateSummarySchema>;

export const certificatePageSchema = pageSchema(certificateSummarySchema);

export const namedValueSchema = z.object({
  role: fieldRoleSchema,
  field_name: z.string(),
  value: z.string(),
  position: z.number().int(),
});
export type NamedValue = z.infer<typeof namedValueSchema>;

export const typedDateSchema = z.object({
  role: fieldRoleSchema,
  field_name: z.string(),
  value: z.string(),
  position: z.number().int(),
});
export type TypedDate = z.infer<typeof typedDateSchema>;

export const documentLinkSchema = z.object({
  id: z.string().uuid(),
  document_id: z.string().uuid(),
  unit_id: z.string().uuid().nullish(),
  kind: documentLinkKindSchema,
  page_start: z.number().int().nullish(),
  page_end: z.number().int().nullish(),
  note: z.string().nullish(),
  created_at: z.string(),
});
export type DocumentLink = z.infer<typeof documentLinkSchema>;

/** Where one value came from. Loosely typed: the pipeline adds keys over time. */
export const provenanceSchema = z.record(
  z.string(),
  z.record(z.string(), z.union([z.string(), z.number(), z.null()])),
);

export const certificateDetailSchema = certificateSummarySchema.extend({
  schema_version_id: z.string().uuid().nullish(),
  record_version: z.number().int(),
  duplicate_of_id: z.string().uuid().nullish(),
  values: z.record(z.string(), z.string()),
  confidences: z.record(z.string(), z.number()),
  provenance: provenanceSchema,
  names: z.array(namedValueSchema),
  dates: z.array(typedDateSchema),
  documents: z.array(documentLinkSchema),
  updated_at: z.string(),
});
export type CertificateDetail = z.infer<typeof certificateDetailSchema>;

export const duplicateCandidateSchema = z.object({
  certificate_id: z.string().uuid(),
  certificate_number: z.string(),
  reason: z.string(),
  primary_name: z.string().nullish(),
  event_date: z.string().nullish(),
  same_type: z.boolean(),
});
export type DuplicateCandidate = z.infer<typeof duplicateCandidateSchema>;

export const duplicateCandidateListSchema = z.array(duplicateCandidateSchema);

/** Which tier of the search answered. The screen says something different for each. */
export const matchKindSchema = z.enum([
  "certificate_number",
  "number_prefix",
  "name",
  "similar_name",
  "filtered",
  "none",
]);
export type MatchKind = z.infer<typeof matchKindSchema>;

export const searchHitSchema = z.object({
  certificate: certificateSummarySchema,
  match: matchKindSchema,
  same_name_count: z.number().int(),
});
export type SearchHit = z.infer<typeof searchHitSchema>;

export const searchResponseSchema = z.object({
  items: z.array(searchHitSchema),
  match: matchKindSchema,
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
  has_more: z.boolean(),
});
export type SearchResponse = z.infer<typeof searchResponseSchema>;

/**
 * What to tell the person at the keyboard about how their answer was found.
 *
 * An exact number is an answer. A similar name is a suggestion, and saying so is the
 * difference between a clerk trusting the screen and a clerk double-checking the
 * paper register.
 */
export const MATCH_KIND_CAPTION: Record<MatchKind, string> = {
  certificate_number: "Matched the certificate number exactly.",
  number_prefix: "No exact match. These certificate numbers start with what you typed.",
  name: "No certificate with that number. These entries carry that name.",
  similar_name: "No exact match. These names are spelled similarly - check carefully.",
  filtered: "Everything matching these filters, newest first.",
  none: "Nothing matched.",
};
