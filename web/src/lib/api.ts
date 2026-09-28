/**
 * Typed HTTP client for the CertExtract API.
 *
 * Three behaviours the rest of the app relies on:
 *
 * 1. **Cookies, not bearer tokens.** Session cookies are httpOnly, so the token
 *    is never reachable from JavaScript. Every request sends `credentials:
 *    "include"`.
 * 2. **CSRF echo.** The server sets a readable `certex_csrf` cookie; unsafe
 *    methods echo it in a header, which a cross-origin page cannot do.
 * 3. **One transparent refresh.** A 401 triggers a single refresh attempt and a
 *    replay of the original request. Concurrent 401s share one refresh promise,
 *    so a grid firing twenty requests does not start twenty rotations - which
 *    would trip the server's token-replay detection and log the user out.
 */

import { z } from "zod";

export const CSRF_COOKIE = "certex_csrf";
export const CSRF_HEADER = "X-CertEx-CSRF";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

/** RFC 7807 problem document, extended with `code` and `remediation`. */
export const problemDetailSchema = z.object({
  type: z.string(),
  title: z.string(),
  status: z.number().int(),
  code: z.string(),
  detail: z.string().nullish(),
  remediation: z.string().nullish(),
  instance: z.string().nullish(),
  errors: z
    .array(
      z.object({
        field: z.string(),
        message: z.string(),
        code: z.string().nullish(),
      }),
    )
    .nullish(),
  retry_after_seconds: z.number().int().nullish(),
  request_id: z.string().nullish(),
});

export type ProblemDetail = z.infer<typeof problemDetailSchema>;
export type FieldError = NonNullable<ProblemDetail["errors"]>[number];

/** Error carrying the server's problem document, so UI can show `remediation`. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly remediation: string | null;
  readonly fieldErrors: readonly FieldError[];
  readonly requestId: string | null;
  readonly retryAfterSeconds: number | null;

  constructor(problem: ProblemDetail) {
    super(problem.detail || problem.title);
    this.name = "ApiError";
    this.status = problem.status;
    this.code = problem.code;
    this.remediation = problem.remediation ?? null;
    this.fieldErrors = problem.errors ?? [];
    this.requestId = problem.request_id ?? null;
    this.retryAfterSeconds = problem.retry_after_seconds ?? null;
  }

  /** True when retrying the identical request could succeed. */
  get isRetryable(): boolean {
    return this.status === 429 || this.status >= 500;
  }

  /** Message plus the "what to do next" sentence, for a toast. */
  get userMessage(): string {
    return this.remediation ? `${this.message} ${this.remediation}` : this.message;
  }
}

/** Raised when the network never produced a response at all. */
export class NetworkError extends Error {
  constructor(cause: unknown) {
    super("Could not reach the server. Check your connection and try again.");
    this.name = "NetworkError";
    this.cause = cause;
  }
}

export function apiBaseUrl(): string {
  // On the server (RSC, route handlers) reach the API over the container
  // network; in the browser use the public URL.
  if (typeof window === "undefined") {
    return (
      process.env.API_INTERNAL_BASE_URL ??
      process.env.NEXT_PUBLIC_API_BASE_URL ??
      "http://localhost:8000"
    );
  }
  return process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(
    new RegExp(`(?:^|; )${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}=([^;]*)`),
  );
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  headers?: Record<string, string>;
  /** Set false to skip the transparent refresh (used by the refresh call itself). */
  retryOnUnauthorized?: boolean;
  /** Return the raw Response instead of parsing JSON - used by exports. */
  raw?: boolean;
}

let refreshInFlight: Promise<boolean> | null = null;

/**
 * Rotate the session. Concurrent callers await the same promise: firing several
 * refreshes in parallel would consume one token and replay another, which the
 * server treats as theft and punishes by revoking the whole family.
 */
export async function refreshSession(): Promise<boolean> {
  refreshInFlight ??= (async () => {
    try {
      const response = await fetch(`${apiBaseUrl()}/api/v1/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      return response.ok;
    } catch {
      return false;
    } finally {
      // Cleared on the next tick so callers that already awaited see the result.
      setTimeout(() => {
        refreshInFlight = null;
      }, 0);
    }
  })();

  return refreshInFlight;
}

async function toProblem(response: Response): Promise<ProblemDetail> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  const parsed = problemDetailSchema.safeParse(payload);
  if (parsed.success) return parsed.data;

  // A non-conforming error body (a proxy 502 page, say) still has to surface as
  // something the UI can render.
  return {
    type: "about:blank",
    title: response.statusText || "Request failed",
    status: response.status,
    code: response.status >= 500 ? "internal_error" : "bad_request",
    detail: `The server returned ${response.status}.`,
    remediation: "Retry in a moment. If it persists, contact an administrator.",
  };
}

async function execute(path: string, options: RequestOptions): Promise<Response> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...options.headers,
  };

  const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
  // Raw bytes - an upload chunk - travel as-is, never JSON-encoded.
  const isBlob = typeof Blob !== "undefined" && options.body instanceof Blob;
  if (options.body !== undefined && !isFormData) {
    headers["Content-Type"] ??= isBlob ? "application/octet-stream" : "application/json";
  }

  if (!SAFE_METHODS.has(method)) {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) headers[CSRF_HEADER] = csrf;
  }

  const init: RequestInit = {
    method,
    credentials: "include",
    headers,
    ...(options.signal ? { signal: options.signal } : {}),
  };

  if (options.body !== undefined) {
    init.body =
      isFormData || isBlob ? (options.body as FormData | Blob) : JSON.stringify(options.body);
  }

  try {
    return await fetch(`${apiBaseUrl()}${path}`, init);
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new NetworkError(cause);
  }
}

/** Issue a request, refreshing the session once on a 401. */
export async function request(path: string, options: RequestOptions = {}): Promise<Response> {
  let response = await execute(path, options);

  if (response.status === 401 && (options.retryOnUnauthorized ?? true)) {
    if (await refreshSession()) {
      response = await execute(path, { ...options, retryOnUnauthorized: false });
    }
  }

  if (!response.ok) {
    throw new ApiError(await toProblem(response));
  }
  return response;
}

/** Issue a request and parse the JSON body against `schema`. */
export async function apiFetch<T>(
  path: string,
  schema: z.ZodType<T, z.ZodTypeDef, unknown>,
  options: RequestOptions = {},
): Promise<T> {
  const response = await request(path, options);

  if (response.status === 204) {
    return schema.parse(undefined);
  }

  const payload: unknown = await response.json();
  const parsed = schema.safeParse(payload);
  if (!parsed.success) {
    // A shape mismatch means the client and server have diverged. Failing loudly
    // beats rendering `undefined` into a reviewer's certificate data.
    throw new Error(
      `Response from ${path} did not match the expected schema: ${parsed.error.message}`,
    );
  }
  return parsed.data;
}

/** Issue a request expecting no body. */
export async function apiVoid(path: string, options: RequestOptions = {}): Promise<void> {
  await request(path, options);
}
