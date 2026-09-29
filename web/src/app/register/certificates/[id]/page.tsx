"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  AlertTriangle,
  ChevronLeft,
  CircleAlert,
  Copy,
  FileText,
  ScrollText,
} from "lucide-react";

import { AppShell } from "@/components/app-shell";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useCertificate, useDuplicateCandidates } from "@/hooks/use-register";
import { apiBaseUrl, ApiError } from "@/lib/api";
import type { CertificateDetail, DocumentLink } from "@/lib/schemas/certificates";
import { valueTextAttributes } from "@/lib/text-direction";

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
  provenance: Record<string, string | number | null> | undefined;
}) {
  const method = typeof provenance?.method === "string" ? provenance.method : null;
  const snippet = typeof provenance?.text === "string" ? provenance.text : null;
  const page = typeof provenance?.page === "number" ? provenance.page : null;
  const score = confidenceLabel(confidence);

  return (
    <div className="border-b border-border py-3 last:border-0">
      <dt className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        {name.replace(/_/g, " ")}
      </dt>
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

function Duplicates({ certificate }: { certificate: CertificateDetail }) {
  const { data: candidates } = useDuplicateCandidates(certificate.id, {
    enabled: certificate.duplicate_status !== "NONE",
  });
  if (!candidates || candidates.length === 0) return null;

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

  const fieldNames = Object.keys(certificate.values);

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

        <Duplicates certificate={certificate} />

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
                    name={name}
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

        <Card>
          <CardHeader className="pb-2">
            <CardTitle>Record history</CardTitle>
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
