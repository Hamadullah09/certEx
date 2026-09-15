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
  QUEUED: { label: "Queued", variant: "default", Icon: Clock },
  PROCESSING: { label: "Processing", variant: "default", Icon: Loader2, spin: true },
  COMPLETED: { label: "Completed", variant: "success", Icon: CheckCircle2 },
  COMPLETED_WITH_ERRORS: {
    label: "Completed with errors",
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
      <Icon aria-hidden="true" className={spin ? "size-3 animate-spin" : "size-3"} />
      {label}
    </Badge>
  );
}

const DOCUMENT_PRESENTATION: Record<
  DocumentStatus,
  { label: string; variant: BadgeProps["variant"] }
> = {
  QUEUED: { label: "Queued", variant: "outline" },
  INGESTED: { label: "Ingested", variant: "outline" },
  NORMALIZING: { label: "Normalising", variant: "default" },
  SPLITTING: { label: "Splitting", variant: "default" },
  EXTRACTING_TEXT: { label: "Reading text", variant: "default" },
  OCR: { label: "OCR", variant: "default" },
  CLASSIFYING: { label: "Classifying", variant: "default" },
  EXTRACTING_FIELDS: { label: "Extracting", variant: "default" },
  VALIDATING: { label: "Validating", variant: "default" },
  COMPLETED: { label: "Completed", variant: "success" },
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
