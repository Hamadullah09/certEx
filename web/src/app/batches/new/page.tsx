"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  CheckCircle2,
  Copy,
  FilePlus2,
  FileText,
  KeyRound,
  Loader2,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { AppShell } from "@/components/app-shell";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { type UploadItem, useBatchUpload, useCreateBatch } from "@/hooks/use-batches";
import { useWorkspaceSettings } from "@/hooks/use-review";
import { ApiError } from "@/lib/api";
import {
  ACCEPTED_EXTENSIONS,
  type CertificateType,
  type FileRejection,
  MAX_BATCH_FILES,
  screenFiles,
} from "@/lib/schemas/batches";
import { needsPassword } from "@/lib/upload";
import { formatBytes } from "@/lib/utils";

import { UploadDropzone } from "./dropzone";

const CERTIFICATE_TYPES: { value: CertificateType; label: string }[] = [
  { value: "BIRTH", label: "Birth" },
  { value: "MARRIAGE", label: "Marriage" },
  { value: "DEATH", label: "Death" },
  { value: "OTHER", label: "Other" },
];

const OCR_LANGUAGES: { value: string; label: string }[] = [
  { value: "eng", label: "English" },
  { value: "urd", label: "Urdu" },
];

/*
 * One tappable row per option: the whole 48px strip is the label, it tints when
 * checked, and the box itself is 24px rather than 16px. Ticking a box should not
 * need a steady hand.
 */
const OPTION_ROW =
  "flex min-h-12 cursor-pointer items-center gap-3 rounded-lg px-3 text-base font-medium transition-colors hover:bg-accent hover:text-accent-foreground has-[:checked]:bg-primary-surface has-[:checked]:text-primary-surface-foreground has-[:disabled]:cursor-not-allowed has-[:disabled]:text-muted-foreground has-[:disabled]:hover:bg-transparent";

/** Inline password entry for a PDF the server could not open. */
function PasswordRetry({
  item,
  disabled,
  onRetry,
}: {
  item: UploadItem;
  disabled: boolean;
  onRetry: (password: string) => Promise<void>;
}) {
  const [password, setPassword] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const inputId = React.useId();

  async function submit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!password) return;
    setBusy(true);
    try {
      await onRetry(password);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.userMessage : "Could not retry the upload.");
    } finally {
      setBusy(false);
      setPassword("");
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="mt-3 flex flex-wrap items-center gap-3">
      <Label htmlFor={inputId} className="sr-only">
        Password for {item.file.name}
      </Label>
      <Input
        id={inputId}
        type="password"
        autoComplete="off"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        placeholder="PDF password"
        className="max-w-64"
        disabled={disabled || busy}
      />
      <Button type="submit" variant="outline" disabled={disabled || busy || !password}>
        {busy ? <Loader2 aria-hidden="true" className="animate-spin" /> : <KeyRound aria-hidden="true" />}
        Unlock and retry
      </Button>
    </form>
  );
}

/**
 * Used only for the instant before the office's settings arrive, and matched to the
 * server's own defaults so the form never shows a policy nobody chose.
 */
const DEFAULT_AUTO_APPROVE = 0.9;
const DEFAULT_REVIEW_FLOOR = 0.6;

export default function NewBatchPage() {
  const router = useRouter();
  const createBatch = useCreateBatch();
  const { data: workspaceSettings } = useWorkspaceSettings();

  const [name, setName] = React.useState("");
  const [expectedTypes, setExpectedTypes] = React.useState<CertificateType[]>([]);
  const [languages, setLanguages] = React.useState<string[]>(["eng", "urd"]);
  // Seeded from the office's own settings rather than from a number chosen here.
  // These two decide what gets filed without anybody looking at it, and an office that
  // has set "check everything" means it - a form that quietly proposed 90% instead
  // would hand that decision back to whoever happens to be uploading.
  const [autoApprove, setAutoApprove] = React.useState(DEFAULT_AUTO_APPROVE);
  const [reviewFloor, setReviewFloor] = React.useState(DEFAULT_REVIEW_FLOOR);
  const seeded = React.useRef(false);
  React.useEffect(() => {
    if (!workspaceSettings || seeded.current) return;
    seeded.current = true;
    setAutoApprove(workspaceSettings.review.confidence_auto_approve);
    setReviewFloor(workspaceSettings.review.confidence_review_floor);
  }, [workspaceSettings]);
  const [rejections, setRejections] = React.useState<FileRejection[]>([]);
  const [batchId, setBatchId] = React.useState<string | null>(null);

  const upload = useBatchUpload(batchId);

  const handleDrop = React.useCallback(
    (incoming: File[]) => {
      const { accepted, rejected } = screenFiles(incoming, upload.items);
      if (accepted.length) upload.addFiles(accepted);
      if (rejected.length) {
        setRejections((current) => [...current, ...rejected].slice(-25));
        toast.warning(
          `${rejected.length} file${rejected.length === 1 ? "" : "s"} could not be added`,
        );
      }
    },
    [upload],
  );

  const thresholdsValid = reviewFloor <= autoApprove;
  const hasPending = upload.items.some((item) => item.phase !== "done" && item.phase !== "error");
  const canStart =
    name.trim().length > 0 &&
    upload.items.length > 0 &&
    thresholdsValid &&
    !upload.isUploading &&
    (batchId === null || hasPending);

  async function handleStart(): Promise<void> {
    if (!canStart) return;

    try {
      let id = batchId;
      if (!id) {
        const created = await createBatch.mutateAsync({
          name: name.trim(),
          settings: {
            expected_types: expectedTypes.length ? expectedTypes : null,
            ocr_languages: languages.join("+"),
            confidence_auto_approve: autoApprove,
            confidence_review_floor: reviewFloor,
          },
        });
        id = created.id;
        setBatchId(id);
      }
      // The id is passed explicitly: state set a moment ago is not visible to this
      // handler yet, which is exactly how the first click used to upload nothing.
      await upload.start(id);
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.userMessage : "Could not create the batch.",
      );
    }
  }

  const allDone =
    upload.items.length > 0 &&
    !upload.isUploading &&
    upload.items.every((item) => item.phase === "done" || item.phase === "error");

  return (
    <AppShell>
      <PageHeader
        icon={FilePlus2}
        title="New batch"
        description="Add certificates as PDF, Word, images or a ZIP. Scanned pages are read automatically."
        actions={
          <Button variant="outline" onClick={() => router.push("/")}>
            Cancel
          </Button>
        }
      />

      <div className="mt-9 grid gap-7 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="space-y-7">
          <Card>
            <CardHeader>
              <CardTitle>Step 1 · Name this batch</CardTitle>
              <CardDescription>
                How this batch appears in the list and in exported filenames.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Label htmlFor="batch-name" className="sr-only">
                Batch name
              </Label>
              <Input
                id="batch-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Lahore birth records 2019"
                maxLength={200}
                disabled={Boolean(batchId)}
                autoFocus
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Step 2 · Add the files</CardTitle>
              <CardDescription>
                Drag a folder of scans straight in, or browse for them.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <UploadDropzone onDrop={handleDrop} disabled={upload.isUploading} />
            </CardContent>
          </Card>

          {rejections.length > 0 ? (
            <Alert variant="warning">
              <AlertCircle aria-hidden="true" />
              <AlertTitle>
                {rejections.length} file{rejections.length === 1 ? "" : "s"} were not added
              </AlertTitle>
              <AlertDescription>
                <ul className="mt-2 space-y-1.5">
                  {rejections.slice(-5).map((rejection, index) => (
                    <li key={`${rejection.file.name}-${index}`} className="text-base">
                      <span className="font-semibold">{rejection.file.name}</span> —{" "}
                      {rejection.message}
                    </li>
                  ))}
                </ul>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-4"
                  onClick={() => setRejections([])}
                >
                  Dismiss
                </Button>
              </AlertDescription>
            </Alert>
          ) : null}

          {upload.items.length > 0 ? (
            <Card>
              <CardHeader className="flex-row items-center justify-between gap-4">
                <div className="min-w-0">
                  <CardTitle>
                    {upload.items.length.toLocaleString()} file
                    {upload.items.length === 1 ? "" : "s"} ready
                  </CardTitle>
                  <CardDescription className="mt-2">
                    {formatBytes(upload.stats.bytes)} total
                    {upload.stats.done > 0 ? ` · ${upload.stats.done} uploaded` : ""}
                    {upload.stats.duplicates > 0
                      ? ` · ${upload.stats.duplicates} duplicate`
                      : ""}
                    {upload.stats.failed > 0 ? ` · ${upload.stats.failed} rejected` : ""}
                  </CardDescription>
                </div>
                {!upload.isUploading && !batchId ? (
                  <Button variant="outline" size="sm" onClick={upload.clear}>
                    <Trash2 aria-hidden="true" />
                    Clear
                  </Button>
                ) : null}
              </CardHeader>
              <CardContent className="max-h-[36rem] overflow-y-auto p-0">
                <ul className="divide-y divide-border-subtle border-t border-border-subtle">
                  {upload.items.map((item) => (
                    <li key={item.id} className="flex items-start gap-4 px-7 py-4">
                      <FileText
                        aria-hidden="true"
                        className="mt-1 size-6 shrink-0 text-muted-foreground"
                      />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-base font-medium">{item.file.name}</p>
                        <div className="mt-2 flex items-center gap-3">
                          <Progress
                            value={item.progress * 100}
                            label={`${item.file.name} upload progress`}
                            tone={
                              item.phase === "error"
                                ? "danger"
                                : item.phase === "done"
                                  ? "success"
                                  : "default"
                            }
                            className="h-2.5"
                          />
                          <span className="w-20 shrink-0 text-right text-sm tabular-nums text-muted-foreground">
                            {formatBytes(item.file.size)}
                          </span>
                        </div>
                        {item.error ? (
                          <div className="mt-2" role="status">
                            <p className="text-base font-semibold text-destructive">
                              {item.error}
                            </p>
                            {item.remediation ? (
                              <p className="mt-0.5 text-base text-muted-foreground">
                                {item.remediation}
                              </p>
                            ) : null}
                          </div>
                        ) : null}
                        {item.phase === "error" && needsPassword(item.errorCode) ? (
                          <PasswordRetry
                            item={item}
                            disabled={upload.isUploading}
                            onRetry={(password) => upload.retryWithPassword(item.id, password)}
                          />
                        ) : null}
                        {item.result?.is_duplicate ? (
                          <p className="mt-2 flex items-center gap-2 text-base text-muted-foreground">
                            <Copy aria-hidden="true" className="size-5 shrink-0" />
                            Already in this workspace — reusing the earlier reading.
                          </p>
                        ) : null}
                        {item.result && item.result.children.length > 0 ? (
                          <p className="mt-2 text-base text-muted-foreground">
                            Unpacked {item.result.children.length} document
                            {item.result.children.length === 1 ? "" : "s"} from the archive.
                          </p>
                        ) : null}
                      </div>

                      {/* An svg carrying an aria-label needs role="img" for the
                          label to be announced at all. */}
                      {item.phase === "done" ? (
                        <CheckCircle2
                          role="img"
                          aria-label="Uploaded"
                          className="mt-1 size-6 shrink-0 text-success"
                        />
                      ) : item.phase === "error" ? (
                        <AlertCircle
                          role="img"
                          aria-label="Rejected"
                          className="mt-1 size-6 shrink-0 text-destructive"
                        />
                      ) : item.phase === "uploading" ? (
                        <Loader2
                          role="img"
                          aria-label="Uploading"
                          className="mt-1 size-6 shrink-0 animate-spin text-primary"
                        />
                      ) : (
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          className="shrink-0"
                          aria-label={`Remove ${item.file.name}`}
                          onClick={() => upload.removeFile(item.id)}
                        >
                          <X aria-hidden="true" />
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          ) : null}
        </div>

        <aside className="space-y-7">
          <Card>
            <CardHeader>
              <CardTitle>How to read them</CardTitle>
              <CardDescription>
                Saved onto this batch, so later changes to workspace defaults do not
                alter how these documents were read.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* One outer fieldset carries the locked state; each group is its
                  own fieldset so its legend is really the group's name rather
                  than a bold line that happens to sit above it. */}
              <fieldset disabled={Boolean(batchId)} className="min-w-0 space-y-6">
                <fieldset className="min-w-0">
                  <legend className="text-base font-semibold text-foreground">
                    Certificates to expect
                  </legend>
                  <p className="mt-1 text-sm text-muted-foreground">
                    Leave all of them unticked to accept any kind.
                  </p>
                  <div className="mt-3 space-y-1">
                    {CERTIFICATE_TYPES.map((type) => (
                      <label key={type.value} className={OPTION_ROW}>
                        <input
                          type="checkbox"
                          className="size-6 shrink-0 accent-primary"
                          checked={expectedTypes.includes(type.value)}
                          onChange={(event) =>
                            setExpectedTypes((current) =>
                              event.target.checked
                                ? [...current, type.value]
                                : current.filter((value) => value !== type.value),
                            )
                          }
                        />
                        {type.label}
                      </label>
                    ))}
                  </div>
                </fieldset>

                <Separator tone="subtle" />

                <fieldset className="min-w-0">
                  <legend className="text-base font-semibold text-foreground">
                    Languages on the certificates
                  </legend>
                  <p className="mt-1 text-sm text-muted-foreground">
                    Used when a page is a scan. Keep Urdu on for certificates written in
                    both languages.
                  </p>
                  <div className="mt-3 space-y-1">
                    {OCR_LANGUAGES.map((language) => (
                      <label key={language.value} className={OPTION_ROW}>
                        <input
                          type="checkbox"
                          className="size-6 shrink-0 accent-primary"
                          checked={languages.includes(language.value)}
                          onChange={(event) =>
                            setLanguages((current) => {
                              const next = event.target.checked
                                ? [...current, language.value]
                                : current.filter((value) => value !== language.value);
                              // Tesseract needs at least one language pack.
                              return next.length ? next : ["eng"];
                            })
                          }
                        />
                        {language.label}
                      </label>
                    ))}
                  </div>
                </fieldset>

                <Separator tone="subtle" />

                <fieldset className="min-w-0 space-y-5">
                  <legend className="text-base font-semibold text-foreground">
                    How sure the app must be
                  </legend>

                  <div>
                    <Label htmlFor="auto-approve">Accept on its own at or above</Label>
                    <div className="mt-2 flex items-center gap-4">
                      <input
                        id="auto-approve"
                        type="range"
                        min={0.5}
                        max={1}
                        step={0.01}
                        value={autoApprove}
                        onChange={(event) => setAutoApprove(Number(event.target.value))}
                        aria-describedby="threshold-help"
                        aria-invalid={!thresholdsValid}
                        className="h-11 w-full cursor-pointer accent-primary"
                      />
                      <span className="w-14 shrink-0 text-right text-lg font-bold tabular-nums">
                        {Math.round(autoApprove * 100)}%
                      </span>
                    </div>
                  </div>

                  <div>
                    <Label htmlFor="review-floor">Mark as failed below</Label>
                    <div className="mt-2 flex items-center gap-4">
                      <input
                        id="review-floor"
                        type="range"
                        min={0}
                        max={0.95}
                        step={0.01}
                        value={reviewFloor}
                        onChange={(event) => setReviewFloor(Number(event.target.value))}
                        aria-describedby="threshold-help"
                        aria-invalid={!thresholdsValid}
                        className="h-11 w-full cursor-pointer accent-primary"
                      />
                      <span className="w-14 shrink-0 text-right text-lg font-bold tabular-nums">
                        {Math.round(reviewFloor * 100)}%
                      </span>
                    </div>
                  </div>

                  {/* One id in both states, so the sliders always describe
                      themselves with whichever message is on screen. */}
                  {!thresholdsValid ? (
                    <p
                      id="threshold-help"
                      className="flex items-start gap-2 text-base font-semibold text-destructive"
                    >
                      <AlertCircle aria-hidden="true" className="mt-0.5 size-5 shrink-0" />
                      The failed number must be smaller than the accept number.
                    </p>
                  ) : (
                    <p id="threshold-help" className="text-base text-muted-foreground">
                      Anything between {Math.round(reviewFloor * 100)}% and{" "}
                      {Math.round(autoApprove * 100)}% goes to a person to check.
                    </p>
                  )}
                </fieldset>
              </fieldset>

              {batchId ? (
                <p className="rounded-lg bg-muted px-4 py-3 text-base text-muted-foreground">
                  These settings are locked now that the batch exists.
                </p>
              ) : null}
            </CardContent>
          </Card>

          {/* Sticky so the upload button stays reachable while someone scrolls a
              long file list on the left. */}
          <Card className="lg:sticky lg:top-28">
            <CardContent className="space-y-4 pt-7">
              {allDone && batchId ? (
                <Button
                  size="lg"
                  className="w-full"
                  onClick={() => router.push(`/batches/${batchId}`)}
                >
                  View batch
                </Button>
              ) : (
                <Button
                  size="lg"
                  className="w-full"
                  onClick={() => void handleStart()}
                  disabled={!canStart || createBatch.isPending}
                >
                  {upload.isUploading || createBatch.isPending ? (
                    <>
                      <Loader2 aria-hidden="true" className="animate-spin" />
                      Uploading {upload.stats.done}/{upload.stats.total}
                    </>
                  ) : (
                    <>
                      <Upload aria-hidden="true" />
                      Upload {upload.items.length || ""} file
                      {upload.items.length === 1 ? "" : "s"}
                    </>
                  )}
                </Button>
              )}

              {upload.isUploading ? (
                <Button variant="outline" size="lg" className="w-full" onClick={upload.cancel}>
                  Cancel upload
                </Button>
              ) : null}

              <p className="text-sm text-muted-foreground">
                Up to {MAX_BATCH_FILES.toLocaleString()} files, 500 MB each. Files over 8 MB
                upload in resumable chunks. Accepted: {ACCEPTED_EXTENSIONS.join(", ")}.
              </p>
            </CardContent>
          </Card>
        </aside>
      </div>
    </AppShell>
  );
}
