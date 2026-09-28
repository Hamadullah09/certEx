import { z } from "zod";

import { certificateTypeSchema, pageSchema } from "@/lib/schemas/batches";

/**
 * A learned extraction template, as the templates screen would list it.
 *
 * There is no `GET /templates` on the API yet. This schema mirrors the
 * `templates` table the pipeline already writes (name, layout fingerprint,
 * certificate type, rule set, hit count, active flag) so that when the route
 * lands the client parses it rather than being rewritten - and so the screen can
 * be honest in the meantime instead of showing invented rows.
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
