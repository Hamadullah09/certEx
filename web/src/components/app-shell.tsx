"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { BookOpen, FileStack, LayoutTemplate, LogOut, Moon, Settings, Sun } from "lucide-react";
import { useTheme } from "next-themes";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useLogout, useSession } from "@/hooks/use-session";
import { cn } from "@/lib/utils";

const appName = process.env.NEXT_PUBLIC_APP_NAME ?? "CertExtract";

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);

  // The server cannot know the viewer's theme, so the icon is only rendered
  // after hydration - otherwise the markup mismatches and React complains.
  React.useEffect(() => setMounted(true), []);

  const isDark = resolvedTheme === "dark";
  return (
    <Button
      variant="outline"
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
        // Holds the icon's box before hydration so the header does not shift.
        <span className="size-5" />
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
export function AppShell({
  children,
  wide = false,
}: {
  children: React.ReactNode;
  /** The review grid needs the whole monitor; every other page reads better narrow. */
  wide?: boolean;
}) {
  const router = useRouter();
  const { data: session, isPending } = useSession();
  const logout = useLogout();

  React.useEffect(() => {
    if (!isPending && session === null) router.replace("/login");
  }, [isPending, session, router]);

  if (!session) {
    return (
      <div className="mx-auto w-full max-w-6xl space-y-5 px-4 py-12 sm:px-6">
        <Skeleton className="h-14 w-14 rounded-xl" />
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-6 w-96" />
        <Skeleton className="h-72 w-full rounded-xl" />
      </div>
    );
  }

  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-30 border-b-2 border-border bg-background/92 backdrop-blur-md">
        <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-x-4 gap-y-3 px-4 py-3 sm:px-6">
          <Link
            href="/"
            className="flex items-center gap-3 rounded-lg text-xl font-bold tracking-tight text-foreground"
          >
            <span
              aria-hidden="true"
              className="flex size-11 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-soft"
            >
              <FileStack className="size-6" />
            </span>
            <span>{appName}</span>
          </Link>

          {/* Plain words, and links rather than icons alone: the places this app
              goes have to be readable at a glance from across a desk. The register
              comes first because looking a certificate up is what happens all day;
              uploading a batch of scans happens once a week. */}
          <nav aria-label="Main" className="order-3 flex items-center gap-1 sm:order-none">
            {[
              { href: "/register", label: "Register", Icon: BookOpen },
              { href: "/", label: "Batches", Icon: FileStack },
              { href: "/templates", label: "Templates", Icon: LayoutTemplate },
              { href: "/settings", label: "Settings", Icon: Settings },
            ].map(({ href, label, Icon }) => (
              <Link
                key={href}
                href={href}
                className="flex min-h-11 items-center gap-2 rounded-lg px-3 text-base font-semibold text-foreground hover:bg-accent hover:text-accent-foreground"
              >
                <Icon aria-hidden="true" className="size-5" />
                {label}
              </Link>
            ))}
          </nav>

          <div className="flex items-center gap-3">
            {/* Which workspace and which account - the two facts an operator
                double-checks before uploading someone's records. */}
            <div className="hidden min-w-0 text-right sm:block">
              <p className="truncate text-base font-semibold text-foreground">
                {session.workspace.name}
              </p>
              <p className="truncate text-sm text-muted-foreground">
                {session.user.email} · {session.user.role.toLowerCase()}
              </p>
            </div>
            <ThemeToggle />
            <Button
              variant="outline"
              onClick={() => logout.mutate()}
              disabled={logout.isPending}
            >
              <LogOut aria-hidden="true" />
              <span className="hidden sm:inline">Sign out</span>
            </Button>
          </div>
        </div>
      </header>

      <main
        id="main"
        className={cn(
          "mx-auto w-full px-4 py-10 sm:px-6 sm:py-12",
          wide ? "max-w-[112rem]" : "max-w-6xl",
        )}
      >
        {children}
      </main>
    </div>
  );
}
