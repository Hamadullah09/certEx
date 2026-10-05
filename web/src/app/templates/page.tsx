"use client";

import { AlertTriangle, LayoutTemplate } from "lucide-react";

import { AppShell } from "@/components/app-shell";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useTemplates } from "@/hooks/use-templates";
import { ApiError } from "@/lib/api";
import { CERTIFICATE_TYPE_LABEL } from "@/lib/schemas/batches";
import type { TemplateSummary } from "@/lib/schemas/templates";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function TemplateCard({ template }: { template: TemplateSummary }) {
  return (
    <li>
      <Card>
        <CardHeader className="gap-2 pb-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <CardTitle>{template.name}</CardTitle>
            <div className="flex items-center gap-2">
              <Badge variant="outline">{CERTIFICATE_TYPE_LABEL[template.certificate_type]}</Badge>
              {template.is_active ? (
                <Badge variant="success">In use</Badge>
              ) : (
                <Badge variant="warning">Switched off</Badge>
              )}
            </div>
          </div>
          <CardDescription>
            Learned {formatDate(template.created_at)} · used on{" "}
            {template.hit_count.toLocaleString()}{" "}
            {template.hit_count === 1 ? "certificate" : "certificates"}
            {typeof template.rule_count === "number"
              ? ` · knows ${template.rule_count} ${template.rule_count === 1 ? "field" : "fields"}`
              : ""}
          </CardDescription>
        </CardHeader>
      </Card>
    </li>
  );
}

/**
 * Learned form layouts.
 *
 * Nothing on this screen is configured; everything on it was earned by somebody
 * correcting a form. So the empty state is a normal state, and it says what to do to
 * leave it rather than apologising - a records office deciding whether the app has
 * learned their form has to be able to trust what this page tells them.
 */
export default function TemplatesPage() {
  const templates = useTemplates();

  return (
    <AppShell>
      <PageHeader
        icon={LayoutTemplate}
        title="Templates"
        description="Forms the app has learned. Once it knows where a field sits on a form, every other copy of that form is read the same way."
      />

      <Card className="mt-8 border-secondary-border/50 bg-secondary/30">
        <CardContent className="p-6">
          <h2 className="text-lg">What a template is</h2>
          <p className="mt-2 max-w-prose text-base">
            When someone corrects the same printed form a few times, the app remembers where each
            value sits on it - not by position on the page, which moves every time a page is
            scanned, but next to the printed words beside it. That is a template, and it makes
            later copies of the same form both faster and more accurate.
          </p>
        </CardContent>
      </Card>

      {templates.isPending ? (
        <div className="mt-8 space-y-3">
          {[0, 1, 2].map((index) => (
            <Skeleton key={index} className="h-28 w-full" />
          ))}
        </div>
      ) : templates.isError ? (
        <Alert variant="destructive" className="mt-8">
          <AlertTriangle aria-hidden="true" />
          <AlertTitle>The template list could not be loaded</AlertTitle>
          <AlertDescription>
            {templates.error instanceof ApiError
              ? templates.error.userMessage
              : "Try again in a moment."}
          </AlertDescription>
        </Alert>
      ) : templates.data.status === "unavailable" ? (
        <Card className="mt-8">
          <CardHeader>
            <CardTitle>This list cannot be loaded from the server</CardTitle>
            <CardDescription>
              The part of the server that keeps the learned forms is older than this screen, so it
              cannot be asked for the list. Nothing has been lost - whatever has been learned is
              still being used to read certificates. Ask whoever installed the app to update it.
            </CardDescription>
          </CardHeader>
        </Card>
      ) : templates.data.items.length === 0 ? (
        <Card className="mt-8">
          <CardHeader>
            <CardTitle>No forms have been learned yet</CardTitle>
            <CardDescription>
              There is nothing to set up here. Go to a batch, open its results, and fix a value the
              app got wrong - it then remembers where that value is printed on that form, and reads
              the next copy of the same form correctly on its own.
            </CardDescription>
          </CardHeader>
        </Card>
      ) : (
        <>
          <p className="mt-8 text-base text-muted-foreground">
            <span className="font-bold text-foreground tabular-nums">
              {(templates.data.total ?? templates.data.items.length).toLocaleString()}
            </span>{" "}
            learned {templates.data.items.length === 1 ? "form" : "forms"}
          </p>
          <ul className="mt-4 space-y-3">
            {templates.data.items.map((template) => (
              <TemplateCard key={template.id} template={template} />
            ))}
          </ul>
        </>
      )}
    </AppShell>
  );
}
