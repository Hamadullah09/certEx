"use client";

import * as React from "react";
import { AlertTriangle, Check, RotateCcw, Scissors, SplitSquareHorizontal } from "lucide-react";
import { toast } from "sonner";

import { PagePreview } from "@/components/review/page-preview";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { useRow } from "@/hooks/use-rows";
import { useSplitUnit } from "@/hooks/use-units";
import { ApiError } from "@/lib/api";
import { CERTIFICATE_TYPE_LABEL } from "@/lib/schemas/batches";
import {
  type RowSummary,
  REVIEW_STATUS_LABEL,
  flagLabel,
  isApproved,
} from "@/lib/schemas/rows";
import { canSplitRow, splitPageOptions } from "@/lib/units";
import { valueTextAttributes } from "@/lib/text-direction";
import { cn, confidenceClass, describeConfidence, formatConfidence } from "@/lib/utils";

export interface RowDetailPaneProps {
  /** The row as the grid knows it, shown while the full row loads. */
  row: RowSummary | null;
  selectedFieldName: string | null;
  onSelectField: (fieldName: string) => void;
  canEdit: boolean;
  onApprove: (row: RowSummary) => void;
  onReprocess: (row: RowSummary) => void;
  isBusy: boolean;
}

function statusVariant(row: RowSummary): "success" | "warning" | "danger" {
  if (isApproved(row.review_status)) return "success";
  return row.review_status === "FAILED" ? "danger" : "warning";
}

/**
 * The pane beside the grid: the page image with the selected field boxed on it,
 * every field with its confidence and provenance, and the actions for one row.
 *
 * The grid's selected column and this pane's selected field are the same thing, so
 * choosing a field here moves the grid and arrowing across the grid moves the box.
 */
export function RowDetailPane({
  row,
  selectedFieldName,
  onSelectField,
  canEdit,
  onApprove,
  onReprocess,
  isBusy,
}: RowDetailPaneProps) {
  const detail = useRow(row?.id ?? null);
  const split = useSplitUnit();
  const [page, setPage] = React.useState<number | null>(null);
  const [splitAt, setSplitAt] = React.useState<string>("");

  const loaded = detail.data;
  const field = loaded?.values.find((value) => value.name === selectedFieldName) ?? null;

  // The preview follows the selected field onto its page; paging by hand still
  // works, and only moves again when a field on another page is chosen.
  const fieldPage = field?.page_number ?? null;
  React.useEffect(() => {
    if (fieldPage) setPage(fieldPage);
  }, [fieldPage]);

  React.useEffect(() => {
    setPage(null);
    setSplitAt("");
  }, [row?.id]);

  if (!row) {
    return (
      <Card className="h-full">
        <CardHeader>
          <CardTitle>No certificate chosen</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-base text-muted-foreground">
            Click a row, or use the arrow keys, to see the page it was read from.
          </p>
        </CardContent>
      </Card>
    );
  }

  const pages = loaded?.page_numbers ?? [row.page_start];
  const currentPage = page ?? pages[0] ?? row.page_start;
  const splitOptions = splitPageOptions(row);

  return (
    <Card className="flex h-full flex-col">
      <CardHeader className="gap-3 pb-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="flex items-baseline gap-2">
              <span className="tabular-nums">#{row.serial_no}</span>
              <span className="text-base font-semibold text-muted-foreground">
                {CERTIFICATE_TYPE_LABEL[row.certificate_type]}
              </span>
            </CardTitle>
            <p className="mt-1 truncate text-sm text-muted-foreground" title={row.file_name}>
              {row.file_name} · pages {row.page_start}
              {row.page_end > row.page_start ? `-${row.page_end}` : ""}
            </p>
          </div>
          <Badge variant={statusVariant(row)}>{REVIEW_STATUS_LABEL[row.review_status]}</Badge>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span
            className={cn(
              "rounded-md px-2.5 py-1 font-semibold tabular-nums",
              confidenceClass(row.row_confidence),
            )}
            title={`Whole row: ${describeConfidence(row.row_confidence)}`}
          >
            Row {formatConfidence(row.row_confidence)}
          </span>
          {row.ocr_used ? <Badge variant="outline">Read from a scan</Badge> : null}
          {row.detected_language ? (
            <Badge variant="outline">Language: {row.detected_language}</Badge>
          ) : null}
        </div>

        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            disabled={!canEdit || isBusy || isApproved(row.review_status)}
            onClick={() => onApprove(row)}
          >
            <Check aria-hidden="true" />
            Approve (a)
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={!canEdit || isBusy}
            onClick={() => onReprocess(row)}
          >
            <RotateCcw aria-hidden="true" />
            Read again
          </Button>
        </div>
      </CardHeader>

      <Separator tone="subtle" />

      <CardContent className="flex-1 space-y-5 overflow-y-auto pt-5">
        {row.flags.length > 0 ? (
          <Alert variant="warning">
            <AlertTriangle aria-hidden="true" />
            <AlertTitle>Checks to look at</AlertTitle>
            <AlertDescription>
              <ul className="ml-4 list-disc space-y-1">
                {row.flags.map((flag) => (
                  <li key={flag}>{flagLabel(flag)}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        ) : null}

        <PagePreview
          documentId={row.document_id}
          pages={pages}
          pageNumber={currentPage}
          onPageChange={setPage}
          bbox={field?.bbox ?? null}
          fieldLabel={field ? (field.label ?? selectedFieldName) : null}
          fileName={row.file_name}
        />

        <Separator tone="subtle" />

        <div>
          <h3 className="text-lg">Everything read from this certificate</h3>
          {detail.isPending ? (
            <div className="mt-3 space-y-2">
              {[0, 1, 2, 3, 4].map((index) => (
                <Skeleton key={index} className="h-12 w-full" />
              ))}
            </div>
          ) : detail.isError ? (
            <Alert variant="destructive" className="mt-3">
              <AlertTriangle aria-hidden="true" />
              <AlertTitle>This certificate could not be opened</AlertTitle>
              <AlertDescription>
                {detail.error instanceof ApiError
                  ? detail.error.userMessage
                  : "Try choosing the row again."}
              </AlertDescription>
            </Alert>
          ) : (
            <ul className="mt-3 divide-y divide-border-subtle">
              {(loaded?.values ?? []).map((value) => {
                const active = value.name === selectedFieldName;
                return (
                  <li key={value.name}>
                    <button
                      type="button"
                      onClick={() => onSelectField(value.name)}
                      aria-current={active}
                      className={cn(
                        "flex w-full flex-wrap items-baseline gap-x-3 gap-y-1 rounded-md px-2 py-3 text-left",
                        "hover:bg-accent hover:text-accent-foreground",
                        active && "bg-primary-surface text-primary-surface-foreground",
                      )}
                    >
                      <span className="w-full text-sm font-semibold text-muted-foreground sm:w-44 sm:shrink-0">
                        {value.label ?? value.name}
                      </span>
                      <span
                        {...valueTextAttributes(value.value)}
                        className={cn(
                          "min-w-0 flex-1 text-base [unicode-bidi:isolate]",
                          !value.value && "text-muted-foreground",
                        )}
                      >
                        {value.value ?? "—"}
                      </span>
                      <span
                        className={cn(
                          "shrink-0 rounded-md px-2 py-0.5 text-xs font-semibold tabular-nums",
                          confidenceClass(value.confidence),
                        )}
                        title={describeConfidence(value.confidence)}
                      >
                        {formatConfidence(value.confidence)}
                      </span>
                      {value.issues.length > 0 ? (
                        <span className="w-full text-sm font-semibold text-destructive-surface-foreground">
                          {value.issues.join(" ")}
                        </span>
                      ) : null}
                      {value.snippet ? (
                        <span className="w-full text-sm text-muted-foreground">
                          Read from: “{value.snippet}”
                          {value.method ? ` · ${value.method}` : ""}
                        </span>
                      ) : null}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {Object.keys(row.extra_fields).length > 0 ? (
          <div>
            <h3 className="text-lg">Other values found</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              These were printed on the certificate but are not columns of this type.
            </p>
            <dl className="mt-3 space-y-2">
              {Object.entries(row.extra_fields).map(([name, value]) => (
                <div key={name} className="flex flex-wrap gap-x-3">
                  <dt className="w-44 shrink-0 text-sm font-semibold text-muted-foreground">
                    {name}
                  </dt>
                  <dd {...valueTextAttributes(value)} className="min-w-0 flex-1 text-base">
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        ) : null}

        {canSplitRow(row) ? (
          <div className="rounded-lg border border-border-subtle p-4">
            <h3 className="flex items-center gap-2 text-lg">
              <SplitSquareHorizontal aria-hidden="true" className="size-5" />
              Two certificates in these pages?
            </h3>
            <p className="mt-1 text-sm text-muted-foreground">
              Say which page the second certificate starts on and this row becomes two.
            </p>
            <div className="mt-3 flex flex-wrap items-end gap-3">
              <div className="min-w-40">
                <Label htmlFor="split-at-page">Second certificate starts on page</Label>
                <Select
                  id="split-at-page"
                  className="mt-1.5"
                  value={splitAt}
                  disabled={!canEdit || split.isPending}
                  onChange={(event) => setSplitAt(event.target.value)}
                >
                  <option value="">Choose a page</option>
                  {splitOptions.map((option) => (
                    <option key={option} value={String(option)}>
                      Page {option}
                    </option>
                  ))}
                </Select>
              </div>
              <Button
                variant="outline"
                disabled={!canEdit || !splitAt || split.isPending}
                onClick={() => {
                  const atPage = Number(splitAt);
                  if (!Number.isFinite(atPage)) return;
                  split.mutate(
                    { unitId: row.unit_id, atPage },
                    {
                      onSuccess: () => {
                        toast.success("Split into two certificates. They are being read again.");
                        setSplitAt("");
                      },
                      onError: (error) =>
                        toast.error(
                          error instanceof ApiError
                            ? error.userMessage
                            : "The certificate could not be split.",
                        ),
                    },
                  );
                }}
              >
                <Scissors aria-hidden="true" />
                Split here
              </Button>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
