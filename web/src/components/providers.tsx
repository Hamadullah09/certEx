"use client";

import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { Toaster } from "sonner";

import { ApiError } from "@/lib/api";

/**
 * Query defaults tuned for a long-running extraction pipeline:
 *
 * - Never retry a 4xx. A 403 or a 422 will not become a 200, and retrying a 401
 *   fights the client's own transparent refresh.
 * - Retry server and rate-limit errors with backoff.
 * - `refetchOnWindowFocus` stays on: an operator who leaves a batch processing
 *   and comes back should see current state without reloading.
 */
function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: true,
        retry: (failureCount: number, error: unknown) => {
          if (error instanceof ApiError && !error.isRetryable) return false;
          return failureCount < 3;
        },
        retryDelay: (attempt: number) => Math.min(1000 * 2 ** attempt, 15_000),
      },
      mutations: {
        retry: false,
      },
    },
  });
}

export function Providers({ children }: { children: React.ReactNode }) {
  // One client per browser session, created lazily so the server render and the
  // client hydration do not share a cache.
  const [queryClient] = React.useState(makeQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
        {children}
        <Toaster richColors closeButton position="bottom-right" />
      </ThemeProvider>
    </QueryClientProvider>
  );
}
