"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

export interface ProgressProps extends React.HTMLAttributes<HTMLDivElement> {
  value: number;
  /** Accessible description of what is progressing. */
  label: string;
  tone?: "default" | "success" | "warning" | "danger";
}

/*
 * Each tone clears 3:1 against the track, which is what WCAG 1.4.11 asks of a
 * graphical object you have to be able to read. The tones point at the semantic
 * fills rather than at the confidence ramp's ink colours, which is what they
 * used to borrow.
 */
const TONE_CLASS: Record<NonNullable<ProgressProps["tone"]>, string> = {
  default: "bg-primary",
  success: "bg-success",
  warning: "bg-warning",
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
        className={cn(
          // The inset hairline keeps an empty track visible; a tint alone
          // disappears against a white card at 0%.
          "h-3 w-full overflow-hidden rounded-full bg-secondary",
          "shadow-[inset_0_0_0_1px_var(--border-subtle)]",
          className,
        )}
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
