"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

export interface ProgressProps extends React.HTMLAttributes<HTMLDivElement> {
  value: number;
  /** Accessible description of what is progressing. */
  label: string;
  tone?: "default" | "success" | "warning" | "danger";
}

const TONE_CLASS: Record<NonNullable<ProgressProps["tone"]>, string> = {
  default: "bg-primary",
  success: "bg-confidence-high-foreground",
  warning: "bg-confidence-medium-foreground",
  danger: "bg-destructive",
};

/**
 * Determinate progress bar.
 *
 * Exposes role="progressbar" with its numeric value, so a screen reader reports
 * "62 percent" rather than the user having to infer progress from a coloured
 * rectangle they cannot see.
 */
const Progress = React.forwardRef<HTMLDivElement, ProgressProps>(
  ({ className, value, label, tone = "default", ...props }, ref) => {
    const clamped = Math.max(0, Math.min(100, value));
    return (
      <div
        ref={ref}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(clamped)}
        aria-label={label}
        className={cn("h-2 w-full overflow-hidden rounded-full bg-secondary", className)}
        {...props}
      >
        <div
          className={cn("h-full rounded-full transition-[width] duration-300", TONE_CLASS[tone])}
          style={{ width: `${clamped}%` }}
        />
      </div>
    );
  },
);
Progress.displayName = "Progress";

export { Progress };
