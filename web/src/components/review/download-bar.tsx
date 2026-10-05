"use client";

import * as React from "react";
import { AlertTriangle, Download, FileSpreadsheet } from "lucide-react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useDownloadExport, useExportPreview } from "@/hooks/use-exports";
import { ApiError } from "@/lib/api";
import type { RowFilters } from "@/lib/rows-query";
import { EXPORT_FORMATS, type ExportFormat } from "@/lib/schemas/exports";

/*
 * The whole point of the system is the file at the end of it, so this sits on the
 * screen where the checking happens rather than behind a menu somewhere else.
 *
 * Three things a person needs before they download: how many certificates are in the
 * file, whether any of them are still unchecked, and which filters are being applied.
 * All three are stated. The format choice is three plain buttons rather than a
 * dropdown, because a dropdown hides two of the three options behind a click.
 */
function describeFilters(filters: RowFilters): string | null {
  const parts: string[] = [];
  if (filters.status === "MANUALLY_APPROVED") parts.push("only the approved ones");
  else if (filters.status === "NEEDS_REVIEW") parts.push("only the ones waiting to be checked");
  else if (filters.status) parts.push(`only rows marked ${filters.status.toLowerCase()}`);
  if (filters.type) parts.push(`only ${filters.type.toLowerCase()} certificates`);
  if (filters.flag) parts.push(`only those that failed the ${filters.flag} check`);
  if (filters.search) parts.push(`only those matching "${filters.search}"`);
  return parts.length ? parts.join(", ") : null;
}

export function DownloadBar({
  batchId,
  batchName,
  filters,
}: {
  batchId: string;
  batchName: string;
  filters: RowFilters;
}) {
  const [format, setFormat] = React.useState<ExportFormat>("xlsx");
  const preview = useExportPreview(batchId, filters);
  const download = useDownloadExport();

  const narrowed = describeFilters(filters);
  const unchecked = preview.data?.unreviewed_count ?? 0;
  const nothingToSend = preview.data?.row_count === 0;

  return (
    <Card className="mt-8 border-2 border-primary-border/50">
      <CardHeader className="pb-4">
        <CardTitle className="flex items-center gap-2">
          <FileSpreadsheet aria-hidden="true" className="size-6 text-primary" />
          Download the results
        </CardTitle>
        <CardDescription>
          Everything that was read from this batch, as one file you can open or send on.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-5">
        {preview.isPending ? (
          <Skeleton className="h-6 w-72" />
        ) : preview.isError ? (
          <Alert variant="destructive">
            <AlertTriangle aria-hidden="true" />
            <AlertTitle>The download could not be prepared</AlertTitle>
            <AlertDescription>
              {preview.error instanceof ApiError
                ? preview.error.userMessage
                : "Try again in a moment."}
            </AlertDescription>
          </Alert>
        ) : preview.data ? (
          <p className="text-lg">
            <span className="font-bold tabular-nums">
              {preview.data.row_count.toLocaleString()}
            </span>{" "}
            {preview.data.row_count === 1 ? "certificate" : "certificates"} in{" "}
            <span className="font-bold tabular-nums">{preview.data.column_count}</span> columns
            {narrowed ? (
              <span className="text-muted-foreground"> — {narrowed}</span>
            ) : null}
          </p>
        ) : null}

        {unchecked > 0 ? (
          <Alert variant="warning">
            <AlertTriangle aria-hidden="true" />
            <AlertTitle>
              {unchecked.toLocaleString()}{" "}
              {unchecked === 1 ? "certificate has" : "certificates have"} not been checked by
              anybody
            </AlertTitle>
            <AlertDescription>
              They will be in the file. The app read them on its own, and nothing it reads is
              certain. Check them first if this file is going to somebody else.
            </AlertDescription>
          </Alert>
        ) : null}

        <fieldset>
          <legend className="mb-2 text-base font-semibold">What kind of file</legend>
          <div className="flex flex-wrap gap-3">
            {EXPORT_FORMATS.map((option) => (
              <button
                key={option.value}
                type="button"
                aria-pressed={format === option.value}
                onClick={() => setFormat(option.value)}
                className={
                  format === option.value
                    ? "min-h-14 rounded-lg border-2 border-primary bg-primary-surface px-5 py-2 text-left"
                    : "min-h-14 rounded-lg border-2 border-input bg-card px-5 py-2 text-left hover:border-primary"
                }
              >
                <span className="block text-base font-bold">{option.label}</span>
                <span className="block text-sm text-muted-foreground">{option.detail}</span>
              </button>
            ))}
          </div>
        </fieldset>

        <div className="flex flex-wrap items-center gap-4">
          <Button
            size="lg"
            disabled={download.isPending || preview.isPending || nothingToSend}
            onClick={() =>
              download.mutate(
                {
                  batchId,
                  format,
                  filters,
                  fallbackName: `${batchName}.${format}`,
                },
                {
                  onSuccess: (filename) => toast.success(`Saved ${filename}`),
                  onError: (error) =>
                    toast.error(
                      error instanceof ApiError
                        ? error.userMessage
                        : "The file could not be downloaded.",
                    ),
                },
              )
            }
          >
            <Download aria-hidden="true" />
            {download.isPending ? "Preparing the file…" : "Download"}
          </Button>
          {nothingToSend ? (
            <p className="text-base text-muted-foreground">
              Nothing matches the filters above, so there is nothing to download.
            </p>
          ) : preview.data ? (
            <p className="text-base text-muted-foreground">
              Saved as{" "}
              <span className="font-mono font-semibold">
                {preview.data.filename.replace(/\.csv$/, `.${format}`)}
              </span>
            </p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
