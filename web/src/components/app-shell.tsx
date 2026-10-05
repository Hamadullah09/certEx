"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { FileStack, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { UserMenu } from "@/components/user-menu";
import { useCertificateTypes } from "@/hooks/use-register";
import { useSession } from "@/hooks/use-session";
import { categoryLook } from "@/lib/categories";
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
  const pathname = usePathname();
  const { data: session, isPending } = useSession();
  const categories = useCertificateTypes();

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
      <header className="z-30 border-b-2 border-border bg-background/92 backdrop-blur-md sm:sticky sm:top-0">
        <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-x-4 gap-y-3 px-4 py-3 sm:px-6">
          <Link
            href="/"
            className="flex items-center gap-3 rounded-lg text-xl font-bold tracking-tight text-foreground"
          >
            <span
              aria-hidden="true"
              className="flex size-11 items-center justify-center rounded-xl bg-linear-to-br from-category-birth via-primary to-category-marriage text-white shadow-soft ring-1 ring-white/25"
            >
              <FileStack className="size-6" />
            </span>
            <span className="hidden sm:inline">{appName}</span>
          </Link>

          {/* The categories are the navigation. Everything a clerk does starts by
              choosing what kind of certificate they are holding, so that choice is the
              top level rather than something reached through a list of batches.

              Each carries its own colour and its own icon, because this is the control
              used dozens of times a day and four identical grey links have to be read
              every single time. The selected one is filled rather than tinted - at a
              glance across a desk, a tint and a hover state look the same.

              Read from the server rather than written out here: these four are what a
              workspace is seeded with, and an office that adds a fifth gets it in the
              navigation without a new release. */}
          <nav
            aria-label="Certificate categories"
            className="flex flex-wrap items-center gap-1.5 rounded-xl bg-muted/70 p-1.5"
          >
            {categories.isPending
              ? [0, 1, 2, 3].map((index) => (
                  <Skeleton key={index} className="h-11 w-28 rounded-lg" />
                ))
              : (categories.data ?? []).map((category) => {
                  const href = `/categories/${category.id}`;
                  const look = categoryLook(category.classifier_key);
                  const current = pathname.startsWith(href);
                  return (
                    <Link
                      key={category.id}
                      href={href}
                      aria-current={current ? "page" : undefined}
                      className={cn(
                        "flex min-h-11 items-center gap-2 whitespace-nowrap rounded-lg px-3.5 text-base font-bold transition-colors",
                        current
                          ? cn(look.fill, "text-white shadow-soft")
                          : cn("text-foreground", look.hover),
                      )}
                    >
                      <look.Icon
                        aria-hidden="true"
                        className={cn("size-5", current ? "text-white" : look.ink)}
                      />
                      {category.name}
                    </Link>
                  );
                })}
          </nav>

          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            {/* Which workspace and which account - the two facts an operator
                double-checks before uploading someone's records. */}
            <ThemeToggle />
            <UserMenu session={session} />
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
