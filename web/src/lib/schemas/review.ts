import { z } from "zod";

/** What has happened to a register entry, one row of its history. */
export const revisionActionSchema = z.enum([
  "CREATED",
  "CORRECTED",
  "APPROVED",
  "VOIDED",
  "SUPERSEDED",
  "DUPLICATE_RESOLVED",
  "DOCUMENT_ATTACHED",
  "DOCUMENT_REPLACED",
]);
export type RevisionAction = z.infer<typeof revisionActionSchema>;

export const revisionSchema = z.object({
  id: z.string().uuid(),
  record_version: z.number().int(),
  action: revisionActionSchema,
  changed_fields: z.array(z.string()),
  note: z.string().nullish(),
  actor_id: z.string().uuid().nullish(),
  created_at: z.string(),
});
export type Revision = z.infer<typeof revisionSchema>;

export const revisionListSchema = z.array(revisionSchema);

export const reviewSummarySchema = z.object({
  needs_review: z.number().int(),
  suspected_duplicates: z.number().int(),
});
export type ReviewSummary = z.infer<typeof reviewSummarySchema>;

/**
 * Workspace review settings.
 *
 * The same cross-field rule the server enforces is enforced here too, so a form
 * catches it before a round trip - the server remains the authority.
 */
export const reviewSettingsSchema = z
  .object({
    confidence_auto_approve: z.number().min(0).max(1),
    confidence_review_floor: z.number().min(0).max(1),
    review_imported_records: z.boolean(),
    review_suspected_duplicates: z.boolean(),
  })
  .refine((value) => value.confidence_review_floor <= value.confidence_auto_approve, {
    message: "The review floor cannot be above the auto-approve threshold.",
    path: ["confidence_review_floor"],
  });
export type ReviewSettings = z.infer<typeof reviewSettingsSchema>;

export const workspaceSettingsSchema = z.object({ review: reviewSettingsSchema });
export type WorkspaceSettings = z.infer<typeof workspaceSettingsSchema>;

/** What each history action means, in a clerk's words rather than the enum's. */
export const REVISION_ACTION_LABEL: Record<RevisionAction, string> = {
  CREATED: "Filed",
  CORRECTED: "Corrected",
  APPROVED: "Accepted by a reviewer",
  VOIDED: "Cancelled by the office",
  SUPERSEDED: "Replaced by another entry",
  DUPLICATE_RESOLVED: "Duplicate question settled",
  DOCUMENT_ATTACHED: "Document attached",
  DOCUMENT_REPLACED: "Scan replaced",
};
