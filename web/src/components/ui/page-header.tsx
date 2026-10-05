import * as React from "react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export interface PageHeaderProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Rendered as the page's only h1. */
  title: string;
  /** One plain sentence saying what this page is for. */
  description: string;
  icon: LucideIcon;
  /**
   * Overrides the icon tile's colours.
   *
   * Used by the category screens, which each wear their own hue so a clerk can tell
   * which register they are in from the shape of the page rather than by reading the
   * heading. Left unset everywhere else, which keeps the primary tile.
   */
  iconClassName?: string;
  /** Primary action for the page, shown at the end of the row. */
  actions?: React.ReactNode;
}

/**
 * The band at the top of every page: icon tile, h1, one explaining sentence, and
 * the page's primary action.
 *
 * It exists as a primitive because the two pages that ship today built it by
 * hand and had already drifted apart on heading size - and because the review,
 * template and settings pages will each want the same band.
 */
const PageHeader = React.forwardRef<HTMLDivElement, PageHeaderProps>(
  ({ className, title, description, icon: Icon, iconClassName, actions, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("flex flex-wrap items-start justify-between gap-5", className)}
      {...props}
    >
      <div className="flex min-w-0 items-start gap-4">
        <span
          aria-hidden="true"
          className={cn(
            "hidden size-14 shrink-0 items-center justify-center rounded-xl border border-primary-border/50 bg-primary-surface text-primary-surface-foreground sm:flex",
            iconClassName,
          )}
        >
          <Icon className="size-7" />
        </span>
        <div className="min-w-0">
          <h1 className="text-3xl text-foreground">{title}</h1>
          <p className="mt-1.5 max-w-prose text-lg text-muted-foreground">{description}</p>
        </div>
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-3">{actions}</div> : null}
    </div>
  ),
);
PageHeader.displayName = "PageHeader";

export { PageHeader };
