"use client";

import * as React from "react";
import Link from "next/link";
import { ChevronLeft, ClipboardCheck } from "lucide-react";

import { AppShell } from "@/components/app-shell";
import { SearchResults } from "@/components/register/search-results";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { useCertificateSearch } from "@/hooks/use-register";
import { useReviewSummary } from "@/hooks/use-review";

const PAGE_SIZE = 25;

type Filter = "all" | "duplicates";

/**
 * The review queue.
 *
 * Two lists rather than one, because the two kinds of question call for different
 * work: a doubtful reading is checked against the scan, and a repeated certificate
 * number is a decision about two records. Mixing them means a reviewer switches
 * between two different jobs every few rows.
 */
export default function ReviewQueuePage() {
  const [filter, setFilter] = React.useState<Filter>("all");
  const [offset, setOffset] = React.useState(0);
  const { data: summary } = useReviewSummary();

  const query = {
    needsReview: filter === "all" ? true : undefined,
    duplicatesOnly: filter === "duplicates",
    limit: PAGE_SIZE,
    offset,
  };
  const { data, isPending, isFetching, error } = useCertificateSearch(query);

  function choose(next: Filter) {
    setFilter(next);
    setOffset(0);
  }

  return (
    <AppShell>
      <div className="space-y-6">
        <Button variant="ghost" asChild className="-ml-3">
          <Link href="/register">
            <ChevronLeft aria-hidden="true" />
            Register
          </Link>
        </Button>

        <PageHeader
          icon={ClipboardCheck}
          title="Review"
          description="Entries the system is not sure about, and numbers it has seen twice."
        />

        <Card>
          <CardContent className="flex flex-wrap items-center gap-3 py-4">
            <Button
              variant={filter === "all" ? "default" : "outline"}
              onClick={() => choose("all")}
              aria-pressed={filter === "all"}
            >
              Waiting for review
              {summary ? (
                <Badge variant="outline" className="ml-2">
                  {summary.needs_review}
                </Badge>
              ) : null}
            </Button>
            <Button
              variant={filter === "duplicates" ? "default" : "outline"}
              onClick={() => choose("duplicates")}
              aria-pressed={filter === "duplicates"}
            >
              Possible duplicates
              {summary ? (
                <Badge variant="outline" className="ml-2">
                  {summary.suspected_duplicates}
                </Badge>
              ) : null}
            </Button>
          </CardContent>
        </Card>

        {summary && summary.needs_review === 0 && summary.suspected_duplicates === 0 ? (
          <Card>
            <CardContent className="py-10 text-center">
              <p className="text-lg font-semibold text-foreground">Nothing is waiting</p>
              <p className="mt-1 text-base text-muted-foreground">
                Every entry in the register has been accepted, and no certificate number
                has an unsettled question against it.
              </p>
            </CardContent>
          </Card>
        ) : (
          <SearchResults
            results={data}
            isPending={isPending || (isFetching && !data)}
            error={error}
            offset={offset}
            onOffsetChange={setOffset}
          />
        )}
      </div>
    </AppShell>
  );
}
