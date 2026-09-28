import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

/*
 * Status pills read at 17px, not 12px: a clerk scanning a list of two hundred
 * batches is reading these, not the row labels.
 *
 * Each variant is a tinted fill with dark ink of the same hue plus a visible
 * edge of that hue. Badges used to borrow the confidence ramp, which meant an
 * "uploading" pill and a low-confidence field said the same thing in the same
 * colour; the ramp is now reserved for confidence alone.
 */
const badgeVariants = cva(
  [
    "inline-flex items-center gap-1.5 rounded-full border px-3 py-1",
    "text-sm font-semibold whitespace-nowrap",
    "[&>svg]:size-4 [&>svg]:shrink-0",
  ],
  {
    variants: {
      variant: {
        default: "border-secondary-border bg-secondary text-secondary-foreground",
        outline: "border-border bg-card text-foreground",
        success: "border-success-border bg-success-surface text-success-surface-foreground",
        warning: "border-warning-border bg-warning-surface text-warning-surface-foreground",
        danger:
          "border-destructive-border bg-destructive-surface text-destructive-surface-foreground",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export type BadgeProps = React.HTMLAttributes<HTMLSpanElement> &
  VariantProps<typeof badgeVariants>;

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
