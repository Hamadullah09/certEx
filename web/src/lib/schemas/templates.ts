import { z } from "zod";

import { certificateTypeSchema, pageSchema } from "@/lib/schemas/batches";

/**
 * A learned extraction template, as the templates screen lists it.
 *
 * One row of the `templates` table the extraction stage writes: the form's name, its
 * layout fingerprint, the certificate type it belongs to, how many fields it knows
 * where to find, and how many certificates it has been applied to.
 */
export const templateSummarySchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  /** Stable hash of issuing authority, form number and layout anchors. */
  fingerprint: z.string(),
  certificate_type: certificateTypeSchema,
  /** How many fields this template knows where to find. */
  rule_count: z.number().int().nullish(),
  /** How many certificates it has been applied to. */
  hit_count: z.number().int(),
  is_active: z.boolean(),
  created_at: z.string(),
  updated_at: z.string().nullish(),
});
export type TemplateSummary = z.infer<typeof templateSummarySchema>;

export const templatePageSchema = pageSchema(templateSummarySchema);
export type TemplatePage = z.infer<typeof templatePageSchema>;
