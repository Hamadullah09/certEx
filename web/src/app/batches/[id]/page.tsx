"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  AlertTriangle,
  FileText,
  Play,
  Radio,
  RefreshCw,
  RotateCcw,
  Table2,
  Timer,
} from "lucide-react";
import { toast } from "sonner";

import { AppShell } from "@/components/app-shell";
import {
  BatchStatusBadge,
  DocumentStatusBadge,
  isTerminalBatchStatus,
} from "@/components/batch-status-badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { useBatch, useBatchDocuments, useStartBatch } from "@/hooks/use-batches";
import { useBatchProgress } from "@/hooks/use-batch-progress";
import { useBatchRows, useReprocessRows } from "@/hooks/use-rows";
import { useSession } from "@/hooks/use-session";
import { ApiError } from "@/lib/api";
import { flattenRowPages } from "@/lib/rows-optimistic";
import { roleSatisfies } from "@/lib/schemas/auth";
import type { DocumentSummary } from "@/lib/schemas/batches";
import { snapshotFromBatch } from "@/lib/schemas/progress";
import { cn, formatBytes } from "@/lib/utils";

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.userMessage;
  return error instanceof Error ? error.message : fallback;
}

function CountTile({ label, value, tone }: { label: string; value: number; tone?: "danger" }) {
  return (
    <div className="rounded-lg border border-border-subtle bg-muted/60 px-4 py-3">
      <p className="text-sm font-semibold text-muted-foreground">{label}</p>
      <p
        className={cn(
          "text-2xl font-bold tabular-nums",
          tone === "danger" && value > 0 ? "text-destructive" : "text-foreground",
        )}
      >
        {value.toLocaleString()}
      </p>
    </div>
  );
}

function FileRow({ document }: { document: DocumentSummary }) {
  return (
    <li className="flex flex-wrap items-start gap-x-4 gap-y-2 px-6 py-4">
      <FileText aria-hidden="true" className="mt-1 size-5 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-base font-semibold text-foreground" title={document.original_filename}>
          {document.original_filename}
        </p>
        <p className="mt-0.5 text-sm text-muted-foreground">
          {formatBytes(document.byte_size)}
          {document.page_count ? ` · ${document.page_count} pages` : ""}
          {document.archive_member_path ? ` · from an archive` : ""}
        </p>
        {/* The server's own words for what went wrong and what to do about it -
            never a message invented here, which would be a guess. */}
        {document.status === "FAILED" ? (
          <div className="mt-2 rounded-lg border-2 border-destructive-border bg-destructive-surface px-4 py-3 text-destructive-surface-foreground">
            <p className="text-base font-semibold">
              {document.error_message ?? "This file could not be read."}
            </p>
            {document.remediation ? <p className="mt-1 text-sm">{document.remediation}</p> : null}
          </div>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {document.is_encrypted ? <Badge variant="outline">Password</Badge> : null}
        <DocumentStatusBadge status={document.status} />
      </div>
    </li>
  );
}

export default function BatchProcessingPage() {
  const params = useParams<{ id: string }>();
  const batchId = params.id;

  const session = useSession();
  const canEdit = Boolean(session.data && roleSatisfies(session.data.user.role, "OPERATOR"));

  const progress = useBatchProgress(batchId);
  const streamFinished = progress.snapshot?.finished ?? false;

  /*
   * The stream carries the batch's counters and nothing else, so the batch itself is
   * polled only when the stream gave up, while the file list is polled while work is
   * in flight either way - a per-file status change is not something the stream
   * reports.
   */
  const batch = useBatch(batchId, {
    poll: progress.transport === "polling" && !streamFinished,
  });
  const live = progress.snapshot ?? (batch.data ? snapshotFromBatch(batch.data) : null);
  const working = live ? !live.finished : Boolean(batch.data && !isTerminalBatchStatus(batch.data.status));

  const documents = useBatchDocuments(batchId, { poll: working });
  const start = useStartBatch();
  const reprocess = useReprocessRows();

  // Only the failed rows are fetched, because that is all the retry action needs;
  // the full row list belongs to the results screen.
  const failedRows = useBatchRows(batchId, { status: "FAILED" });
  const failedRowList = React.useMemo(() => flattenRowPages(failedRows.data), [failedRows.data]);

  /*
   * The stream reports the batch's counters, not each file's stage, so when it says
   * the batch is over the detail and the file list are read once more - otherwise the
   * last few files would sit on the stage they were at when polling stopped.
   */
  const settledOnce = React.useRef(false);
  React.useEffect(() => {
    if (!streamFinished || settledOnce.current) return;
    settledOnce.current = true;
    void batch.refetch();
    void documents.refetch();
    void failedRows.refetch();
  }, [streamFinished, batch, documents, failedRows]);

  const files = documents.data?.items ?? [];
  const failedFiles = files.filter((file) => file.status === "FAILED");
  // A file that fell over before any certificate was found has nothing to reprocess:
  // there is no row, and therefore no unit to re-run.
  const failedWithoutRows = failedFiles.filter(
    (file) => !failedRowList.some((row) => row.document_id === file.id),
  );

  const retryFailures = () => {
    if (failedRowList.length === 0) return;
    reprocess.mutate(
      failedRowList.map((row) => row.id),
      {
        onSuccess: (result) => {
          void failedRows.refetch();
          if (result.failed.length === 0) {
            toast.success(
              `${result.approved.length} ${result.approved.length === 1 ? "certificate" : "certificates"} queued to be read again.`,
            );
            return;
          }
          toast.warning(
            `${result.approved.length} queued, ${result.failed.length} could not be: ${result.failed[0]?.message ?? ""}`,
          );
        },
        onError: (error) => toast.error(errorMessage(error, "Nothing could be queued.")),
      },
    );
  };

  const percent = live?.percent ?? 0;
  const hasRows = (live?.unit_count ?? 0) > 0;

  return (
    <AppShell>
      <PageHeader
        icon={Timer}
        title={batch.data?.name ?? "Batch"}
        description="Watch the files being read, and open the results when the certificates are ready."
        actions={
          hasRows ? (
            <Button asChild size="lg">
              <Link href={`/batches/${batchId}/results`}>
                <Table2 aria-hidden="true" />
                Check the results
              </Link>
            </Button>
          ) : null
        }
      />

      {progress.isGone ? (
        <Alert variant="destructive" className="mt-8">
          <AlertTriangle aria-hidden="true" />
          <AlertTitle>This batch has been deleted</AlertTitle>
          <AlertDescription>
            <Link href="/" className="underline decoration-2 underline-offset-4">
              Go back to the list of batches
            </Link>
          </AlertDescription>
        </Alert>
      ) : null}

      {batch.isError ? (
        <Alert variant="destructive" className="mt-8">
          <AlertTriangle aria-hidden="true" />
          <AlertTitle>This batch could not be loaded</AlertTitle>
          <AlertDescription>{errorMessage(batch.error, "Try again in a moment.")}</AlertDescription>
        </Alert>
      ) : null}

      <Card className="mt-8">
        <CardHeader className="gap-4 pb-5">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <CardTitle>Reading the files</CardTitle>
            <div className="flex items-center gap-3">
              {live ? <BatchStatusBadge status={live.status} /> : null}
              {/* Which way the page is listening. An operator watching a long batch
                  should be able to tell "nothing has changed" from "this page
                  stopped listening". */}
              {working ? (
                <span className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground">
                  {progress.transport === "polling" ? (
                    <>
                      <RefreshCw aria-hidden="true" className="size-4" />
                      Checking every few seconds
                    </>
                  ) : (
                    <>
                      <Radio aria-hidden="true" className="size-4" />
                      Live
                    </>
                  )}
                </span>
              ) : null}
            </div>
          </div>

          <div className="flex items-center gap-4">
            <Progress
              value={percent}
              label={`${batch.data?.name ?? "This batch"} is ${Math.round(percent)} percent read`}
              tone={
                (live?.failed_count ?? 0) > 0 ? "warning" : live?.finished ? "success" : "default"
              }
            />
            <span className="w-16 shrink-0 text-right text-xl font-bold tabular-nums">
              {Math.round(percent)}%
            </span>
          </div>
        </CardHeader>

        <CardContent className="space-y-5">
          {live ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              <CountTile label="Files" value={live.file_count} />
              <CountTile label="Read" value={live.processed_count} />
              <CountTile label="Certificates" value={live.unit_count} />
              <CountTile label="Duplicates" value={live.duplicate_count} />
              <CountTile label="Failed" value={live.failed_count} tone="danger" />
            </div>
          ) : (
            <Skeleton className="h-20 w-full" />
          )}

          {batch.data?.status === "CREATED" ? (
            <div className="flex flex-wrap items-center gap-4 rounded-lg border-2 border-primary-border bg-primary-surface px-5 py-4">
              <p className="flex-1 text-base font-semibold text-primary-surface-foreground">
                These files have been uploaded but not read yet.
              </p>
              <Button
                size="lg"
                disabled={!canEdit || start.isPending || files.length === 0}
                onClick={() =>
                  start.mutate(batchId, {
                    onSuccess: () => toast.success("Reading has started."),
                    onError: (error) =>
                      toast.error(errorMessage(error, "The batch could not be started.")),
                  })
                }
              >
                <Play aria-hidden="true" />
                Start reading
              </Button>
            </div>
          ) : null}

          {batch.data?.error_message ? (
            <Alert variant="destructive">
              <AlertTriangle aria-hidden="true" />
              <AlertTitle>The batch stopped</AlertTitle>
              <AlertDescription>{batch.data.error_message}</AlertDescription>
            </Alert>
          ) : null}

          {failedRowList.length > 0 || failedFiles.length > 0 ? (
            <Alert variant="warning">
              <AlertTriangle aria-hidden="true" />
              <AlertTitle>
                {failedFiles.length > 0
                  ? `${failedFiles.length} ${failedFiles.length === 1 ? "file" : "files"} could not be read`
                  : `${failedRowList.length} ${failedRowList.length === 1 ? "certificate" : "certificates"} could not be read`}
              </AlertTitle>
              <AlertDescription className="space-y-3">
                {failedRowList.length > 0 ? (
                  <p>
                    {failedRowList.length}{" "}
                    {failedRowList.length === 1 ? "certificate" : "certificates"} can be tried
                    again. The values anyone has already typed are kept.
                  </p>
                ) : null}
                {failedWithoutRows.length > 0 ? (
                  <p>
                    {failedWithoutRows.length}{" "}
                    {failedWithoutRows.length === 1 ? "file" : "files"} failed before any
                    certificate was found, so there is nothing to try again. Fix what the file
                    list says below and upload {failedWithoutRows.length === 1 ? "it" : "them"}{" "}
                    again.
                  </p>
                ) : null}
                <Button
                  variant="outline"
                  disabled={!canEdit || failedRowList.length === 0 || reprocess.isPending}
                  onClick={retryFailures}
                >
                  <RotateCcw aria-hidden="true" />
                  Try the failed certificates again
                </Button>
              </AlertDescription>
            </Alert>
          ) : null}
        </CardContent>
      </Card>

      <Card className="mt-8">
        <CardHeader className="pb-4">
          <CardTitle>Files in this batch</CardTitle>
          <CardDescription>
            {documents.data
              ? `${files.length.toLocaleString()} ${files.length === 1 ? "file" : "files"}`
              : "Loading the file list"}
          </CardDescription>
        </CardHeader>
        <Separator tone="subtle" />
        <CardContent className="p-0">
          {documents.isPending ? (
            <div className="space-y-2 p-6">
              {[0, 1, 2].map((index) => (
                <Skeleton key={index} className="h-16 w-full" />
              ))}
            </div>
          ) : files.length === 0 ? (
            <p className="p-6 text-base text-muted-foreground">
              No files were uploaded into this batch.
            </p>
          ) : (
            <ul className="divide-y divide-border-subtle">
              {files.map((file) => (
                <FileRow key={file.id} document={file} />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </AppShell>
  );
}
