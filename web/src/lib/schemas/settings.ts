import { z } from "zod";

import type { BatchSettings } from "@/lib/schemas/batches";

/**
 * Workspace settings, as the settings screen edits them.
 *
 * There is no settings endpoint yet, so nothing here is written anywhere. The
 * shape deliberately uses the server's own names - `confidence_auto_approve`,
 * `confidence_review_floor`, `ocr_languages`, `retention_days` - so the form maps
 * onto `certex.config.Settings` one-to-one when a route appears.
 *
 * There are no model or LLM settings: this deployment reads certificates with
 * Tesseract and rules, and a switch for something that does not exist would be a
 * lie about what the software does.
 */
export const workspaceSettingsSchema = z
  .object({
    /** At or above this row confidence, a row is accepted without a reviewer. */
    confidence_auto_approve: z.number().min(0).max(1),
    /** Below this, a row is marked as failed rather than sent for review. */
    confidence_review_floor: z.number().min(0).max(1),
    /** Tesseract language codes, in the order they are given to the engine. */
    ocr_languages: z.array(z.string().min(2).max(8)).min(1),
    retention_days: z.number().int().min(1).max(3650),
  })
  .refine((value) => value.confidence_review_floor <= value.confidence_auto_approve, {
    // The same invariant the server checks on boot; catching it in the form means
    // a reviewer reads a sentence instead of a 422.
    message: "The review floor cannot be higher than the accept-automatically level.",
    path: ["confidence_review_floor"],
  });
export type WorkspaceSettings = z.infer<typeof workspaceSettingsSchema>;

/** The OCR languages this build ships with, in the words staff use for them. */
export const OCR_LANGUAGE_OPTIONS: readonly { value: string; label: string }[] = [
  { value: "eng", label: "English" },
  { value: "urd", label: "Urdu" },
];

/**
 * Split the server's `eng+urd` into codes.
 *
 * Codes the UI has no checkbox for are kept rather than dropped: a workspace
 * configured for Arabic must not silently lose it by someone opening this form.
 */
export function parseOcrLanguages(value: string | null | undefined): string[] {
  if (!value) return [];
  return value
    .split("+")
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
}

export function formatOcrLanguages(codes: readonly string[]): string {
  return codes.join("+");
}

/** Codes present in `codes` that the form has no checkbox for. */
export function unknownOcrLanguages(codes: readonly string[]): string[] {
  const known = new Set(OCR_LANGUAGE_OPTIONS.map((option) => option.value));
  return codes.filter((code) => !known.has(code));
}

/**
 * The parts of a workspace's configuration that are readable from a batch.
 *
 * A batch records the thresholds and languages it was created with, which is the
 * only place the API exposes them today. Retention is server-side only, so it is
 * absent here rather than guessed at.
 */
export function settingsFromBatchSettings(
  settings: BatchSettings | null | undefined,
): Partial<WorkspaceSettings> {
  if (!settings) return {};
  const languages = parseOcrLanguages(settings.ocr_languages);
  return {
    ...(typeof settings.confidence_auto_approve === "number"
      ? { confidence_auto_approve: settings.confidence_auto_approve }
      : {}),
    ...(typeof settings.confidence_review_floor === "number"
      ? { confidence_review_floor: settings.confidence_review_floor }
      : {}),
    ...(languages.length > 0 ? { ocr_languages: languages } : {}),
  };
}
