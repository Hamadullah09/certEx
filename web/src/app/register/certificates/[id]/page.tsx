"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  AlertTriangle,
  ArrowRightLeft,
  ChevronLeft,
  CircleAlert,
  Copy,
  FileText,
  ScrollText,
} from "lucide-react";

import { AppShell } from "@/components/app-shell";
import { ReviewActions } from "@/components/register/review-actions";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useCertificate, useDuplicateCandidates, useSchemaVersion } from "@/hooks/use-register";
import { apiBaseUrl, ApiError } from "@/lib/api";
import { COMMON_FIELDS } from "@/lib/schemas/fields.generated";
import type { DocumentLink, DuplicateCandidate } from "@/lib/schemas/certificates";
import { valueTextAttributes } from "@/lib/text-direction";

/**
 * What to call a field on screen.
 *
 * In order of authority: the schema the entry was filed under, then the built-in
 * fields this build knows, then the machine name tidied up. The last one is a fallback
 * and looks like one - it is there so an unknown field still appears rather than being
 * dropped, which is the one outcome a register cannot have.
 */
function labelFor(name: string, fromSchema: Map<string, string>): string {
  const defined = fromSchema.get(name);
  if (defined) return defined;
  const builtIn = COMMON_FIELDS.find((spec) => spec.name === name);
  if (builtIn) return builtIn.label;
  const spaced = name.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/**
 * The order the certificate itself is laid out in.
 *
 * `values` is a JSON object, so its key order is whatever the pipeline happened to
 * write. Reading a birth certificate that starts at "sex" and puts the child's name
 * seventh is needlessly hard, and the schema already knows the order a clerk expects.
 */
function inSchemaOrder(names: string[], order: Map<string, number>): string[] {
  return [...names].sort((left, right) => {
    const leftPosition = order.get(left);
    const rightPosition = order.get(right);
    if (leftPosition !== undefined && rightPosition !== undefined) {
      return leftPosition - rightPosition;
    }
    // Anything the schema does not mention sorts after everything it does.
    if (leftPosition !== undefined) return -1;
    if (rightPosition !== undefined) return 1;
    return left.localeCompare(right);
  });
}

function displayDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const parsed = new Date(iso.length > 10 ? iso : `${iso}T00:00:00`);
  return Number.isNaN(parsed.valueOf())
    ? iso
    : parsed.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/** A confidence, as a percentage, or nothing for a record a person typed. */
function confidenceLabel(value: number | undefined): string | null {
  if (value === undefined) return null;
  return `${Math.round(value * 100)}%`;
}

/**
 * One field of the entry.
 *
 * The provenance line is the point of this screen: a value read by a machine has to
 * say so, and say what it read and how sure it was, or a clerk has no way to decide
 * whether to trust it against the paper in their hand.
 */
function ValueRow({
  name,
  value,
  confidence,
  provenance,
}: {
  name: string;
  value: string;
  confidence: number | undefined;
  provenance: Record<string, unknown> | undefined;
}) {
  // Key names as the pipeline writes them. They were read here under different names -
  // "text" and "page" - which no stage has ever produced, so the line below silently
  // stayed empty on every value.
  const method = typeof provenance?.method === "string" ? provenance.method : null;
  const snippet = typeof provenance?.snippet === "string" ? provenance.snippet : null;
  const page = typeof provenance?.page_number === "number" ? provenance.page_number : null;
  const score = confidenceLabel(confidence);

  return (
    <div className="border-b border-border py-3 last:border-0">
      <dt className="text-base font-semibold text-muted-foreground">{name}</dt>
      <dd className="mt-1 space-y-1">
        <p className="text-lg text-foreground" {...valueTextAttributes(value)}>
          {value}
        </p>
        {method || score || page ? (
          <p className="text-sm text-muted-foreground">
            {method ? <span>Read by {method.toLowerCase().replace(/_/g, " ")}</span> : null}
            {page ? <span> · page {page}</span> : null}
            {score ? <span> · confidence {score}</span> : null}
            {snippet && snippet !== value ? (
              <span> · printed as &ldquo;{snippet}&rdquo;</span>
            ) : null}
          </p>
        ) : null}
      </dd>
    </div>
  );
}

function DocumentRow({
  certificateId,
  link,
}: {
  certificateId: string;
  link: DocumentLink;
}) {
  const superseded = link.kind === "SUPERSEDED";
  const pages =
    link.page_start && link.page_end
      ? link.page_start === link.page_end
        ? `page ${link.page_start}`
        : `pages ${link.page_start}–${link.page_end}`
      : null;

  return (
    <li className="flex flex-wrap items-center justify-between gap-3 border-b border-border py-3 last:border-0">
      <div className="min-w-0">
        <p className="text-base font-semibold text-foreground">
          {link.kind === "PRIMARY" ? "The certificate" : link.kind.toLowerCase()}
          {pages ? <span className="text-muted-foreground"> · {pages}</span> : null}
        </p>
        {superseded ? (
          <p className="text-sm text-muted-foreground">
            A better scan replaced this one. It is kept because values in the register
            were read from it.
          </p>
        ) : null}
        {link.note ? <p className="text-sm text-muted-foreground">{link.note}</p> : null}
      </div>
      {/* Opened through the API, which checks the session and records who looked.
          There is no URL for this file that works without one. */}
      <Button variant="outline" asChild>
        <a
          href={`${apiBaseUrl()}/api/v1/certificates/${certificateId}/documents/${link.id}/content`}
          target="_blank"
          rel="noreferrer"
        >
          <FileText aria-hidden="true" />
          Open scan
        </a>
      </Button>
    </li>
  );
}

function Duplicates({
  candidates,
}: {
  candidates: readonly DuplicateCandidate[];
}) {
  if (candidates.length === 0) return null;

  return (
    <Alert variant="warning">
      <Copy aria-hidden="true" />
      <AlertTitle>
        {candidates.length === 1
          ? "Another entry may be the same certificate"
          : `${candidates.length} other entries may be the same certificate`}
      </AlertTitle>
      <AlertDescription>
        <p className="mb-2">
          Nothing has been merged. A person decides whether these are one record or two.
        </p>
        <ul className="space-y-1">
          {candidates.map((candidate) => (
            <li key={candidate.certificate_id}>
              <Link
                href={`/register/certificates/${candidate.certificate_id}`}
                className="font-mono underline underline-offset-4"
              >
                {candidate.certificate_number}
              </Link>
              <span>
                {candidate.primary_name ? ` · ${candidate.primary_name}` : ""}
                {candidate.event_date ? ` · ${displayDate(candidate.event_date)}` : ""}
                {` · matched on ${candidate.reason.replace(/_/g, " ")}`}
              </span>
            </li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}

/**
 * One register entry in full.
 *
 * Ordered the way a clerk reads it: what it is, then whether to trust it, then the
 * values with their provenance, then the scan it came from.
 */
export default function CertificateDetailPage() {
  const params = useParams<{ id: string }>();
  const { data: certificate, isPending, error } = useCertificate(params.id);
  const { data: candidates } = useDuplicateCandidates(params.id);
  const { data: schema } = useSchemaVersion(certificate?.schema_version_id);

  const labels = React.useMemo(
    () => new Map((schema?.fields ?? []).map((definition) => [definition.name, definition.label])),
    [schema],
  );
  const order = React.useMemo(
    () => new Map((schema?.fields ?? []).map((definition, index) => [definition.name, index])),
    [schema],
  );

  if (error) {
    return (
      <AppShell>
        <Alert variant="destructive">
          <AlertTriangle aria-hidden="true" />
          <AlertTitle>This entry could not be opened</AlertTitle>
          <AlertDescription>
            {error instanceof ApiError
              ? (error.remediation ?? error.message)
              : "Check the connection and try again."}
          </AlertDescription>
        </Alert>
      </AppShell>
    );
  }

  if (isPending || !certificate) {
    return (
      <AppShell>
        <div className="space-y-5">
          <Skeleton className="h-10 w-40" />
          <Skeleton className="h-24 w-full max-w-xl" />
          <Skeleton className="h-96 w-full rounded-xl" />
        </div>
      </AppShell>
    );
  }

  const fieldNames = inSchemaOrder(Object.keys(certificate.values), order);

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
          icon={ScrollText}
          title={certificate.primary_name ?? certificate.certificate_number}
          description={`Certificate ${certificate.certificate_number}${
            certificate.event_date ? ` · ${displayDate(certificate.event_date)}` : ""
          }`}
          actions={
            <div className="flex flex-wrap items-center gap-2">
              {certificate.needs_review ? (
                <Badge variant="warning" className="gap-1">
                  <CircleAlert aria-hidden="true" className="size-4" />
                  Needs review
                </Badge>
              ) : null}
              {certificate.status !== "ACTIVE" ? (
                <Badge variant="danger">{certificate.status.toLowerCase()}</Badge>
              ) : null}
              <Badge variant="outline">{certificate.source.toLowerCase()}</Badge>
            </div>
          }
        />

        {certificate.superseded_by_id ? (
          <Alert variant="warning">
            <ArrowRightLeft aria-hidden="true" />
            <AlertTitle>This entry has been replaced</AlertTitle>
            <AlertDescription>
              A reviewer decided this and another entry were the same certificate.{" "}
              <Link
                href={`/register/certificates/${certificate.superseded_by_id}`}
                className="underline underline-offset-4"
              >
                Open the entry that replaced it
              </Link>
              . This one is kept because somebody may be holding a copy of it.
            </AlertDescription>
          </Alert>
        ) : null}

        <Duplicates candidates={candidates ?? []} />

        <Card>
          <CardHeader className="pb-2">
            <CardTitle>What the certificate says</CardTitle>
            <CardDescription>
              {certificate.source === "EXTRACTION"
                ? "Read from the scan below. Each value says how it was read and how sure the reading was."
                : "Entered by hand, so there is no machine reading to check."}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {fieldNames.length > 0 ? (
              <dl>
                {fieldNames.map((name) => (
                  <ValueRow
                    key={name}
                    name={labelFor(name, labels)}
                    value={certificate.values[name] ?? ""}
                    confidence={certificate.confidences[name]}
                    provenance={certificate.provenance[name]}
                  />
                ))}
              </dl>
            ) : (
              <p className="text-base text-muted-foreground">
                This entry carries no values, which should not happen. Report it.
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle>Documents</CardTitle>
            <CardDescription>
              Linked to this entry by record, not by filename, so renaming a file cannot
              detach it.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {certificate.documents.length > 0 ? (
              <ul>
                {certificate.documents.map((link) => (
                  <DocumentRow key={link.id} certificateId={certificate.id} link={link} />
                ))}
              </ul>
            ) : (
              <p className="text-base text-muted-foreground">
                No scan is attached. This entry was typed in or imported from a
                spreadsheet.
              </p>
            )}
          </CardContent>
        </Card>

        <ReviewActions certificate={certificate} candidates={candidates ?? []} />

        <Card>
          <CardHeader className="pb-2">
            <CardTitle>Record details</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-3 sm:grid-cols-2">
              <div>
                <dt className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  Version
                </dt>
                <dd className="text-base text-foreground">{certificate.record_version}</dd>
              </div>
              <div>
                <dt className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  Filed
                </dt>
                <dd className="text-base text-foreground">
                  {displayDate(certificate.created_at)}
                </dd>
              </div>
              <div>
                <dt className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  Last changed
                </dt>
                <dd className="text-base text-foreground">
                  {displayDate(certificate.updated_at)}
                </dd>
              </div>
              <div>
                <dt className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  Registered
                </dt>
                <dd className="text-base text-foreground">
                  {displayDate(certificate.registration_date)}
                </dd>
              </div>
            </dl>
          </CardContent>
        </Card>
      </div>
    </AppShell>
  );
}
