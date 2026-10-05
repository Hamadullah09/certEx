"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useDeleteBatch } from "@/hooks/use-batches";
import { ApiError } from "@/lib/api";
import { roleSatisfies, type UserRole } from "@/lib/schemas/auth";

/**
 * Deleting a batch: the one action on this screen that cannot be taken back.
 *
 * Confirmed in place rather than in a pop-up. A dialog that appears over the page
 * hides the thing being deleted at the moment the person is deciding about it, and on
 * a small screen it can open scrolled past its own buttons. Expanding underneath keeps
 * the batch name, the file count and the warning all visible together.
 *
 * The confirm step always says what will actually be lost, in counts rather than in
 * words like "associated data", because the person clicking this may be about to throw
 * away a morning of somebody's typing.
 */
export function DeleteBatch({
  batchId,
  batchName,
  fileCount,
  certificateCount,
  role,
  isProcessing,
}: {
  batchId: string;
  batchName: string;
  fileCount: number;
  certificateCount: number;
  role: UserRole | undefined;
  /** True only while the server is actually reading it - not while it waits for files. */
  isProcessing: boolean;
}) {
  const [confirming, setConfirming] = React.useState(false);
  // Only offered once the server has said the register is in the way, so a batch that
  // filed nothing never shows a question about register entries.
  const [alsoRegister, setAlsoRegister] = React.useState(false);
  const [registerBlocked, setRegisterBlocked] = React.useState<string | null>(null);
  const router = useRouter();
  const remove = useDeleteBatch();

  // The server refuses this for anyone below administrator. Saying so here, rather
  // than letting the button fail with a 403, is the difference between "you cannot do
  // this" and "something went wrong".
  const isAdmin = Boolean(role && roleSatisfies(role, "ADMIN"));

  return (
    <Card className="mt-8 border-2 border-destructive-border/60">
      <CardHeader className="pb-4">
        <CardTitle className="text-destructive">Delete this batch</CardTitle>
        <CardDescription>
          {isAdmin
            ? "Removes the uploaded files and everything that was read from them. If any certificates from it reached the register, you will be asked about those separately."
            : "Only an administrator can delete a batch. Ask one of them if this batch was uploaded by mistake."}
        </CardDescription>
      </CardHeader>

      {isAdmin ? (
        <CardContent>
          {confirming ? (
            <div className="space-y-4 rounded-lg border-2 border-destructive-border bg-destructive-surface p-5">
              <div className="flex gap-3">
                <AlertTriangle
                  aria-hidden="true"
                  className="mt-0.5 size-6 shrink-0 text-destructive"
                />
                <div className="space-y-2">
                  <p className="text-lg font-bold text-destructive-surface-foreground">
                    Delete &ldquo;{batchName}&rdquo;?
                  </p>
                  {isProcessing ? (
                    <p className="text-base font-semibold text-destructive-surface-foreground">
                      This batch is still being read. Deleting it stops that, and
                      whatever has been read so far goes with it.
                    </p>
                  ) : null}
                  <p className="text-base text-destructive-surface-foreground">
                    This will permanently remove{" "}
                    <span className="font-bold tabular-nums">{fileCount.toLocaleString()}</span>{" "}
                    {fileCount === 1 ? "file" : "files"}
                    {certificateCount > 0 ? (
                      <>
                        {" "}
                        and what was read from{" "}
                        <span className="font-bold tabular-nums">
                          {certificateCount.toLocaleString()}
                        </span>{" "}
                        {certificateCount === 1 ? "certificate" : "certificates"}, including any
                        corrections somebody typed
                      </>
                    ) : null}
                    . It cannot be undone.
                  </p>
                </div>
              </div>

              {registerBlocked ? (
                <div className="rounded-lg border-2 border-destructive-border bg-card p-4">
                  <p className="text-base font-bold text-destructive">{registerBlocked}</p>
                  <p className="mt-1 text-base">
                    Those entries are in the register and point at the scans in this
                    batch. Deleting the batch without them would leave records citing
                    files that no longer exist.
                  </p>
                  <label className="mt-3 flex min-h-12 items-center gap-3 text-base font-semibold">
                    <input
                      type="checkbox"
                      className="size-5"
                      checked={alsoRegister}
                      onChange={(event) => setAlsoRegister(event.target.checked)}
                    />
                    Delete those register entries as well
                  </label>
                  <p className="text-sm text-muted-foreground">
                    Tick this only if the whole batch was a mistake. Entries filed from
                    other batches are never touched.
                  </p>
                </div>
              ) : null}

              <div className="flex flex-wrap gap-3">
                <Button
                  variant="destructive"
                  size="lg"
                  disabled={remove.isPending || (registerBlocked !== null && !alsoRegister)}
                  onClick={() =>
                    remove.mutate(
                      // Forced only when it really is mid-read; the server refuses
                      // that case without it.
                      { id: batchId, force: isProcessing, includeRegister: alsoRegister },
                      {
                        onSuccess: () => {
                          toast.success(`"${batchName}" was deleted.`);
                          router.replace("/");
                        },
                        onError: (error) => {
                          // A refusal because the register cites this batch's scans is
                          // not a failure to report and dismiss - it is a question, and
                          // the answer is one more deliberate tick.
                          if (error instanceof ApiError && error.status === 409) {
                            setRegisterBlocked(error.userMessage);
                            return;
                          }
                          setConfirming(false);
                          toast.error(
                            error instanceof ApiError
                              ? error.userMessage
                              : "The batch could not be deleted.",
                          );
                        },
                      },
                    )
                  }
                >
                  <Trash2 aria-hidden="true" />
                  {remove.isPending ? "Deleting…" : "Yes, delete it"}
                </Button>
                <Button
                  variant="outline"
                  size="lg"
                  disabled={remove.isPending}
                  onClick={() => {
                    setConfirming(false);
                    setRegisterBlocked(null);
                    setAlsoRegister(false);
                  }}
                >
                  No, keep it
                </Button>
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              <Button variant="destructive" onClick={() => setConfirming(true)}>
                <Trash2 aria-hidden="true" />
                Delete this batch
              </Button>
              {isProcessing ? (
                <p className="text-base text-muted-foreground">
                  This batch is still being read. It can still be deleted - useful when
                  one has stopped making progress and will never finish.
                </p>
              ) : null}
            </div>
          )}
        </CardContent>
      ) : null}
    </Card>
  );
}
