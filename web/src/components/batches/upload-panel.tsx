"use client";

import * as React from "react";
import { FileText, UploadCloud } from "lucide-react";
import { toast } from "sonner";

import { UploadDropzone } from "@/app/batches/new/dropzone";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { useBatchUpload } from "@/hooks/use-batches";
import { screenFiles, type FileRejection } from "@/lib/schemas/batches";
import { formatBytes } from "@/lib/utils";

/**
 * Adding documents to a batch that already exists.
 *
 * The reason this is on the batch screen and not only on the one that creates a batch:
 * a register is not uploaded in a single sitting. An office scans a drawer this week
 * and another next month, and both go into the same batch - read for the same columns,
 * joining the same dataset - without anybody configuring anything again.
 *
 * Nothing here holds the batch in memory. The files being uploaded right now are the
 * only ones this component knows about; the batch itself may already hold a million.
 */
export function UploadPanel({
  batchId,
  columnCount,
  onFinished,
}: {
  batchId: string;
  columnCount: number;
  onFinished?: () => void;
}) {
  const upload = useBatchUpload(batchId);
  const [rejections, setRejections] = React.useState<FileRejection[]>([]);

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

  const pending = upload.items.filter(
    (item) => item.phase !== "done" && item.phase !== "error",
  ).length;
  const finished = upload.items.length > 0 && pending === 0 && !upload.isUploading;

  React.useEffect(() => {
    if (finished) onFinished?.();
  }, [finished, onFinished]);

  return (
    <Card className="mt-8">
      <CardHeader className="pb-4">
        <CardTitle className="flex items-center gap-2">
          <UploadCloud aria-hidden="true" className="size-6 text-primary" />
          Add documents
        </CardTitle>
        <CardDescription>
          Every document added here is read for this batch&rsquo;s {columnCount}{" "}
          {columnCount === 1 ? "column" : "columns"}. You are not asked to set them up again.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        <UploadDropzone onDrop={handleDrop} disabled={upload.isUploading} />

        {rejections.length > 0 ? (
          <Alert variant="warning">
            <AlertTitle>
              {rejections.length} {rejections.length === 1 ? "file was" : "files were"} not added
            </AlertTitle>
            <AlertDescription>
              <ul className="list-disc space-y-1 pl-5">
                {rejections.slice(-5).map((rejection) => (
                  <li key={rejection.file.name}>
                    <span className="font-medium">{rejection.file.name}</span> — {rejection.reason}
                  </li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        ) : null}

        {upload.items.length > 0 ? (
          <>
            <ul className="max-h-80 divide-y divide-border-subtle overflow-y-auto rounded-lg border border-border">
              {upload.items.map((item) => (
                <li key={item.id} className="flex items-start gap-4 px-4 py-3">
                  <FileText
                    aria-hidden="true"
                    className="mt-1 size-5 shrink-0 text-muted-foreground"
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-base font-medium">{item.file.name}</p>
                    <p className="text-sm text-muted-foreground">
                      {formatBytes(item.file.size)}
                      {item.phase === "error" && item.error ? ` — ${item.error}` : ""}
                    </p>
                  </div>
                  <div className="w-40 shrink-0">
                    {item.phase === "done" ? (
                      <span className="text-base font-semibold text-success">Uploaded</span>
                    ) : item.phase === "error" ? (
                      <span className="text-base font-semibold text-destructive">Failed</span>
                    ) : (
                      <Progress
                        value={item.progress ?? 0}
                        label={`${item.file.name} upload progress`}
                      />
                    )}
                  </div>
                </li>
              ))}
            </ul>

            <div className="flex flex-wrap items-center gap-4">
              <Button
                size="lg"
                disabled={upload.isUploading || pending === 0}
                onClick={() => void upload.start()}
              >
                <UploadCloud aria-hidden="true" />
                {upload.isUploading
                  ? "Uploading…"
                  : `Upload ${pending} ${pending === 1 ? "document" : "documents"}`}
              </Button>
              {upload.isUploading ? (
                <Button variant="outline" size="lg" onClick={() => upload.cancel()}>
                  Stop
                </Button>
              ) : upload.items.length > 0 ? (
                <Button variant="outline" size="lg" onClick={() => upload.clear()}>
                  Clear the list
                </Button>
              ) : null}
              {finished ? (
                <p className="text-base text-muted-foreground">
                  Uploaded. Reading happens in the background — the counts above update as it
                  goes.
                </p>
              ) : null}
            </div>
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}
