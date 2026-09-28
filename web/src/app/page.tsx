"use client";

import * as React from "react";
import Link from "next/link";
import { FileStack, Plus, Search } from "lucide-react";

import { AppShell } from "@/components/app-shell";
import { BatchStatusBadge, isTerminalBatchStatus } from "@/components/batch-status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
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
    <tr className="border-b border-border-subtle last:border-0 hover:bg-accent">
      <td className="px-5 py-4">
        {/* Underlined at rest: the batch name is the only way into a batch, so
            it cannot rely on colour to say it is a link. */}
        <Link
          href={`/batches/${batch.id}`}
          className="text-lg font-semibold text-primary underline decoration-2 underline-offset-4 hover:decoration-[3px]"
        >
          {batch.name}
        </Link>
        <p className="mt-1 text-sm text-muted-foreground">{formatDate(batch.created_at)}</p>
      </td>
      <td className="px-5 py-4">
        <BatchStatusBadge status={batch.status} />
      </td>
      <td className="px-5 py-4 text-right font-semibold tabular-nums">
        {batch.file_count.toLocaleString()}
      </td>
      <td className="px-5 py-4 text-right font-semibold tabular-nums">
        {batch.unit_count.toLocaleString()}
      </td>
      <td className="px-5 py-4 text-right tabular-nums">
        {batch.failed_count > 0 ? (
          <span className="font-bold text-destructive">
            {batch.failed_count.toLocaleString()}
          </span>
        ) : (
          <span className="text-muted-foreground">0</span>
        )}
      </td>
      <td className="px-5 py-4 text-right tabular-nums text-muted-foreground">
        {formatBytes(batch.total_bytes)}
      </td>
      <td className="w-48 px-5 py-4">
        <div className="flex items-center gap-3">
          <Progress
            value={percent}
            label={`${batch.name} is ${Math.round(percent)} percent processed`}
            tone={batch.failed_count > 0 ? "warning" : active ? "default" : "success"}
          />
          <span className="w-12 shrink-0 text-right text-base font-semibold tabular-nums">
            {Math.round(percent)}%
          </span>
        </div>
      </td>
    </tr>
  );
}

function EmptyState({ hasSearch }: { hasSearch: boolean }) {
  return (
    <Card className="mt-8">
      <CardHeader className="items-start">
        <span
          aria-hidden="true"
          className="mb-1 flex size-14 items-center justify-center rounded-xl border border-secondary-border/50 bg-secondary text-secondary-foreground"
        >
          <FileStack className="size-7" />
        </span>
        <CardTitle>{hasSearch ? "Nothing matched that name" : "No batches yet"}</CardTitle>
        <CardDescription>
          {hasSearch
            ? "Try a different name, or clear the search box to see everything."
            : "A batch is a folder of certificates handled together. Create one, drop in your PDFs and Word files, and the app reads them for you."}
        </CardDescription>
      </CardHeader>
      {!hasSearch ? (
        <CardContent>
          <Button asChild size="lg">
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
      <PageHeader
        icon={FileStack}
        title="Batches"
        description="Upload certificates, check what was read from them, and export a CSV."
        actions={
          <Button asChild size="lg">
            <Link href="/batches/new">
              <Plus aria-hidden="true" />
              New batch
            </Link>
          </Button>
        }
      />

      <div className="mt-9 max-w-md">
        {/* A visible label rather than placeholder-only text: a placeholder
            disappears the moment someone starts typing. */}
        <Label htmlFor="batch-search">Find a batch</Label>
        <div className="relative mt-2">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-4 top-1/2 size-5 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            id="batch-search"
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Type part of the name"
            aria-label="Search batches by name"
            className="pl-12"
          />
        </div>
      </div>

      {query.isPending ? (
        <div className="mt-8 space-y-3">
          {[0, 1, 2, 3].map((row) => (
            <Skeleton key={row} className="h-20 w-full" />
          ))}
        </div>
      ) : batches.length === 0 ? (
        <EmptyState hasSearch={Boolean(debounced)} />
      ) : (
        // The table is wider than a phone, so the scroller is focusable: a
        // keyboard user has to be able to reach the columns on the right.
        <div
          tabIndex={0}
          role="group"
          aria-label="Batches table, scrolls sideways"
          className="mt-8 overflow-x-auto rounded-xl border border-border bg-card shadow-soft"
        >
          <table className="w-full min-w-[62rem] border-collapse text-base">
            <caption className="sr-only">
              Batches in this workspace, newest first, with processing progress.
            </caption>
            <thead>
              <tr className="border-b-2 border-border bg-muted text-left">
                <th scope="col" className="px-5 py-3.5 font-bold">Name</th>
                <th scope="col" className="px-5 py-3.5 font-bold">Status</th>
                <th scope="col" className="px-5 py-3.5 text-right font-bold">Files</th>
                <th scope="col" className="px-5 py-3.5 text-right font-bold">Rows</th>
                <th scope="col" className="px-5 py-3.5 text-right font-bold">Failed</th>
                <th scope="col" className="px-5 py-3.5 text-right font-bold">Size</th>
                <th scope="col" className="px-5 py-3.5 font-bold">Progress</th>
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
        <div className="mt-6 flex justify-center">
          <Button
            variant="outline"
            size="lg"
            onClick={() => void query.fetchNextPage()}
            disabled={query.isFetchingNextPage}
          >
            {query.isFetchingNextPage ? "Loading" : "Show more batches"}
          </Button>
        </div>
      ) : null}
    </AppShell>
  );
}
