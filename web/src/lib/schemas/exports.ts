import { z } from "zod";

import { certificateTypeSchema } from "@/lib/schemas/batches";

/**
 * What a download would contain, asked for before the download is made.
 *
 * The counts are the point. A clerk about to hand a spreadsheet to somebody else
 * should be told how many certificates are in it and how many of those nobody has
 * checked, *before* the file exists - afterwards it is too late to be useful.
 */
export const exportPreviewSchema = z.object({
  batch_id: z.string().uuid(),
  row_count: z.number().int(),
  column_count: z.number().int(),
  /** Rows in this download that no person has checked or approved yet. */
  unreviewed_count: z.number().int(),
  certificate_types: z.array(certificateTypeSchema),
  columns: z.array(z.string()),
  filename: z.string(),
});
export type ExportPreview = z.infer<typeof exportPreviewSchema>;

export const exportFormatSchema = z.enum(["csv", "xlsx", "json"]);
export type ExportFormat = z.infer<typeof exportFormatSchema>;

/**
 * How each format is offered, in the order a records office wants them.
 *
 * Excel is first because it is what the file is opened in. The extension is part of
 * the name on screen: "Excel" and "CSV" mean nothing to somebody who has only ever
 * been told to send "the .xlsx".
 */
export const EXPORT_FORMATS: readonly {
  value: ExportFormat;
  label: string;
  detail: string;
}[] = [
  { value: "xlsx", label: "Excel (.xlsx)", detail: "Opens straight in Excel." },
  {
    value: "csv",
    label: "Spreadsheet (.csv)",
    detail: "A plain table that any program can read.",
  },
  { value: "json", label: "Data file (.json)", detail: "For loading into another system." },
];
