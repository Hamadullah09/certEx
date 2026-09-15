"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FileStack, LogOut, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useLogout, useSession } from "@/hooks/use-session";

const appName = process.env.NEXT_PUBLIC_APP_NAME ?? "CertExtract";
const llmLabel = process.env.NEXT_PUBLIC_LLM_PROVIDER_LABEL;

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);

  // The server cannot know the viewer's theme, so the icon is only rendered
  // after hydration - otherwise the markup mismatches and React complains.
  React.useEffect(() => setMounted(true), []);

  const isDark = resolvedTheme === "dark";
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
      onClick={() => setTheme(isDark ? "light" : "dark")}
    >
      {mounted ? (
        isDark ? (
          <Sun aria-hidden="true" />
        ) : (
          <Moon aria-hidden="true" />
        )
      ) : (
        <span className="size-4" />
      )}
    </Button>
  );
}

/**
 * Authenticated chrome: header, sign-out, and the redirect-to-login guard.
 *
 * The guard is a convenience, not a control - every endpoint enforces its own
 * authorisation server-side, so hiding UI is never what keeps data safe.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { data: session, isPending } = useSession();
  const logout = useLogout();

  React.useEffect(() => {
    if (!isPending && session === null) router.replace("/login");
  }, [isPending, session, router]);

  if (!session) {
    return (
      <div className="mx-auto w-full max-w-6xl space-y-4 px-4 py-10">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-4 w-80" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-30 border-b border-border bg-background/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-6xl items-center justify-between gap-4 px-4 py-3">
          <Link href="/" className="flex items-center gap-2 rounded-md font-semibold">
            <FileStack aria-hidden="true" className="size-5 text-primary" />
            <span>{appName}</span>
          </Link>

          <div className="flex items-center gap-2">
            <div className="hidden min-w-0 text-right sm:block">
              <p className="truncate text-sm font-medium">{session.workspace.name}</p>
              <p className="truncate text-xs text-muted-foreground">
                {session.user.email} · {session.user.role.toLowerCase()}
              </p>
            </div>
            <ThemeToggle />
            <Button
              variant="outline"
              size="sm"
              onClick={() => logout.mutate()}
              disabled={logout.isPending}
            >
              <LogOut aria-hidden="true" />
              <span className="hidden sm:inline">Sign out</span>
            </Button>
          </div>
        </div>
      </header>

      <main id="main" className="mx-auto w-full max-w-6xl px-4 py-8">
        {children}
      </main>

      {llmLabel ? (
        <footer className="mx-auto w-full max-w-6xl px-4 pb-8">
          {/* Section 11: if the language model is external, say so in the UI. */}
          <p className="text-xs text-muted-foreground">
            Field extraction may send certificate text to {llmLabel} when rules cannot
            read a field. Disable the LLM fallback per batch or in settings to keep all
            processing local.
          </p>
        </footer>
      ) : null}
    </div>
  );
}
