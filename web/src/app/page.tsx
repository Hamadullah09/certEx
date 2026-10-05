"use client";

import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  BookOpen,
  FileStack,
  LayoutTemplate,
  ScrollText,
  Settings,
} from "lucide-react";

import { AppShell } from "@/components/app-shell";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useCertificateTypes } from "@/hooks/use-register";
import { ApiError } from "@/lib/api";
import { categoryLook } from "@/lib/categories";
import { cn } from "@/lib/utils";

/**
 * Where the work starts: what kind of certificate is in your hand.
 *
 * The office's own categories, read from the server. A workspace is seeded with
 * births, marriages, deaths and a catch-all, and an office that adds a fifth sees it
 * here without anybody shipping a release.
 *
 * Everything else in the app hangs off this choice - the batches are inside a
 * category, the columns are inside a batch - so this page does one thing and says
 * what each category is for rather than listing counts nobody acts on.
 */
export default function HomePage() {
  const categories = useCertificateTypes();

  return (
    <AppShell>
      <PageHeader
        icon={ScrollText}
        title="Certificates"
        description="Choose the kind of certificate you are working with. Each one keeps its own batches."
      />

      {categories.isError ? (
        <Alert variant="destructive" className="mt-8">
          <AlertTriangle aria-hidden="true" />
          <AlertTitle>The categories could not be loaded</AlertTitle>
          <AlertDescription>
            {categories.error instanceof ApiError
              ? categories.error.userMessage
              : "Try again in a moment."}
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="mt-8 grid gap-4 sm:grid-cols-2">
        {categories.isPending
          ? [0, 1, 2, 3].map((index) => <Skeleton key={index} className="h-32 w-full rounded-xl" />)
          : (categories.data ?? []).map((category) => {
              const look = categoryLook(category.classifier_key);
              return (
                <Link
                  key={category.id}
                  href={`/categories/${category.id}`}
                  className="rounded-xl focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                >
                  {/* The same hue and icon the navigation uses, so the four choices are
                      learned once rather than on every screen. */}
                  <Card
                    className={cn("h-full border-2 transition-colors", look.hover)}
                  >
                    <CardContent className="flex h-full items-start gap-4 p-6">
                      <span
                        aria-hidden="true"
                        className={cn(
                          "flex size-14 shrink-0 items-center justify-center rounded-xl",
                          look.surface,
                          look.ink,
                        )}
                      >
                        <look.Icon className="size-7" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <h2 className="text-2xl font-bold text-foreground">{category.name}</h2>
                        {category.description ? (
                          <p className="mt-1 text-base text-muted-foreground">
                            {category.description}
                          </p>
                        ) : null}
                      </div>
                      <ArrowRight
                        aria-hidden="true"
                        className={cn("mt-1 size-6 shrink-0", look.ink)}
                      />
                    </CardContent>
                  </Card>
                </Link>
              );
            })}
      </div>

      {/* Everything that is not a category. It lives here rather than in the
          navigation bar, which carries only the one choice a clerk makes all day -
          what kind of certificate is in their hand. These are occasional. */}
      <div className="mt-10">
        <h2 className="text-lg text-muted-foreground">More</h2>
        <div className="mt-3 flex flex-wrap gap-3">
          {[
            {
              href: "/register",
              label: "Search",
              detail: "Every certificate, all categories",
              Icon: BookOpen,
            },
            {
              href: "/batches",
              label: "Batches",
              detail: "All uploads, all categories",
              Icon: FileStack,
            },
            {
              href: "/templates",
              label: "Templates",
              detail: "Forms the app has learned",
              Icon: LayoutTemplate,
            },
            {
              href: "/settings",
              label: "Settings",
              detail: "Name, thresholds and users",
              Icon: Settings,
            },
          ].map(({ href, label, detail, Icon }) => (
            <Link
              key={href}
              href={href}
              className="flex min-h-14 flex-1 basis-56 items-center gap-3 rounded-lg border-2 border-border bg-card px-4 py-3 hover:border-primary hover:bg-accent/40"
            >
              <Icon aria-hidden="true" className="size-5 shrink-0 text-muted-foreground" />
              <span className="min-w-0">
                <span className="block text-base font-semibold text-foreground">{label}</span>
                <span className="block text-sm text-muted-foreground">{detail}</span>
              </span>
            </Link>
          ))}
        </div>
      </div>
    </AppShell>
  );
}
