"use client";

import * as React from "react";
import * as LabelPrimitive from "@radix-ui/react-label";

import { cn } from "@/lib/utils";

/*
 * Labels are set at body size and semi-bold rather than small and grey. A field
 * label is the single most important word on a form for someone filling it in
 * for the first time, so it is never the quietest thing on the row.
 */
const Label = React.forwardRef<
  React.ElementRef<typeof LabelPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof LabelPrimitive.Root>
>(({ className, ...props }, ref) => (
  <LabelPrimitive.Root
    ref={ref}
    className={cn(
      "inline-block text-base font-semibold leading-snug text-foreground",
      "peer-disabled:cursor-not-allowed peer-disabled:text-muted-foreground",
      className,
    )}
    {...props}
  />
));
Label.displayName = LabelPrimitive.Root.displayName;

export { Label };
