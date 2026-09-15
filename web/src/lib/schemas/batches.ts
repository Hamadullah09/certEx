import { z } from "zod";

/**
 * Client-side mirrors of the server's Pydantic models.
 *
 * Responses are parsed against these before they reach a component, so a
 * client/server divergence fails loudly at the boundary rather than rendering
 * `undefined` into a reviewer's certificate data.
 */

export const batchStatusSchema = z.enum([
  "CREATED",
  "UPLOADING",
  "QUEUED",
  "PROCESSING",
  "COMPLETED",
  "COMPLETED_WITH_ERRORS",
  "FAILED",
  "CANCELLED",
]);
export type BatchStatus = z.infer<typeof batchStatusSchema>;

export const documentStatusSchema = z.enum([
  "QUEUED",
  "INGESTED",
  "NORMALIZING",
  "SPLITTING",
  "EXTRACTING_TEXT",
  "OCR",
  "CLASSIFYING",
  "EXTRACTING_FIELDS",
  "VALIDATING",
  "COMPLETED",
  "FAILED",
  "DUPLICATE",
  "SKIPPED",
]);
export type DocumentStatus = z.infer<typeof documentStatusSchema>;

export const certificateTypeSchema = z.enum(["BIRTH", "MARRIAGE", "DEATH", "OTHER"]);
export type CertificateType = z.infer<typeof certificateTypeSchema>;

export const batchSettingsSchema = z.object({
  expected_types: z.array(certificateTypeSchema).nullish(),
  ocr_languages: z.string().nullish(),
  confidence_auto_approve: z.number().min(0).max(1).nullish(),
  confidence_review_floor: z.number().min(0).max(1).nullish(),
  llm_enabled: z.boolean().nullish(),
  allow_vision: z.boolean().default(false),
});
export type BatchSettings = z.infer<typeof batchSettingsSchema>;

export const batchSummarySchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  status: batchStatusSchema,
  file_count: z.number().int(),
  unit_count: z.number().int(),
  processed_count: z.number().int(),
  failed_count: z.number().int(),
  duplicate_count: z.number().int(),
  total_bytes: z.number().int(),
  created_at: z.string(),
  started_at: z.string().nullish(),
  completed_at: z.string().nullish(),
  created_by: z.string().uuid().nullish(),
});
export type BatchSummary = z.infer<typeof batchSummarySchema>;

export const batchDetailSchema = batchSummarySchema.extend({
  settings: batchSettingsSchema,
  error_message: z.string().nullish(),
});
export type BatchDetail = z.infer<typeof batchDetailSchema>;

export const documentSummarySchema = z.object({
  id: z.string().uuid(),
  original_filename: z.string(),
  mime_type: z.string(),
  byte_size: z.number().int(),
  sha256: z.string(),
  page_count: z.number().int().nullish(),
  status: documentStatusSchema,
  error_code: z.string().nullish(),
  error_message: z.string().nullish(),
  is_duplicate_of: z.string().uuid().nullish(),
  parent_document_id: z.string().uuid().nullish(),
  archive_member_path: z.string().nullish(),
  is_encrypted: z.boolean().default(false),
  created_at: z.string(),
});
export type DocumentSummary = z.infer<typeof documentSummarySchema>;

/** `children` is recursive: an archive yields documents that may be archives. */
export type UploadedFile = {
  document_id: string;
  original_filename: string;
  byte_size: number;
  sha256: string;
  mime_type: string;
  status: DocumentStatus;
  is_duplicate: boolean;
  duplicate_of?: string | null;
  extracted_from_archive: boolean;
  children: UploadedFile[];
};

export const uploadedFileSchema: z.ZodType<UploadedFile, z.ZodTypeDef, unknown> = z.lazy(() =>
  z.object({
    document_id: z.string().uuid(),
    original_filename: z.string(),
    byte_size: z.number().int(),
    sha256: z.string(),
    mime_type: z.string(),
    status: documentStatusSchema,
    is_duplicate: z.boolean().default(false),
    duplicate_of: z.string().uuid().nullish(),
    extracted_from_archive: z.boolean().default(false),
    children: z.array(uploadedFileSchema).default([]),
  }),
);

export const pageMetaSchema = z.object({
  next_cursor: z.string().nullish(),
  has_more: z.boolean(),
  limit: z.number().int(),
  total: z.number().int().nullish(),
});

export function pageSchema<T extends z.ZodTypeAny>(item: T) {
  return z.object({ items: z.array(item), meta: pageMetaSchema });
}

export const batchPageSchema = pageSchema(batchSummarySchema);
export const documentPageSchema = pageSchema(documentSummarySchema);
export const uploadResultSchema = z.array(uploadedFileSchema);

// ---------------------------------------------------------------------------
// Client-side upload validation
// ---------------------------------------------------------------------------

/**
 * Extensions offered in the file picker.
 *
 * Purely an affordance. The server decides what a file really is by reading its
 * bytes, so this list narrows the picker and catches obvious mistakes early - it
 * is not a security control and is not treated as one.
 */
export const ACCEPTED_EXTENSIONS = [
  ".pdf",
  ".docx",
  ".doc",
  ".jpg",
  ".jpeg",
  ".png",
  ".tif",
  ".tiff",
  ".bmp",
  ".webp",
  ".zip",
] as const;

export const DROPZONE_ACCEPT: Record<string, string[]> = {
  "application/pdf": [".pdf"],
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
  "application/msword": [".doc"],
  "image/jpeg": [".jpg", ".jpeg"],
  "image/png": [".png"],
  "image/tiff": [".tif", ".tiff"],
  "image/bmp": [".bmp"],
  "image/webp": [".webp"],
  "application/zip": [".zip"],
  "application/x-zip-compressed": [".zip"],
};

export const MAX_FILE_BYTES = 500 * 1024 * 1024;
export const MAX_BATCH_FILES = 2000;
export const MAX_BATCH_BYTES = 5 * 1024 * 1024 * 1024;

export type RejectionReason =
  | "too_large"
  | "unsupported_extension"
  | "empty"
  | "batch_full"
  | "batch_too_large";

export interface FileRejection {
  file: File;
  reason: RejectionReason;
  message: string;
}

export function describeRejection(reason: RejectionReason): string {
  switch (reason) {
    case "too_large":
      return "Larger than the 500 MB per-file limit.";
    case "unsupported_extension":
      return "Not a supported document type.";
    case "empty":
      return "The file is empty.";
    case "batch_full":
      return `A batch holds at most ${MAX_BATCH_FILES.toLocaleString()} files.`;
    case "batch_too_large":
      return "Adding this would take the batch over its 5 GB limit.";
  }
}

/** Screen files before upload. The server re-checks everything regardless. */
export function screenFiles(
  incoming: File[],
  existing: { file: File }[],
): { accepted: File[]; rejected: FileRejection[] } {
  const accepted: File[] = [];
  const rejected: FileRejection[] = [];

  const seen = new Set(existing.map((entry) => `${entry.file.name}:${entry.file.size}`));
  let count = existing.length;
  let bytes = existing.reduce((total, entry) => total + entry.file.size, 0);

  for (const file of incoming) {
    const key = `${file.name}:${file.size}`;
    if (seen.has(key)) continue;

    const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    let reason: RejectionReason | null = null;

    if (file.size === 0) reason = "empty";
    else if (file.size > MAX_FILE_BYTES) reason = "too_large";
    else if (!ACCEPTED_EXTENSIONS.includes(extension as (typeof ACCEPTED_EXTENSIONS)[number]))
      reason = "unsupported_extension";
    else if (count + 1 > MAX_BATCH_FILES) reason = "batch_full";
    else if (bytes + file.size > MAX_BATCH_BYTES) reason = "batch_too_large";

    if (reason) {
      rejected.push({ file, reason, message: describeRejection(reason) });
      continue;
    }

    seen.add(key);
    accepted.push(file);
    count += 1;
    bytes += file.size;
  }

  return { accepted, rejected };
}
