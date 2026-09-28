"use client";

import * as React from "react";
import * as SwitchPrimitive from "@radix-ui/react-switch";

import { cn } from "@/lib/utils";

/*
 * Sized well above the old 20x36px: at 36x64px the travel of the thumb is
 * visible from a metre away, and `tap-target` grows the clickable area to the
 * 44px minimum without pushing the row taller.
 */
const Switch = React.forwardRef<
  React.ElementRef<typeof SwitchPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof SwitchPrimitive.Root>
>(({ className, ...props }, ref) => (
  <SwitchPrimitive.Root
    ref={ref}
    className={cn(
      "peer tap-target inline-flex h-9 w-16 shrink-0 cursor-pointer items-center rounded-full",
      "border-2 border-transparent transition-colors duration-150",
      "disabled:cursor-not-allowed disabled:bg-muted",
      "data-[state=checked]:bg-primary data-[state=unchecked]:bg-input",
      className,
    )}
    {...props}
  >
    <SwitchPrimitive.Thumb
      className={cn(
        "pointer-events-none block size-7 rounded-full bg-card shadow-lift ring-0 transition-transform duration-150",
        "data-[state=checked]:translate-x-8 data-[state=unchecked]:translate-x-0",
      )}
    />
  </SwitchPrimitive.Root>
));
Switch.displayName = SwitchPrimitive.Root.displayName;

export { Switch };
