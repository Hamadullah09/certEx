"use client";

import * as React from "react";
import Link from "next/link";
import { FileStack, Plus, Search } from "lucide-react";

import { AppShell } from "@/components/app-shell";
import { BatchStatusBadge, isTerminalBatchStatus } from "@/components/batch-status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { useBatchList } from "@/hooks/use-batches";
import type { BatchSummary } from "@/lib/schemas/batches";
import { formatBytes } from "@/lib/utils";

function progressOf(batch: BatchSummary): number {
  if (batch.file_count <= 0) return 0;
  const done = batch.processed_count + batch.failed_count + batch.duplicate_count;
  return Math.min((done / batch.file_count) * 100, 100);
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function BatchRow({ batch }: { batch: BatchSummary }) {
  const percent = progressOf(batch);
  const active = !isTerminalBatchStatus(batch.status);

  return (
    <tr className="border-b border-border last:border-0 hover:bg-accent/40">
      <td className="px-4 py-3">
        <Link
          href={`/batches/${batch.id}`}
          className="font-medium underline-offset-4 hover:underline"
        >
          {batch.name}
        </Link>
        <p className="mt-0.5 text-xs text-muted-foreground">{formatDate(batch.created_at)}</p>
      </td>
      <td className="px-4 py-3">
        <BatchStatusBadge status={batch.status} />
      </td>
      <td className="px-4 py-3 text-right tabular-nums">
        {batch.file_count.toLocaleString()}
      </td>
      <td className="px-4 py-3 text-right tabular-nums">
        {batch.unit_count.toLocaleString()}
      </td>
      <td className="px-4 py-3 text-right tabular-nums">
        {batch.failed_count > 0 ? (
          <span className="text-destructive">{batch.failed_count.toLocaleString()}</span>
        ) : (
          <span className="text-muted-foreground">0</span>
        )}
      </td>
      <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">
        {formatBytes(batch.total_bytes)}
      </td>
      <td className="w-40 px-4 py-3">
        <div className="flex items-center gap-2">
          <Progress
            value={percent}
            label={`${batch.name} is ${Math.round(percent)} percent processed`}
            tone={batch.failed_count > 0 ? "warning" : active ? "default" : "success"}
          />
          <span className="w-10 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
            {Math.round(percent)}%
          </span>
        </div>
      </td>
    </tr>
  );
}

function EmptyState({ hasSearch }: { hasSearch: boolean }) {
  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <FileStack aria-hidden="true" className="size-5 text-muted-foreground" />
          {hasSearch ? "No batches match that search" : "No batches yet"}
        </CardTitle>
        <CardDescription>
          {hasSearch
            ? "Try a different name, or clear the search to see everything."
            : "A batch is a folder of certificates processed together. Create one, drop in your PDFs and Word files, and the pipeline reads them."}
        </CardDescription>
      </CardHeader>
      {!hasSearch ? (
        <CardContent>
          <Button asChild>
            <Link href="/batches/new">
              <Plus aria-hidden="true" />
              New batch
            </Link>
          </Button>
        </CardContent>
      ) : null}
    </Card>
  );
}

export default function DashboardPage() {
  const [search, setSearch] = React.useState("");
  const [debounced, setDebounced] = React.useState("");

  // Debounced so typing does not fire a query per keystroke against a workspace
  // that may hold thousands of batches.
  React.useEffect(() => {
    const timer = setTimeout(() => setDebounced(search.trim()), 250);
    return () => clearTimeout(timer);
  }, [search]);

  const query = useBatchList(debounced ? { search: debounced } : {});
  const batches = query.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <AppShell>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Batches</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Upload certificates, review what was extracted, and export a CSV.
          </p>
        </div>
        <Button asChild>
          <Link href="/batches/new">
            <Plus aria-hidden="true" />
            New batch
          </Link>
        </Button>
      </div>

      <div className="relative mt-6 max-w-sm">
        <Search
          aria-hidden="true"
          className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search batches by name"
          aria-label="Search batches by name"
          className="pl-9"
        />
      </div>

      {query.isPending ? (
        <div className="mt-6 space-y-2">
          {[0, 1, 2, 3].map((row) => (
            <Skeleton key={row} className="h-14 w-full" />
          ))}
        </div>
      ) : batches.length === 0 ? (
        <EmptyState hasSearch={Boolean(debounced)} />
      ) : (
        <div className="mt-6 overflow-x-auto rounded-lg border border-border">
          <table className="w-full min-w-[46rem] border-collapse text-sm">
            <caption className="sr-only">
              Batches in this workspace, newest first, with processing progress.
            </caption>
            <thead>
              <tr className="border-b border-border bg-muted/50 text-left">
                <th scope="col" className="px-4 py-2 font-medium">Name</th>
                <th scope="col" className="px-4 py-2 font-medium">Status</th>
                <th scope="col" className="px-4 py-2 text-right font-medium">Files</th>
                <th scope="col" className="px-4 py-2 text-right font-medium">Rows</th>
                <th scope="col" className="px-4 py-2 text-right font-medium">Failed</th>
                <th scope="col" className="px-4 py-2 text-right font-medium">Size</th>
                <th scope="col" className="px-4 py-2 font-medium">Progress</th>
              </tr>
            </thead>
            <tbody>
              {batches.map((batch) => (
                <BatchRow key={batch.id} batch={batch} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {query.hasNextPage ? (
        <div className="mt-4 flex justify-center">
          <Button
            variant="outline"
            onClick={() => void query.fetchNextPage()}
            disabled={query.isFetchingNextPage}
          >
            {query.isFetchingNextPage ? "Loading" : "Load more"}
          </Button>
        </div>
      ) : null}
    </AppShell>
  );
}
