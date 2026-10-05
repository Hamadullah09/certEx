"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { AlertTriangle, ChevronLeft, FileText, Search } from "lucide-react";

import { AppShell } from "@/components/app-shell";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useBatch } from "@/hooks/use-batches";
import { useCertificateSearch } from "@/hooks/use-register";
import { ApiError } from "@/lib/api";
import { MATCH_KIND_CAPTION } from "@/lib/schemas/certificates";
import { valueTextAttributes } from "@/lib/text-direction";

const PAGE_SIZE = 25;

/** The columns worth showing in a result row before it stops being readable. */
const MAX_RESULT_COLUMNS = 5;

/**
 * Finding one certificate inside a batch, by any column the batch defines.
 *
 * The field list is the batch's own columns, so a register with a "Village" column is
 * searched by village and one without it never offers the option. That is also why the
 * search is driven by a chosen column rather than by one box that guesses: an office
 * that invents a column has nothing in the general search that knows what it means,
 * and guessing across every column at once is the query that does not scale.
 *
 * Paged by offset against a server-side count. Nothing about this screen changes when
 * the batch holds a million records - it asks for twenty-five of them.
 */
export default function BatchSearchPage() {
  const params = useParams<{ id: string }>();
  const batchId = params.id;
  const batch = useBatch(batchId);

  // Memoised because it seeds an effect: a fresh [] on every render would re-run the
  // field choice and fight the operator for the dropdown.
  const columns = React.useMemo(() => batch.data?.columns ?? [], [batch.data]);
  const [field, setField] = React.useState("");
  const [valueBox, setValueBox] = React.useState("");
  const [value, setValue] = React.useState("");
  const [page, setPage] = React.useState(0);

  // The identifier column is what somebody at the counter is usually holding.
  React.useEffect(() => {
    if (field || columns.length === 0) return;
    const identifier = columns.find((column) => column.role === "identifier");
    setField((identifier ?? columns[0])?.name ?? "");
  }, [columns, field]);

  React.useEffect(() => {
    const timer = setTimeout(() => {
      setValue(valueBox.trim());
      setPage(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [valueBox]);

  const results = useCertificateSearch(
    { batchId, field, fieldValue: value, limit: PAGE_SIZE, offset: page * PAGE_SIZE },
    { enabled: Boolean(field) },
  );

  const shown = columns.slice(0, MAX_RESULT_COLUMNS);
  const hits = results.data?.items ?? [];
  const total = results.data?.total ?? 0;
  const lastPage = Math.max(Math.ceil(total / PAGE_SIZE) - 1, 0);

  return (
    <AppShell wide>
      <Button variant="ghost" asChild className="-ml-3">
        <Link href={`/batches/${batchId}`}>
          <ChevronLeft aria-hidden="true" />
          {batch.data?.name ?? "Back to the batch"}
        </Link>
      </Button>

      <PageHeader
        icon={Search}
        title="Find a certificate"
        description="Search this batch by any of its columns. Open a result to see the original document."
      />

      <div className="mt-8 grid max-w-3xl gap-4 sm:grid-cols-[minmax(0,14rem)_1fr]">
        <div>
          <Label htmlFor="search-field">Which column</Label>
          {batch.isPending ? (
            <Skeleton className="mt-1.5 h-12 w-full" />
          ) : (
            <Select
              id="search-field"
              className="mt-1.5"
              value={field}
              onChange={(event) => {
                setField(event.target.value);
                setPage(0);
              }}
            >
              {columns.map((column) => (
                <option key={column.name} value={column.name}>
                  {column.label}
                </option>
              ))}
            </Select>
          )}
        </div>

        <div>
          <Label htmlFor="search-value">What to look for</Label>
          <div className="relative mt-1.5">
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute left-3 top-1/2 size-5 -translate-y-1/2 text-muted-foreground"
            />
            <Input
              id="search-value"
              className="pl-11"
              placeholder="Type the value exactly as it appears"
              value={valueBox}
              onChange={(event) => setValueBox(event.target.value)}
            />
          </div>
        </div>
      </div>

      {results.isError ? (
        <Alert variant="destructive" className="mt-6">
          <AlertTriangle aria-hidden="true" />
          <AlertTitle>The search could not be run</AlertTitle>
          <AlertDescription>
            {results.error instanceof ApiError
              ? results.error.userMessage
              : "Try again in a moment."}
          </AlertDescription>
        </Alert>
      ) : null}

      {!value ? (
        <p className="mt-8 text-base text-muted-foreground">
          Type a value above to search. Nothing is loaded until you do.
        </p>
      ) : results.isPending ? (
        <div className="mt-8 space-y-2">
          {[0, 1, 2].map((index) => (
            <Skeleton key={index} className="h-14 w-full" />
          ))}
        </div>
      ) : hits.length === 0 ? (
        <Card className="mt-8">
          <CardContent className="p-8 text-center">
            <h2 className="text-xl font-bold">Nothing matched</h2>
            <p className="mx-auto mt-2 max-w-prose text-base text-muted-foreground">
              No certificate in this batch has that value in{" "}
              <span className="font-semibold">
                {columns.find((column) => column.name === field)?.label ?? "that column"}
              </span>
              . Check the spelling, or try another column.
            </p>
          </CardContent>
        </Card>
      ) : (
        <>
          <p className="mt-8 text-base text-muted-foreground">
            <span className="font-bold text-foreground tabular-nums">
              {total.toLocaleString()}
            </span>{" "}
            {total === 1 ? "certificate" : "certificates"}
            {results.data ? ` — ${MATCH_KIND_CAPTION[results.data.match]}` : ""}
          </p>

          <div className="mt-3 overflow-x-auto rounded-xl border-2 border-border">
            <table className="w-full border-collapse text-left">
              <caption className="sr-only">
                Certificates in this batch matching the search, with a link to each
                original document.
              </caption>
              <thead>
                <tr className="border-b-2 border-border bg-muted">
                  {shown.map((column) => (
                    <th key={column.name} scope="col" className="px-5 py-3.5 font-bold">
                      {column.label}
                    </th>
                  ))}
                  <th scope="col" className="px-5 py-3.5 font-bold">
                    Status
                  </th>
                  <th scope="col" className="px-5 py-3.5 font-bold">
                    Document
                  </th>
                </tr>
              </thead>
              <tbody>
                {hits.map((hit) => (
                  <tr
                    key={hit.certificate.id}
                    className="border-b border-border-subtle last:border-0 hover:bg-accent/40"
                  >
                    {shown.map((column, index) => {
                      const cell = hit.values[column.name] ?? "";
                      return (
                        <td key={column.name} className="px-5 py-3.5 text-base">
                          {index === 0 ? (
                            <Link
                              href={`/register/certificates/${hit.certificate.id}`}
                              className="font-semibold text-primary underline decoration-2 underline-offset-4"
                              {...valueTextAttributes(cell)}
                            >
                              {cell || "—"}
                            </Link>
                          ) : (
                            <span {...valueTextAttributes(cell)}>{cell || "—"}</span>
                          )}
                        </td>
                      );
                    })}
                    <td className="px-5 py-3.5">
                      {hit.certificate.needs_review ? (
                        <Badge variant="warning">Needs checking</Badge>
                      ) : (
                        <Badge variant="success">Checked</Badge>
                      )}
                    </td>
                    <td className="px-5 py-3.5">
                      <Button variant="outline" asChild>
                        <Link href={`/register/certificates/${hit.certificate.id}`}>
                          <FileText aria-hidden="true" />
                          Open
                        </Link>
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {total > PAGE_SIZE ? (
            <div className="mt-4 flex flex-wrap items-center gap-4">
              <Button
                variant="outline"
                disabled={page === 0}
                onClick={() => setPage((current) => Math.max(current - 1, 0))}
              >
                Previous
              </Button>
              <span className="text-base text-muted-foreground tabular-nums">
                Page {page + 1} of {lastPage + 1}
              </span>
              <Button
                variant="outline"
                disabled={page >= lastPage}
                onClick={() => setPage((current) => current + 1)}
              >
                Next
              </Button>
            </div>
          ) : null}
        </>
      )}
    </AppShell>
  );
}
