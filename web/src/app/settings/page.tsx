"use client";

import * as React from "react";
import Link from "next/link";
import { toast } from "sonner";
import { Info, Lock, Settings as SettingsIcon, Users } from "lucide-react";

import { AppShell } from "@/components/app-shell";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { useBatch, useBatchList } from "@/hooks/use-batches";
import { useSession } from "@/hooks/use-session";
import {
  useRenameWorkspace,
  useSaveWorkspaceSettings,
  useWorkspaceSettings,
} from "@/hooks/use-review";
import { ApiError } from "@/lib/api";
import type { ReviewSettings } from "@/lib/schemas/review";
import {
  OCR_LANGUAGE_OPTIONS,
  settingsFromBatchSettings,
  unknownOcrLanguages,
} from "@/lib/schemas/settings";

/*
 * The same tappable option row the upload screen uses: the whole 48px strip is the
 * label and the box itself is 24px, so ticking one does not need a steady hand.
 */
const OPTION_ROW =
  "flex min-h-12 items-center gap-3 rounded-lg px-3 text-base font-medium has-[:disabled]:text-muted-foreground";

/** A threshold as a whole number out of 100, which is how the page talks about it. */
function asPercent(value: number): string {
  return String(Math.round(value * 100));
}

function fromPercent(text: string): number | null {
  const parsed = Number(text);
  if (!Number.isFinite(parsed) || parsed < 0 || parsed > 100) return null;
  return parsed / 100;
}

/**
 * Workspace settings.
 *
 * The review thresholds are the office's own judgement, and this is where they are
 * made: an archive digitising fifty-year-old registers accepts more automatically than
 * an office issuing certificates today, and both are right.
 *
 * Only administrators can save. An operator sees the same numbers, read-only, because
 * knowing why a row was routed for checking is part of doing the checking - and the
 * server enforces the same rule regardless of what this page shows.
 *
 * OCR languages and retention are still per-batch and server-side respectively, and
 * the page says so rather than pretending otherwise.
 */
export default function SettingsPage() {
  const { data: session } = useSession();
  const canSave = session?.user.role === "ADMIN";

  const { data: settings, isPending } = useWorkspaceSettings();
  const save = useSaveWorkspaceSettings();
  const rename = useRenameWorkspace();

  const [officeName, setOfficeName] = React.useState("");
  const nameSeeded = React.useRef(false);
  React.useEffect(() => {
    if (nameSeeded.current || !session) return;
    nameSeeded.current = true;
    setOfficeName(session.workspace.name);
  }, [session]);

  const [autoApprove, setAutoApprove] = React.useState("");
  const [floor, setFloor] = React.useState("");
  const [reviewImports, setReviewImports] = React.useState(false);
  const [reviewDuplicates, setReviewDuplicates] = React.useState(true);

  // Seeded once the server's values arrive; typing afterwards is the operator's.
  const seeded = React.useRef(false);
  React.useEffect(() => {
    if (!settings || seeded.current) return;
    seeded.current = true;
    setAutoApprove(asPercent(settings.review.confidence_auto_approve));
    setFloor(asPercent(settings.review.confidence_review_floor));
    setReviewImports(settings.review.review_imported_records);
    setReviewDuplicates(settings.review.review_suspected_duplicates);
  }, [settings]);

  const parsedAuto = fromPercent(autoApprove);
  const parsedFloor = fromPercent(floor);
  const contradictory =
    parsedAuto !== null && parsedFloor !== null && parsedFloor > parsedAuto;
  const usable = parsedAuto !== null && parsedFloor !== null && !contradictory;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (parsedAuto === null || parsedFloor === null) return;
    const review: ReviewSettings = {
      confidence_auto_approve: parsedAuto,
      confidence_review_floor: parsedFloor,
      review_imported_records: reviewImports,
      review_suspected_duplicates: reviewDuplicates,
    };
    save.mutate({ review });
  }

  // The OCR languages are still per batch, and the newest batch is the only place
  // the API exposes them. Shown with their source named rather than as a default.
  const batches = useBatchList();
  const newestId = batches.data?.pages[0]?.items[0]?.id ?? "";
  const newest = useBatch(newestId);
  const batchSettings = settingsFromBatchSettings(newest.data?.settings);
  const languages = batchSettings.ocr_languages ?? [];
  const extraLanguages = unknownOcrLanguages(languages);

  return (
    <AppShell>
      <PageHeader
        icon={SettingsIcon}
        actions={
          canSave ? (
            <Button asChild size="lg" variant="outline">
              <Link href="/settings/users">
                <Users aria-hidden="true" />
                Users
              </Link>
            </Button>
          ) : null
        }
        title="Settings"
        description="What this office is called, and how it reads certificates."
      />

      {!canSave ? (
        <Alert className="mt-8">
          <Lock aria-hidden="true" />
          <AlertTitle>Only an administrator can change these</AlertTitle>
          <AlertDescription>
            They are shown here because knowing where the thresholds sit explains why a
            row was sent for checking.
          </AlertDescription>
        </Alert>
      ) : null}

      {/* First, because it is the one setting a new deployment always needs to change:
          the name it starts with is whatever the install script was given. */}
      <Card className="mt-8">
        <CardHeader className="pb-4">
          <CardTitle>Name of this office</CardTitle>
          <CardDescription>
            Shown at the top of every screen and used in the name of every file you
            download. A new installation starts out called &ldquo;Demo Records
            Office&rdquo; until somebody changes it here.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="max-w-md">
            <Label htmlFor="workspace-name">Name</Label>
            <Input
              id="workspace-name"
              className="mt-1.5"
              placeholder="Union Council 42, Lahore"
              value={officeName}
              disabled={!canSave || rename.isPending}
              onChange={(event) => setOfficeName(event.target.value)}
            />
          </div>
          {canSave ? (
            <Button
              size="lg"
              disabled={
                rename.isPending ||
                !officeName.trim() ||
                officeName.trim() === session?.workspace.name
              }
              onClick={() =>
                rename.mutate(officeName.trim(), {
                  onSuccess: () => toast.success("The name was saved."),
                  onError: (error) =>
                    toast.error(
                      error instanceof ApiError
                        ? error.userMessage
                        : "The name could not be saved.",
                    ),
                })
              }
            >
              {rename.isPending ? "Saving…" : "Save the name"}
            </Button>
          ) : null}
        </CardContent>
      </Card>

      <form onSubmit={submit}>
        <Card className="mt-6">
          <CardHeader className="pb-5">
            <CardTitle>When a reading is accepted without a person</CardTitle>
            <CardDescription>
              Every certificate read by the machine is scored out of 100. At or above the
              upper level it is accepted on its own; below the lower one it is marked as
              unread rather than shown as a value somebody might trust. Anything between
              goes to the review queue.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-6 sm:grid-cols-2">
            <div>
              <Label htmlFor="confidence-auto">Accept on its own at or above</Label>
              {isPending ? (
                <Skeleton className="mt-1.5 h-12 w-full" />
              ) : (
                <Input
                  id="confidence-auto"
                  className="mt-1.5"
                  type="number"
                  min={0}
                  max={100}
                  inputMode="numeric"
                  value={autoApprove}
                  disabled={!canSave}
                  onChange={(event) => setAutoApprove(event.target.value)}
                  aria-describedby="confidence-auto-note"
                />
              )}
              <p id="confidence-auto-note" className="mt-1.5 text-sm text-muted-foreground">
                100 means every reading is checked by a person, which is the safe
                position for an office that has not decided yet.
              </p>
            </div>

            <div>
              <Label htmlFor="confidence-floor">Mark as unread below</Label>
              {isPending ? (
                <Skeleton className="mt-1.5 h-12 w-full" />
              ) : (
                <Input
                  id="confidence-floor"
                  className="mt-1.5"
                  type="number"
                  min={0}
                  max={100}
                  inputMode="numeric"
                  value={floor}
                  disabled={!canSave}
                  onChange={(event) => setFloor(event.target.value)}
                  aria-describedby="confidence-floor-note"
                  aria-invalid={contradictory}
                />
              )}
              <p id="confidence-floor-note" className="mt-1.5 text-sm text-muted-foreground">
                {contradictory
                  ? "This is above the upper level, which would make every reading both failed and accepted."
                  : "A value nobody can read is worse than no value, so below this it is not offered as one."}
              </p>
            </div>
          </CardContent>
        </Card>

        <Card className="mt-8">
          <CardHeader className="pb-5">
            <CardTitle>What else goes to review</CardTitle>
            <CardDescription>
              Beyond the score, two kinds of entry can be queued for a person.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-1">
            <label className={OPTION_ROW}>
              <input
                type="checkbox"
                className="size-6"
                checked={reviewDuplicates}
                disabled={!canSave}
                onChange={(event) => setReviewDuplicates(event.target.checked)}
              />
              A certificate number the register already holds
            </label>
            <p className="px-3 text-sm text-muted-foreground">
              Turning this off does not merge anything. The duplicate is still recorded
              and still shown on both entries; it simply does not queue work.
            </p>

            <label className={OPTION_ROW}>
              <input
                type="checkbox"
                className="size-6"
                checked={reviewImports}
                disabled={!canSave}
                onChange={(event) => setReviewImports(event.target.checked)}
              />
              Records loaded from a spreadsheet
            </label>
            <p className="px-3 text-sm text-muted-foreground">
              They were typed by a person, so by default there is no machine reading to
              check.
            </p>
          </CardContent>
        </Card>

        {save.error ? (
          <Alert variant="destructive" className="mt-8">
            <AlertTitle>The settings were not saved</AlertTitle>
            <AlertDescription>
              {save.error instanceof ApiError
                ? (save.error.remediation ?? save.error.message)
                : save.error.message}
            </AlertDescription>
          </Alert>
        ) : null}

        {save.isSuccess ? (
          <Alert variant="success" className="mt-8">
            <AlertTitle>Saved</AlertTitle>
            <AlertDescription>
              These levels apply to every certificate read from now on. Entries already
              in the register are not re-routed.
            </AlertDescription>
          </Alert>
        ) : null}

        <Separator className="mt-10" tone="subtle" />

        <div className="mt-6 flex flex-wrap items-center gap-4">
          <Button type="submit" size="lg" disabled={!canSave || !usable || save.isPending}>
            Save settings
          </Button>
          {!canSave ? (
            <p className="text-base text-muted-foreground">
              Ask an administrator to change these.
            </p>
          ) : null}
        </div>
      </form>

      <Card className="mt-10">
        <CardHeader className="pb-5">
          <CardTitle>Languages on the scans</CardTitle>
          <CardDescription>
            Which languages the reader looks for when a certificate is a photograph or a
            scan rather than a document with text in it. Still chosen per batch, on the
            screen where the batch is created.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <fieldset disabled className="space-y-1">
            <legend className="sr-only">OCR languages</legend>
            {OCR_LANGUAGE_OPTIONS.map((option) => (
              <label key={option.value} className={OPTION_ROW}>
                <input
                  type="checkbox"
                  className="size-6"
                  disabled
                  checked={languages.includes(option.value)}
                  readOnly
                />
                {option.label}
              </label>
            ))}
          </fieldset>
          {extraLanguages.length > 0 ? (
            <p className="mt-2 text-sm text-muted-foreground">
              Also set on the server: {extraLanguages.join(", ")}
            </p>
          ) : null}
          {languages.length === 0 ? (
            <p className="mt-2 text-sm text-muted-foreground">
              No batch has been created yet, so there is nothing to show here. The server
              default is English and Urdu together.
            </p>
          ) : null}
        </CardContent>
      </Card>

      <Alert className="mt-8">
        <Info aria-hidden="true" />
        <AlertTitle>How long files are kept</AlertTitle>
        <AlertDescription>
          Retention is set where the server is configured, not from here, because it
          deletes files rather than changing how they are read.
        </AlertDescription>
      </Alert>
    </AppShell>
  );
}
