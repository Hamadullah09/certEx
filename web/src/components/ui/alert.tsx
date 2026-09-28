import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

/*
 * Alerts carry a tinted fill rather than only a coloured border. The previous
 * destructive variant was red text on the page background, which put the whole
 * weight of "something went wrong" on the colour of the words - the first thing
 * a colour-blind reader loses.
 */
const alertVariants = cva(
  [
    "relative w-full rounded-xl border-2 p-5 text-base",
    "[&>svg]:absolute [&>svg]:left-5 [&>svg]:top-5 [&>svg]:size-6 [&>svg]:shrink-0",
    "[&>svg~*]:pl-10",
  ],
  {
    variants: {
      variant: {
        default: "border-border bg-card text-card-foreground",
        destructive:
          "border-destructive-border bg-destructive-surface text-destructive-surface-foreground",
        warning: "border-warning-border bg-warning-surface text-warning-surface-foreground",
        success: "border-success-border bg-success-surface text-success-surface-foreground",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

const Alert = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement> & VariantProps<typeof alertVariants>
>(({ className, variant, ...props }, ref) => (
  // role="alert" so screen readers announce validation and pipeline failures
  // without the user having to go looking for them.
  <div ref={ref} role="alert" className={cn(alertVariants({ variant }), className)} {...props} />
));
Alert.displayName = "Alert";

const AlertTitle = React.forwardRef<
  HTMLParagraphElement,
  React.HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h3 ref={ref} className={cn("mb-1.5 text-lg font-bold leading-snug", className)} {...props} />
));
AlertTitle.displayName = "AlertTitle";

const AlertDescription = React.forwardRef<
  HTMLParagraphElement,
  React.HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("text-base leading-relaxed [&_p]:leading-relaxed", className)} {...props} />
));
AlertDescription.displayName = "AlertDescription";

export { Alert, AlertTitle, AlertDescription };
