"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { AlertTriangle, ArrowRight, FolderPlus, Search } from "lucide-react";

import { AppShell } from "@/components/app-shell";
import { BatchStatusBadge } from "@/components/batch-status-badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { useBatchList } from "@/hooks/use-batches";
import { useCertificateTypes } from "@/hooks/use-register";
import { ApiError } from "@/lib/api";
import { categoryLook } from "@/lib/categories";
import type { BatchSummary } from "@/lib/schemas/batches";
import { cn, formatBytes } from "@/lib/utils";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function progressPercent(batch: BatchSummary): number {
  if (batch.file_count <= 0) return 0;
  const done = batch.processed_count + batch.failed_count + batch.duplicate_count;
  return Math.round(Math.min(done / batch.file_count, 1) * 100);
}

/**
 * One batch, as a card rather than a table row.
 *
 * A card because this list is read, not scanned: an office has a few dozen registers,
 * each one a year's work, and the question is "which register" rather than "which of
 * these four hundred rows". The counts that matter are the ones that tell you whether
 * the batch still needs attention.
 */
function BatchCard({ batch }: { batch: BatchSummary }) {
  const percent = progressPercent(batch);

  return (
    <li>
      <Link
        href={`/batches/${batch.id}`}
        className="block rounded-xl focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      >
        <Card className="h-full border-2 transition-colors hover:border-primary hover:bg-accent/30">
          <CardContent className="space-y-3 p-5">
            <div className="flex items-start justify-between gap-3">
              <h3 className="text-xl font-bold text-foreground">{batch.name}</h3>
              <ArrowRight aria-hidden="true" className="mt-1 size-5 shrink-0 text-primary" />
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <BatchStatusBadge status={batch.status} />
              <span className="text-base text-muted-foreground">
                Created {formatDate(batch.created_at)}
              </span>
            </div>

            <dl className="flex flex-wrap gap-x-6 gap-y-1 text-base">
              <div className="flex gap-1.5">
                <dt className="text-muted-foreground">Documents</dt>
                <dd className="font-bold tabular-nums">{batch.file_count.toLocaleString()}</dd>
              </div>
              <div className="flex gap-1.5">
                <dt className="text-muted-foreground">Certificates</dt>
                <dd className="font-bold tabular-nums">{batch.unit_count.toLocaleString()}</dd>
              </div>
              {batch.failed_count > 0 ? (
                <div className="flex gap-1.5">
                  <dt className="text-muted-foreground">Failed</dt>
                  <dd className="font-bold tabular-nums text-destructive">
                    {batch.failed_count.toLocaleString()}
                  </dd>
                </div>
              ) : null}
              <div className="flex gap-1.5">
                <dt className="text-muted-foreground">Size</dt>
                <dd className="font-bold tabular-nums">{formatBytes(batch.total_bytes)}</dd>
              </div>
            </dl>

            {batch.file_count > 0 && percent < 100 ? (
              <div className="flex items-center gap-3">
                <Progress value={percent} label={`${batch.name} is ${percent} percent read`} />
                <span className="w-12 shrink-0 text-right text-base font-bold tabular-nums">
                  {percent}%
                </span>
              </div>
            ) : null}
          </CardContent>
        </Card>
      </Link>
    </li>
  );
}

/**
 * Every batch in one certificate category.
 *
 * The search box filters by batch name server-side, and the list pages by cursor, so
 * a category holding thousands of registers costs the same to open as one holding
 * three.
 */
export default function CategoryPage() {
  const params = useParams<{ typeId: string }>();
  const typeId = params.typeId;

  const [searchBox, setSearchBox] = React.useState("");
  const [search, setSearch] = React.useState("");

  // Debounced, so typing does not fire a query per keystroke.
  React.useEffect(() => {
    const timer = setTimeout(() => setSearch(searchBox.trim()), 300);
    return () => clearTimeout(timer);
  }, [searchBox]);

  const categories = useCertificateTypes();
  const category = (categories.data ?? []).find((item) => item.id === typeId);
  const look = categoryLook(category?.classifier_key);
  const batches = useBatchList({ certificateTypeId: typeId, search: search || undefined });
  const rows = (batches.data?.pages ?? []).flatMap((page) => page.items);

  return (
    <AppShell>
      <PageHeader
        icon={look.Icon}
        iconClassName={cn(look.surface, look.ink)}
        title={category?.name ?? "Category"}
        description={
          category?.description ??
          "Every batch of this kind of certificate, newest first."
        }
        actions={
          <Button asChild size="lg">
            <Link href={`/categories/${typeId}/new`}>
              <FolderPlus aria-hidden="true" />
              Create new batch
            </Link>
          </Button>
        }
      />

      <div className="mt-8 max-w-xl">
        <Label htmlFor="batch-search">Find a batch</Label>
        <div className="relative mt-1.5">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 size-5 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            id="batch-search"
            className="pl-11"
            placeholder="Type part of the name"
            value={searchBox}
            onChange={(event) => setSearchBox(event.target.value)}
          />
        </div>
      </div>

      {batches.isError ? (
        <Alert variant="destructive" className="mt-6">
          <AlertTriangle aria-hidden="true" />
          <AlertTitle>The batches could not be loaded</AlertTitle>
          <AlertDescription>
            {batches.error instanceof ApiError
              ? batches.error.userMessage
              : "Try again in a moment."}
          </AlertDescription>
        </Alert>
      ) : null}

      {batches.isPending ? (
        <div className="mt-6 grid gap-4 sm:grid-cols-2">
          {[0, 1, 2, 3].map((index) => (
            <Skeleton key={index} className="h-44 w-full rounded-xl" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <Card className="mt-6">
          <CardContent className="p-8 text-center">
            <h2 className="text-xl font-bold">
              {search ? "No batch matches that name" : "No batches here yet"}
            </h2>
            <p className="mx-auto mt-2 max-w-prose text-base text-muted-foreground">
              {search
                ? "Clear the search to see every batch in this category."
                : "A batch is one set of certificates read together - usually a year, or one register. Creating it is where you say which columns to pull out of every document in it."}
            </p>
            {!search ? (
              <Button asChild size="lg" className="mt-5">
                <Link href={`/categories/${typeId}/new`}>
                  <FolderPlus aria-hidden="true" />
                  Create the first batch
                </Link>
              </Button>
            ) : null}
          </CardContent>
        </Card>
      ) : (
        <>
          <p className="mt-6 text-base text-muted-foreground">
            <span className="font-bold text-foreground tabular-nums">
              {rows.length.toLocaleString()}
            </span>{" "}
            {rows.length === 1 ? "batch" : "batches"}
            {batches.hasNextPage ? " so far" : ""}
          </p>
          <ul className="mt-3 grid gap-4 sm:grid-cols-2">
            {rows.map((batch) => (
              <BatchCard key={batch.id} batch={batch} />
            ))}
          </ul>
          {batches.hasNextPage ? (
            <Button
              variant="outline"
              size="lg"
              className="mt-5"
              disabled={batches.isFetchingNextPage}
              onClick={() => void batches.fetchNextPage()}
            >
              {batches.isFetchingNextPage ? "Loading…" : "Show more batches"}
            </Button>
          ) : null}
        </>
      )}
    </AppShell>
  );
}
