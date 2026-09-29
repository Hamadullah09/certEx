"use client";

import * as React from "react";
import { Check, History, Pencil } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  useApproveCertificate,
  useCertificateHistory,
  useCorrectCertificate,
  useResolveDuplicate,
} from "@/hooks/use-review";
import { ApiError } from "@/lib/api";
import type { CertificateDetail, DuplicateCandidate } from "@/lib/schemas/certificates";
import { REVISION_ACTION_LABEL } from "@/lib/schemas/review";
import { valueTextAttributes } from "@/lib/text-direction";

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function problem(error: Error | null): string | null {
  if (!error) return null;
  return error instanceof ApiError ? (error.remediation ?? error.message) : error.message;
}

/**
 * Correcting one field.
 *
 * One field at a time, and a reason with it. A form that submitted the whole record
 * would quietly overwrite whatever a second reviewer had just fixed in another tab,
 * and a correction with no reason is indistinguishable from a mistake six months
 * later - which is why the note is required here as well as on the server.
 */
function CorrectionForm({
  certificate,
  onDone,
}: {
  certificate: CertificateDetail;
  onDone: () => void;
}) {
  const fields = Object.keys(certificate.values);
  const [field, setField] = React.useState(fields[0] ?? "");
  const [value, setValue] = React.useState(certificate.values[fields[0] ?? ""] ?? "");
  const [note, setNote] = React.useState("");
  const correct = useCorrectCertificate(certificate.id);

  function choose(name: string) {
    setField(name);
    setValue(certificate.values[name] ?? "");
  }

  const unchanged = (certificate.values[field] ?? "") === value;

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        correct.mutate(
          { values: { [field]: value }, note },
          { onSuccess: onDone },
        );
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="correct-field">Field</Label>
          <select
            id="correct-field"
            value={field}
            onChange={(event) => choose(event.target.value)}
            className="h-11 w-full rounded-lg border-2 border-input bg-background px-3 text-base"
          >
            {fields.map((name) => (
              <option key={name} value={name}>
                {name.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="correct-value">Correct value</Label>
          <Input
            id="correct-value"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            {...valueTextAttributes(value)}
          />
          <p className="text-sm text-muted-foreground">
            Leave it empty to clear the field.
          </p>
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="correct-note">Why</Label>
        <Input
          id="correct-note"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="The scan reads Malick with a c"
          required
        />
      </div>

      {correct.error ? (
        <Alert variant="destructive">
          <AlertTitle>The correction was not saved</AlertTitle>
          <AlertDescription>{problem(correct.error)}</AlertDescription>
        </Alert>
      ) : null}

      <div className="flex items-center gap-3">
        <Button type="submit" disabled={correct.isPending || !note.trim() || unchanged}>
          Save correction
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

/**
 * Settling whether two entries are one certificate.
 *
 * Both answers are recorded and neither deletes anything. Saying they are the same
 * marks the later entry superseded and points it at the earlier one; saying they are
 * different stops the pair being raised again.
 */
function DuplicateDecision({
  certificate,
  candidate,
}: {
  certificate: CertificateDetail;
  candidate: DuplicateCandidate;
}) {
  const [note, setNote] = React.useState("");
  const resolve = useResolveDuplicate(certificate.id);

  return (
    <div className="space-y-3 border-t border-border pt-4">
      <div className="space-y-2">
        <Label htmlFor={`dup-note-${candidate.certificate_id}`}>
          Note (optional)
        </Label>
        <Input
          id={`dup-note-${candidate.certificate_id}`}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Two offices reused the number"
        />
      </div>

      {resolve.error ? (
        <Alert variant="destructive">
          <AlertTitle>The decision was not recorded</AlertTitle>
          <AlertDescription>{problem(resolve.error)}</AlertDescription>
        </Alert>
      ) : null}

      <div className="flex flex-wrap items-center gap-3">
        <Button
          disabled={resolve.isPending}
          onClick={() =>
            resolve.mutate({
              otherId: candidate.certificate_id,
              sameCertificate: true,
              note: note || undefined,
            })
          }
        >
          They are one certificate
        </Button>
        <Button
          variant="outline"
          disabled={resolve.isPending}
          onClick={() =>
            resolve.mutate({
              otherId: candidate.certificate_id,
              sameCertificate: false,
              note: note || undefined,
            })
          }
        >
          They are different certificates
        </Button>
      </div>
      <p className="text-sm text-muted-foreground">
        Neither answer deletes anything. Both entries stay in the register and stay
        findable.
      </p>
    </div>
  );
}

export function ReviewActions({
  certificate,
  candidates,
}: {
  certificate: CertificateDetail;
  candidates: readonly DuplicateCandidate[];
}) {
  const [correcting, setCorrecting] = React.useState(false);
  const approve = useApproveCertificate(certificate.id);
  const { data: revisions } = useCertificateHistory(certificate.id);

  const blocked = certificate.duplicate_status === "SUSPECTED";
  // The strongest candidate first, which is the order the server returns them in.
  const first = candidates.at(0);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="pb-3">
          <CardTitle>Review</CardTitle>
          <CardDescription>
            {certificate.needs_review
              ? "This entry is waiting for somebody to accept it or correct it."
              : "This entry has been accepted. A correction is still possible."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {correcting ? (
            <CorrectionForm
              certificate={certificate}
              onDone={() => setCorrecting(false)}
            />
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              {certificate.needs_review ? (
                <Button
                  disabled={approve.isPending || blocked}
                  onClick={() => approve.mutate({})}
                >
                  <Check aria-hidden="true" />
                  Accept as it stands
                </Button>
              ) : null}
              <Button variant="outline" onClick={() => setCorrecting(true)}>
                <Pencil aria-hidden="true" />
                Correct a value
              </Button>
            </div>
          )}

          {blocked ? (
            <p className="text-base text-muted-foreground">
              The duplicate question below has to be settled before this entry can be
              accepted.
            </p>
          ) : null}

          {approve.error ? (
            <Alert variant="destructive">
              <AlertTitle>Not accepted</AlertTitle>
              <AlertDescription>{problem(approve.error)}</AlertDescription>
            </Alert>
          ) : null}
        </CardContent>
      </Card>

      {first ? (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle>Settle the duplicate</CardTitle>
            <CardDescription>
              Comparing this entry with {first.certificate_number}
              {first.primary_name ? ` (${first.primary_name})` : ""}.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <DuplicateDecision certificate={certificate} candidate={first} />
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2">
            <History aria-hidden="true" className="size-5" />
            History
          </CardTitle>
          <CardDescription>
            Everything that has happened to this entry, oldest first. Nothing is ever
            removed.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {revisions && revisions.length > 0 ? (
            <ol className="space-y-3">
              {revisions.map((revision) => (
                <li
                  key={revision.id}
                  className="border-b border-border pb-3 last:border-0 last:pb-0"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline">v{revision.record_version}</Badge>
                    <span className="text-base font-semibold text-foreground">
                      {REVISION_ACTION_LABEL[revision.action]}
                    </span>
                    <span className="text-sm text-muted-foreground">
                      {when(revision.created_at)}
                    </span>
                  </div>
                  {revision.changed_fields.length > 0 ? (
                    <p className="mt-1 text-sm text-muted-foreground">
                      Changed {revision.changed_fields.map((name) => name.replace(/_/g, " ")).join(", ")}
                    </p>
                  ) : null}
                  {revision.note ? (
                    <p className="mt-1 text-base text-foreground">{revision.note}</p>
                  ) : null}
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-base text-muted-foreground">Nothing recorded yet.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
