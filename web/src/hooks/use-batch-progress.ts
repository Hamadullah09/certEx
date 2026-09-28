"use client";

import * as React from "react";

import { apiBaseUrl, refreshSession } from "@/lib/api";
import { type ProgressSnapshot, parseProgressEvent } from "@/lib/schemas/progress";

/**
 * How the processing view is currently learning about progress.
 *
 * The distinction is shown to the operator, quietly: someone watching two hundred
 * files go through needs to know the difference between "nothing has changed" and
 * "this page stopped listening".
 */
export type ProgressTransport = "connecting" | "stream" | "polling" | "ended";

export interface BatchProgress {
  snapshot: ProgressSnapshot | null;
  transport: ProgressTransport;
  /** The batch was deleted while we were watching it. */
  isGone: boolean;
  /** When the last event or heartbeat arrived, for the "still listening" note. */
  lastEventAt: number | null;
}

/**
 * Live progress for one batch over server-sent events, falling back to polling.
 *
 * The stream is the API's own `GET /batches/{id}/progress`: one `progress` event
 * per change, a `heartbeat` every fifteen seconds so a silent connection is not
 * mistaken for a dead one, and `gone` if the batch is deleted. The server ends the
 * stream itself once the batch reaches a terminal state, so this hook closes on
 * `finished` rather than letting the browser reconnect to a stream that is over.
 *
 * `EventSource` cannot carry a header and cannot be asked to retry with a fresh
 * session, so an error closes the stream here and is answered once with a session
 * refresh and one reopen - an access token expiring during a long batch is the
 * common case. A second failure gives up on the stream and reports `polling`, and
 * the caller then polls the batch instead; the bar looks identical either way
 * because both transports produce the same snapshot shape.
 */
export function useBatchProgress(
  batchId: string,
  options: { enabled?: boolean } = {},
): BatchProgress {
  const enabled = options.enabled ?? true;
  const [snapshot, setSnapshot] = React.useState<ProgressSnapshot | null>(null);
  const [transport, setTransport] = React.useState<ProgressTransport>("connecting");
  const [isGone, setIsGone] = React.useState(false);
  const [lastEventAt, setLastEventAt] = React.useState<number | null>(null);

  React.useEffect(() => {
    if (!batchId || !enabled) {
      setTransport("ended");
      return;
    }

    // Server rendering and the test environment have no EventSource; polling is
    // the honest answer there rather than a stream that silently never opens.
    if (typeof EventSource === "undefined") {
      setTransport("polling");
      return;
    }

    let cancelled = false;
    let current: EventSource | null = null;
    let refreshAttempted = false;

    const open = (): void => {
      if (cancelled) return;
      setTransport((previous) => (previous === "stream" ? previous : "connecting"));

      const stream = new EventSource(`${apiBaseUrl()}/api/v1/batches/${batchId}/progress`, {
        withCredentials: true,
      });
      current = stream;

      stream.addEventListener("open", () => {
        if (!cancelled) setTransport("stream");
      });

      stream.addEventListener("progress", (event) => {
        const parsed = parseProgressEvent((event as MessageEvent<string>).data);
        if (!parsed || cancelled) return;
        setSnapshot(parsed);
        setLastEventAt(Date.now());
        if (parsed.finished) {
          stream.close();
          setTransport("ended");
          return;
        }
        setTransport("stream");
      });

      stream.addEventListener("heartbeat", () => {
        if (!cancelled) setLastEventAt(Date.now());
      });

      stream.addEventListener("gone", () => {
        stream.close();
        if (cancelled) return;
        setIsGone(true);
        setTransport("ended");
      });

      stream.addEventListener("error", () => {
        stream.close();
        if (cancelled) return;
        if (!refreshAttempted) {
          refreshAttempted = true;
          void refreshSession().then(() => open());
          return;
        }
        setTransport("polling");
      });
    };

    open();

    return () => {
      cancelled = true;
      current?.close();
    };
  }, [batchId, enabled]);

  return { snapshot, transport, isGone, lastEventAt };
}
