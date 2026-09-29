"use client";

import Link from "next/link";
import { AlertTriangle, CircleAlert, Copy, Users } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { valueTextAttributes } from "@/lib/text-direction";
import {
  MATCH_KIND_CAPTION,
  type SearchHit,
  type SearchResponse,
} from "@/lib/schemas/certificates";

/** A date as printed, or a dash: a blank cell in a list reads as a missing column. */
function displayDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const parsed = new Date(`${iso}T00:00:00`);
  return Number.isNaN(parsed.valueOf())
    ? iso
    : parsed.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/**
 * One result.
 *
 * Laid out so a clerk looking at four people with the same name can tell which is
 * theirs without opening any of them: the number, the other party or the date, and a
 * plain count of how many namesakes there are.
 */
function ResultRow({ hit }: { hit: SearchHit }) {
  const record = hit.certificate;
  return (
    <li>
      <Link
        href={`/register/certificates/${record.id}`}
        className="group block rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        <Card className="transition-colors group-hover:border-primary">
          <CardContent className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3 py-4">
            <div className="min-w-0 space-y-1">
              <p
                className="truncate text-lg font-semibold text-foreground"
                {...valueTextAttributes(record.primary_name)}
              >
                {record.primary_name ?? "Name not recorded"}
                {record.secondary_name ? (
                  <span className="text-muted-foreground"> · {record.secondary_name}</span>
                ) : null}
              </p>
              <p className="truncate font-mono text-base text-muted-foreground">
                {record.certificate_number}
                {record.registration_number ? (
                  <span> · reg {record.registration_number}</span>
                ) : null}
              </p>
            </div>

            <div className="flex shrink-0 flex-col items-end gap-2">
              <p className="text-base font-semibold text-foreground">
                {displayDate(record.event_date)}
              </p>
              <div className="flex flex-wrap items-center justify-end gap-2">
                {hit.same_name_count > 1 ? (
                  <Badge variant="outline" className="gap-1">
                    <Users aria-hidden="true" className="size-4" />
                    {hit.same_name_count} with this name
                  </Badge>
                ) : null}
                {record.duplicate_status !== "NONE" ? (
                  <Badge variant="warning" className="gap-1">
                    <Copy aria-hidden="true" className="size-4" />
                    Possible duplicate
                  </Badge>
                ) : null}
                {record.needs_review ? (
                  <Badge variant="warning" className="gap-1">
                    <CircleAlert aria-hidden="true" className="size-4" />
                    Needs review
                  </Badge>
                ) : null}
                {record.status !== "ACTIVE" ? (
                  <Badge variant="danger">{record.status.toLowerCase()}</Badge>
                ) : null}
              </div>
            </div>
          </CardContent>
        </Card>
      </Link>
    </li>
  );
}

export function SearchResults({
  results,
  isPending,
  error,
  offset,
  onOffsetChange,
}: {
  results: SearchResponse | undefined;
  isPending: boolean;
  error: Error | null;
  offset: number;
  onOffsetChange: (offset: number) => void;
}) {
  if (error) {
    return (
      <Alert variant="destructive">
        <AlertTriangle aria-hidden="true" />
        <AlertTitle>The search could not be run</AlertTitle>
        <AlertDescription>{error.message}</AlertDescription>
      </Alert>
    );
  }

  if (isPending) {
    return (
      <ul className="space-y-3" aria-busy="true">
        {[0, 1, 2].map((index) => (
          <li key={index}>
            <Skeleton className="h-24 w-full rounded-xl" />
          </li>
        ))}
      </ul>
    );
  }

  if (!results) return null;

  const shown = results.offset + results.items.length;
  const limit = results.limit;

  return (
    <div className="space-y-4">
      {/* Saying which tier answered is the difference between an answer and a
          suggestion, and a clerk needs to know which one they are reading. */}
      <p className="text-base text-muted-foreground" role="status">
        {MATCH_KIND_CAPTION[results.match]}
        {results.total > 0 ? (
          <span>
            {" "}
            Showing {results.offset + 1}–{shown} of {results.total.toLocaleString()}.
          </span>
        ) : null}
      </p>

      {results.items.length > 0 ? (
        <ul className="space-y-3">
          {results.items.map((hit) => (
            <ResultRow key={hit.certificate.id} hit={hit} />
          ))}
        </ul>
      ) : (
        <Card>
          <CardContent className="py-8 text-center">
            <p className="text-lg font-semibold text-foreground">Nothing found</p>
            <p className="mt-1 text-base text-muted-foreground">
              Check the certificate number, or search by the name on the certificate
              instead. A partial number of three characters or more also works.
            </p>
          </CardContent>
        </Card>
      )}

      {results.total > limit ? (
        <div className="flex items-center justify-between gap-4">
          <Button
            variant="outline"
            disabled={offset === 0}
            onClick={() => onOffsetChange(Math.max(0, offset - limit))}
          >
            Previous
          </Button>
          <p className="text-base text-muted-foreground">
            Page {Math.floor(offset / limit) + 1} of {Math.ceil(results.total / limit)}
          </p>
          <Button
            variant="outline"
            disabled={!results.has_more}
            onClick={() => onOffsetChange(offset + limit)}
          >
            Next
          </Button>
        </div>
      ) : null}
    </div>
  );
}
