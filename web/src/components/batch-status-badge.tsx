import { AlertTriangle, CheckCircle2, CircleSlash, Clock, Loader2, Upload } from "lucide-react";

import { Badge, type BadgeProps } from "@/components/ui/badge";
import type { BatchStatus, DocumentStatus } from "@/lib/schemas/batches";

/**
 * Status is never conveyed by colour alone: each badge carries an icon and a
 * word, so it still reads correctly in greyscale and to a screen reader.
 */
const BATCH_PRESENTATION: Record<
  BatchStatus,
  { label: string; variant: BadgeProps["variant"]; Icon: typeof Clock; spin?: boolean }
> = {
  CREATED: { label: "Draft", variant: "outline", Icon: Clock },
  UPLOADING: { label: "Uploading", variant: "default", Icon: Upload },
  QUEUED: { label: "Waiting", variant: "default", Icon: Clock },
  PROCESSING: { label: "Working", variant: "default", Icon: Loader2, spin: true },
  COMPLETED: { label: "Done", variant: "success", Icon: CheckCircle2 },
  COMPLETED_WITH_ERRORS: {
    label: "Done, some failed",
    variant: "warning",
    Icon: AlertTriangle,
  },
  FAILED: { label: "Failed", variant: "danger", Icon: AlertTriangle },
  CANCELLED: { label: "Cancelled", variant: "outline", Icon: CircleSlash },
};

export function BatchStatusBadge({ status }: { status: BatchStatus }) {
  const { label, variant, Icon, spin } = BATCH_PRESENTATION[status];
  return (
    <Badge variant={variant}>
      <Icon aria-hidden="true" className={spin ? "animate-spin" : undefined} />
      {label}
    </Badge>
  );
}

/*
 * Pipeline stage names as a clerk would say them. The keys are the server's
 * enum and are untouched; only the words a human reads have changed, because
 * "Normalising" and "OCR" describe the code rather than the work.
 */
const DOCUMENT_PRESENTATION: Record<
  DocumentStatus,
  { label: string; variant: BadgeProps["variant"] }
> = {
  QUEUED: { label: "Waiting", variant: "outline" },
  INGESTED: { label: "Received", variant: "outline" },
  NORMALIZING: { label: "Preparing", variant: "default" },
  SPLITTING: { label: "Separating pages", variant: "default" },
  EXTRACTING_TEXT: { label: "Reading text", variant: "default" },
  OCR: { label: "Reading scan", variant: "default" },
  CLASSIFYING: { label: "Finding type", variant: "default" },
  EXTRACTING_FIELDS: { label: "Reading fields", variant: "default" },
  VALIDATING: { label: "Checking", variant: "default" },
  COMPLETED: { label: "Done", variant: "success" },
  FAILED: { label: "Failed", variant: "danger" },
  DUPLICATE: { label: "Duplicate", variant: "warning" },
  SKIPPED: { label: "Archive", variant: "outline" },
};

export function DocumentStatusBadge({ status }: { status: DocumentStatus }) {
  const { label, variant } = DOCUMENT_PRESENTATION[status];
  return <Badge variant={variant}>{label}</Badge>;
}

export function isTerminalBatchStatus(status: BatchStatus): boolean {
  return (
    status === "COMPLETED" ||
    status === "COMPLETED_WITH_ERRORS" ||
    status === "FAILED" ||
    status === "CANCELLED"
  );
}
