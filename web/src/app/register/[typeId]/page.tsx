"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ChevronLeft, Search } from "lucide-react";

import { AppShell } from "@/components/app-shell";
import { SearchResults } from "@/components/register/search-results";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useCertificateSearch, useCertificateTypes } from "@/hooks/use-register";

const PAGE_SIZE = 25;

/** Milliseconds of quiet before a keystroke becomes a query. */
const TYPING_PAUSE = 300;

function useDebounced<T>(value: T, delay: number): T {
  const [settled, setSettled] = React.useState(value);
  React.useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

/**
 * Searching one register.
 *
 * One box, because that is how the counter works: whoever is standing there has a
 * number or a name, and typing it in should be enough. The extra fields below narrow a
 * shared name - which is the case the box alone cannot settle, because four people
 * really are called Muhammad Ahmed and only the father's name or the year tells them
 * apart.
 */
export default function RegisterTypePage() {
  const params = useParams<{ typeId: string }>();
  const typeId = params.typeId;

  const { data: types } = useCertificateTypes();
  const type = types?.find((candidate) => candidate.id === typeId);

  const [text, setText] = React.useState("");
  const [fatherName, setFatherName] = React.useState("");
  const [from, setFrom] = React.useState("");
  const [to, setTo] = React.useState("");
  const [offset, setOffset] = React.useState(0);

  const query = {
    q: useDebounced(text, TYPING_PAUSE),
    fatherName: useDebounced(fatherName, TYPING_PAUSE),
    eventDateFrom: from || undefined,
    eventDateTo: to || undefined,
    certificateTypeId: typeId,
    limit: PAGE_SIZE,
    offset,
  };

  // A changed search is a new search: staying on page four of the previous one
  // would show an empty list and look broken.
  const signature = `${query.q}|${query.fatherName}|${query.eventDateFrom}|${query.eventDateTo}`;
  const previous = React.useRef(signature);
  React.useEffect(() => {
    if (previous.current !== signature) {
      previous.current = signature;
      setOffset(0);
    }
  }, [signature]);

  const { data, isPending, isFetching, error } = useCertificateSearch(query);

  return (
    <AppShell>
      <div className="space-y-6">
        <Button variant="ghost" asChild className="-ml-3">
          <Link href="/register">
            <ChevronLeft aria-hidden="true" />
            All registers
          </Link>
        </Button>

        {type ? (
          <PageHeader
            icon={Search}
            title={type.name}
            description={
              type.description ??
              "Search by certificate number, or by any name printed on the certificate."
            }
          />
        ) : (
          <Skeleton className="h-20 w-full max-w-xl" />
        )}

        <Card>
          <CardContent className="space-y-4 py-5">
            <div className="space-y-2">
              <Label htmlFor="search-text">Certificate number or name</Label>
              <Input
                id="search-text"
                value={text}
                onChange={(event) => setText(event.target.value)}
                placeholder="BC/LHR/2019/1001, or Ayesha Noor Malik"
                autoComplete="off"
                autoFocus
              />
              <p className="text-sm text-muted-foreground">
                Numbers are matched exactly first, then by the first few characters.
                Names are matched exactly, then by similar spelling.
              </p>
            </div>

            <div className="grid gap-4 sm:grid-cols-3">
              <div className="space-y-2">
                <Label htmlFor="father-name">Father&rsquo;s name</Label>
                <Input
                  id="father-name"
                  value={fatherName}
                  onChange={(event) => setFatherName(event.target.value)}
                  placeholder="Narrows a shared name"
                  autoComplete="off"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="date-from">Date from</Label>
                <Input
                  id="date-from"
                  type="date"
                  value={from}
                  onChange={(event) => setFrom(event.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="date-to">Date to</Label>
                <Input
                  id="date-to"
                  type="date"
                  value={to}
                  onChange={(event) => setTo(event.target.value)}
                />
              </div>
            </div>
          </CardContent>
        </Card>

        <SearchResults
          results={data}
          isPending={isPending || (isFetching && !data)}
          error={error}
          offset={offset}
          onOffsetChange={setOffset}
        />
      </div>
    </AppShell>
  );
}
