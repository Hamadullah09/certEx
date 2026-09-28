"use client";

import * as React from "react";
import { Info, Settings as SettingsIcon } from "lucide-react";

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
import {
  OCR_LANGUAGE_OPTIONS,
  settingsFromBatchSettings,
  unknownOcrLanguages,
} from "@/lib/schemas/settings";
import { formatConfidence } from "@/lib/utils";

/*
 * The same tappable option row the upload screen uses: the whole 48px strip is the
 * label and the box itself is 24px, so ticking one does not need a steady hand.
 */
const OPTION_ROW =
  "flex min-h-12 items-center gap-3 rounded-lg px-3 text-base font-medium has-[:disabled]:text-muted-foreground";

function ReadOnlyValue({ children }: { children: React.ReactNode }) {
  return <p className="mt-1.5 text-base font-semibold tabular-nums text-foreground">{children}</p>;
}

/**
 * Workspace settings.
 *
 * Nothing here can be saved yet: the API has no settings route, and a form that
 * looked writable would quietly lose a records officer's work. So the controls are
 * disabled and say why, and the values that *are* readable - the thresholds and OCR
 * languages a batch was created with - are shown with their source named.
 *
 * There are no model or LLM settings on this page, and there is nowhere else in the
 * app for them either: this deployment reads certificates with Tesseract and rules.
 */
export default function SettingsPage() {
  /*
   * The newest batch is the only place the API exposes these values today: a batch
   * summary carries no settings, but its detail does, so the list gives the id and
   * one more request gives the numbers. It is the honest source, and the page says
   * so rather than printing the server's defaults as if they had been read.
   */
  const batches = useBatchList();
  const newestId = batches.data?.pages[0]?.items[0]?.id ?? "";
  const newest = useBatch(newestId);
  const current = settingsFromBatchSettings(newest.data?.settings);
  const isLoading = batches.isPending || (Boolean(newestId) && newest.isPending);

  const languages = current.ocr_languages ?? [];
  const extraLanguages = unknownOcrLanguages(languages);

  return (
    <AppShell>
      <PageHeader
        icon={SettingsIcon}
        title="Settings"
        description="How this workspace reads certificates: when a row is accepted on its own, which languages are recognised, and how long files are kept."
      />

      <Alert className="mt-8">
        <Info aria-hidden="true" />
        <AlertTitle>These settings cannot be changed here yet</AlertTitle>
        <AlertDescription>
          Changing them workspace-wide is coming in a later release. Until then each batch carries
          its own confidence levels and languages, chosen on the screen where the batch is created,
          and retention is set by an administrator on the server.
        </AlertDescription>
      </Alert>

      <Card className="mt-8">
        <CardHeader className="pb-5">
          <CardTitle>When a certificate is accepted without a person</CardTitle>
          <CardDescription>
            Every certificate is scored out of 100. Above the upper level it is accepted on its own;
            below the lower one it is marked as failed instead of being sent for checking.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-6 sm:grid-cols-2">
          <div>
            <Label htmlFor="confidence-auto">Accept on its own at or above</Label>
            {isLoading ? (
              <Skeleton className="mt-1.5 h-12 w-full" />
            ) : current.confidence_auto_approve !== undefined ? (
              <ReadOnlyValue>{formatConfidence(current.confidence_auto_approve)}</ReadOnlyValue>
            ) : (
              <Input
                id="confidence-auto"
                className="mt-1.5"
                disabled
                placeholder="Set on the server"
                aria-describedby="confidence-auto-note"
              />
            )}
            <p id="confidence-auto-note" className="mt-1.5 text-sm text-muted-foreground">
              A score this high means every field was read cleanly and no check failed.
            </p>
          </div>

          <div>
            <Label htmlFor="confidence-floor">Mark as failed below</Label>
            {isLoading ? (
              <Skeleton className="mt-1.5 h-12 w-full" />
            ) : current.confidence_review_floor !== undefined ? (
              <ReadOnlyValue>{formatConfidence(current.confidence_review_floor)}</ReadOnlyValue>
            ) : (
              <Input
                id="confidence-floor"
                className="mt-1.5"
                disabled
                placeholder="Set on the server"
                aria-describedby="confidence-floor-note"
              />
            )}
            <p id="confidence-floor-note" className="mt-1.5 text-sm text-muted-foreground">
              Anything between the two levels is put in front of a person to check.
            </p>
          </div>
        </CardContent>
      </Card>

      <Card className="mt-8">
        <CardHeader className="pb-5">
          <CardTitle>Languages on the scans</CardTitle>
          <CardDescription>
            Which languages the reader looks for when a certificate is a photograph or a scan rather
            than a document with text in it.
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
          {languages.length === 0 && !isLoading ? (
            <p className="mt-2 text-sm text-muted-foreground">
              No batch has been created yet, so there is nothing to show here. The server default is
              English and Urdu together.
            </p>
          ) : null}
        </CardContent>
      </Card>

      <Card className="mt-8">
        <CardHeader className="pb-5">
          <CardTitle>How long files are kept</CardTitle>
          <CardDescription>
            After this many days the uploaded files and their page images are deleted. The extracted
            rows are not affected.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="max-w-xs">
            <Label htmlFor="retention-days">Days</Label>
            <Input
              id="retention-days"
              className="mt-1.5"
              disabled
              placeholder="Set on the server"
              aria-describedby="retention-note"
            />
            <p id="retention-note" className="mt-1.5 text-sm text-muted-foreground">
              Retention is not readable from here. An administrator sets it where the server is
              configured.
            </p>
          </div>
        </CardContent>
      </Card>

      <Separator className="mt-10" tone="subtle" />

      <div className="mt-6 flex flex-wrap items-center gap-4">
        <Button disabled size="lg">
          Save settings
        </Button>
        <p className="text-base text-muted-foreground">
          Saving arrives with the workspace settings route in a later release.
        </p>
      </div>
    </AppShell>
  );
}
