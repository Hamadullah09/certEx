"use client";

import Link from "next/link";
import { AlertTriangle, ArrowRight, BookOpen, ClipboardCheck } from "lucide-react";

import { AppShell } from "@/components/app-shell";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useCertificateTypes } from "@/hooks/use-register";
import { useReviewSummary } from "@/hooks/use-review";
import { ApiError } from "@/lib/api";
import type { CertificateTypeSummary } from "@/lib/schemas/registry";

/** The queue, with what is in it, so a reviewer can see the work without opening it. */
function ReviewQueueLink() {
  const { data: summary } = useReviewSummary();
  const waiting = summary?.needs_review ?? 0;
  return (
    <Button variant={waiting > 0 ? "default" : "outline"} asChild>
      <Link href="/register/review">
        <ClipboardCheck aria-hidden="true" />
        Review
        {waiting > 0 ? (
          <Badge variant="outline" className="ml-2">
            {waiting}
          </Badge>
        ) : null}
      </Link>
    </Button>
  );
}

function TypeCard({ type }: { type: CertificateTypeSummary }) {
  return (
    <li>
      <Link
        href={`/register/${type.id}`}
        className="group block h-full rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        <Card className="h-full transition-colors group-hover:border-primary">
          <CardHeader className="gap-2">
            <div className="flex items-start justify-between gap-3">
              <CardTitle className="text-2xl">{type.name}</CardTitle>
              <ArrowRight
                aria-hidden="true"
                className="mt-1 size-6 shrink-0 text-muted-foreground group-hover:text-primary"
              />
            </div>
            <CardDescription>
              {type.description ?? `Search and open ${type.name.toLowerCase()} records.`}
            </CardDescription>
          </CardHeader>
        </Card>
      </Link>
    </li>
  );
}

/**
 * The register: one card per certificate type this office holds.
 *
 * The list comes from the server, not from a constant in this file. Birth, Marriage
 * and Death are seeded for a new workspace and are what most offices will ever use,
 * but an administrator can add a type and it appears here the moment they do - which
 * is the whole point of the types being data rather than code.
 */
export default function RegisterPage() {
  const { data: types, isPending, error } = useCertificateTypes();

  return (
    <AppShell>
      <PageHeader
        icon={BookOpen}
        title="Register"
        description="Find a certificate by its number, or by the names on it."
        actions={<ReviewQueueLink />}
      />

      {error ? (
        <Alert variant="destructive">
          <AlertTriangle aria-hidden="true" />
          <AlertTitle>The register could not be read</AlertTitle>
          <AlertDescription>
            {error instanceof ApiError
              ? (error.remediation ?? error.message)
              : "Check the connection and try again."}
          </AlertDescription>
        </Alert>
      ) : null}

      {isPending ? (
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((index) => (
            <li key={index}>
              <Skeleton className="h-36 w-full rounded-xl" />
            </li>
          ))}
        </ul>
      ) : null}

      {types && types.length > 0 ? (
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {types.map((type) => (
            <TypeCard key={type.id} type={type} />
          ))}
        </ul>
      ) : null}

      {types && types.length === 0 ? (
        <Card>
          <CardHeader className="gap-2">
            <CardTitle className="flex items-center gap-2">
              <BookOpen aria-hidden="true" className="size-5" />
              No certificate types yet
            </CardTitle>
            <CardDescription>
              An administrator defines what kinds of certificate this office keeps, and
              which fields each one carries, before records can be filed.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-base text-muted-foreground">
              Ask an administrator to add a certificate type in Settings.
            </p>
          </CardContent>
        </Card>
      ) : null}
    </AppShell>
  );
}
