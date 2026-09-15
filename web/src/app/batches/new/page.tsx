"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  CheckCircle2,
  Copy,
  FileText,
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
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { useBatchUpload, useCreateBatch } from "@/hooks/use-batches";
import { ApiError } from "@/lib/api";
import {
  ACCEPTED_EXTENSIONS,
  type CertificateType,
  type FileRejection,
  MAX_BATCH_FILES,
  screenFiles,
} from "@/lib/schemas/batches";
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
  { value: "ara", label: "Arabic" },
];

export default function NewBatchPage() {
  const router = useRouter();
  const createBatch = useCreateBatch();

  const [name, setName] = React.useState("");
  const [expectedTypes, setExpectedTypes] = React.useState<CertificateType[]>([]);
  const [languages, setLanguages] = React.useState<string[]>(["eng"]);
  const [autoApprove, setAutoApprove] = React.useState(0.9);
  const [reviewFloor, setReviewFloor] = React.useState(0.6);
  const [llmEnabled, setLlmEnabled] = React.useState(true);
  const [allowVision, setAllowVision] = React.useState(false);
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
  const canStart =
    name.trim().length > 0 && upload.items.length > 0 && thresholdsValid && !upload.isUploading;

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
            llm_enabled: llmEnabled,
            allow_vision: allowVision,
          },
        });
        id = created.id;
        setBatchId(id);
      }
      // The hook reads batchId from state, which has not flushed yet on the very
      // first run, so the upload is kicked off on the next tick.
      setTimeout(() => void upload.start(), 0);
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
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">New batch</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Drop in certificates as PDF, Word, images or a ZIP. Scanned pages are
            OCR&apos;d automatically.
          </p>
        </div>
        <Button variant="ghost" onClick={() => router.push("/")}>
          Cancel
        </Button>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Batch name</CardTitle>
              <CardDescription>
                How this batch appears in the dashboard and in exported filenames.
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

          <UploadDropzone onDrop={handleDrop} disabled={upload.isUploading} />

          {rejections.length > 0 ? (
            <Alert variant="warning">
              <AlertCircle aria-hidden="true" />
              <AlertTitle>
                {rejections.length} file{rejections.length === 1 ? "" : "s"} were not added
              </AlertTitle>
              <AlertDescription>
                <ul className="mt-1 space-y-0.5">
                  {rejections.slice(-5).map((rejection, index) => (
                    <li key={`${rejection.file.name}-${index}`} className="truncate text-xs">
                      <span className="font-medium">{rejection.file.name}</span> —{" "}
                      {rejection.message}
                    </li>
                  ))}
                </ul>
                <Button
                  variant="ghost"
                  size="sm"
                  className="mt-2"
                  onClick={() => setRejections([])}
                >
                  Dismiss
                </Button>
              </AlertDescription>
            </Alert>
          ) : null}

          {upload.items.length > 0 ? (
            <Card>
              <CardHeader className="flex-row items-center justify-between space-y-0">
                <div>
                  <CardTitle className="text-base">
                    {upload.items.length.toLocaleString()} file
                    {upload.items.length === 1 ? "" : "s"}
                  </CardTitle>
                  <CardDescription>
                    {formatBytes(upload.stats.bytes)} total
                    {upload.stats.done > 0
                      ? ` · ${upload.stats.done} uploaded`
                      : ""}
                    {upload.stats.duplicates > 0
                      ? ` · ${upload.stats.duplicates} duplicate`
                      : ""}
                    {upload.stats.failed > 0 ? ` · ${upload.stats.failed} rejected` : ""}
                  </CardDescription>
                </div>
                {!upload.isUploading ? (
                  <Button variant="ghost" size="sm" onClick={upload.clear}>
                    <Trash2 aria-hidden="true" />
                    Clear
                  </Button>
                ) : null}
              </CardHeader>
              <CardContent className="max-h-96 overflow-y-auto p-0">
                <ul className="divide-y divide-border">
                  {upload.items.map((item) => (
                    <li key={item.id} className="flex items-center gap-3 px-6 py-2.5">
                      <FileText
                        aria-hidden="true"
                        className="size-4 shrink-0 text-muted-foreground"
                      />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm">{item.file.name}</p>
                        <div className="mt-1 flex items-center gap-2">
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
                            className="h-1"
                          />
                          <span className="w-20 shrink-0 text-right text-xs text-muted-foreground">
                            {formatBytes(item.file.size)}
                          </span>
                        </div>
                        {item.error ? (
                          <p className="mt-1 text-xs text-destructive">{item.error}</p>
                        ) : null}
                        {item.result?.is_duplicate ? (
                          <p className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
                            <Copy aria-hidden="true" className="size-3" />
                            Already in this workspace — reusing the earlier extraction.
                          </p>
                        ) : null}
                        {item.result && item.result.children.length > 0 ? (
                          <p className="mt-1 text-xs text-muted-foreground">
                            Unpacked {item.result.children.length} document
                            {item.result.children.length === 1 ? "" : "s"} from the archive.
                          </p>
                        ) : null}
                      </div>

                      {item.phase === "done" ? (
                        <CheckCircle2
                          aria-label="Uploaded"
                          className="size-4 shrink-0 text-confidence-high-foreground"
                        />
                      ) : item.phase === "error" ? (
                        <AlertCircle
                          aria-label="Rejected"
                          className="size-4 shrink-0 text-destructive"
                        />
                      ) : item.phase === "uploading" ? (
                        <Loader2
                          aria-label="Uploading"
                          className="size-4 shrink-0 animate-spin text-muted-foreground"
                        />
                      ) : (
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-7 shrink-0"
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

        <aside className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Processing settings</CardTitle>
              <CardDescription>
                Frozen onto this batch, so later changes to workspace defaults do not
                alter how these documents were read.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <fieldset disabled={Boolean(batchId)} className="space-y-5">
                <div>
                  <legend className="text-sm font-medium">Certificate types expected</legend>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    Leave all unchecked to accept any type.
                  </p>
                  <div className="mt-2 space-y-1.5">
                    {CERTIFICATE_TYPES.map((type) => (
                      <label
                        key={type.value}
                        className="flex cursor-pointer items-center gap-2 text-sm"
                      >
                        <input
                          type="checkbox"
                          className="size-4 rounded border-input accent-primary"
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
                </div>

                <Separator />

                <div>
                  <legend className="text-sm font-medium">OCR languages</legend>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    Used for scanned pages. Add Urdu for Nastaliq documents.
                  </p>
                  <div className="mt-2 space-y-1.5">
                    {OCR_LANGUAGES.map((language) => (
                      <label
                        key={language.value}
                        className="flex cursor-pointer items-center gap-2 text-sm"
                      >
                        <input
                          type="checkbox"
                          className="size-4 rounded border-input accent-primary"
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
                </div>

                <Separator />

                <div className="space-y-3">
                  <div>
                    <Label htmlFor="auto-approve" className="text-sm">
                      Auto-approve at or above
                    </Label>
                    <div className="mt-1 flex items-center gap-3">
                      <input
                        id="auto-approve"
                        type="range"
                        min={0.5}
                        max={1}
                        step={0.01}
                        value={autoApprove}
                        onChange={(event) => setAutoApprove(Number(event.target.value))}
                        className="w-full accent-primary"
                      />
                      <span className="w-12 shrink-0 text-right text-sm tabular-nums">
                        {Math.round(autoApprove * 100)}%
                      </span>
                    </div>
                  </div>

                  <div>
                    <Label htmlFor="review-floor" className="text-sm">
                      Fail below
                    </Label>
                    <div className="mt-1 flex items-center gap-3">
                      <input
                        id="review-floor"
                        type="range"
                        min={0}
                        max={0.95}
                        step={0.01}
                        value={reviewFloor}
                        onChange={(event) => setReviewFloor(Number(event.target.value))}
                        className="w-full accent-primary"
                      />
                      <span className="w-12 shrink-0 text-right text-sm tabular-nums">
                        {Math.round(reviewFloor * 100)}%
                      </span>
                    </div>
                  </div>

                  {!thresholdsValid ? (
                    <p className="text-xs text-destructive">
                      The failure threshold must sit below the auto-approve threshold.
                    </p>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      Rows between {Math.round(reviewFloor * 100)}% and{" "}
                      {Math.round(autoApprove * 100)}% go to review.
                    </p>
                  )}
                </div>

                <Separator />

                <div className="space-y-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <Label htmlFor="llm-enabled" className="text-sm">
                        LLM fallback
                      </Label>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        Used only for fields the rules could not read. Turn off to keep
                        all text inside this deployment.
                      </p>
                    </div>
                    <Switch
                      id="llm-enabled"
                      checked={llmEnabled}
                      onCheckedChange={setLlmEnabled}
                    />
                  </div>

                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <Label htmlFor="allow-vision" className="text-sm">
                        Allow page images
                      </Label>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        Sends page images to the model only when text extraction found
                        nothing at all.
                      </p>
                    </div>
                    <Switch
                      id="allow-vision"
                      checked={allowVision}
                      disabled={!llmEnabled}
                      onCheckedChange={setAllowVision}
                    />
                  </div>
                </div>
              </fieldset>

              {batchId ? (
                <p className="text-xs text-muted-foreground">
                  Settings are locked once the batch exists.
                </p>
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-3 pt-6">
              {allDone ? (
                <Button className="w-full" onClick={() => router.push(`/batches/${batchId}`)}>
                  View batch
                </Button>
              ) : (
                <Button
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
                <Button variant="outline" className="w-full" onClick={upload.cancel}>
                  Cancel upload
                </Button>
              ) : null}

              <p className="text-xs text-muted-foreground">
                Up to {MAX_BATCH_FILES.toLocaleString()} files, 500 MB each.
                Accepted: {ACCEPTED_EXTENSIONS.join(", ")}.
              </p>
            </CardContent>
          </Card>
        </aside>
      </div>
    </AppShell>
  );
}
