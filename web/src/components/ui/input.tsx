"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

/*
 * 48px tall with a two-pixel edge, so a field is obviously a field on a scanned
 * office monitor. Focus thickens the edge to the ring colour in addition to the
 * global outline: two cues for "you are typing here", not one.
 */
const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => (
    <input
      ref={ref}
      type={type}
      className={cn(
        "flex h-12 w-full rounded-lg border-2 border-input bg-card px-4 text-base text-foreground shadow-soft",
        "transition-[border-color,background-color] duration-150",
        "placeholder:text-muted-foreground",
        "hover:border-primary-border",
        "focus-visible:border-ring",
        // Disabled fields keep AA-legible text: an operator still has to be
        // able to read the batch name that is now locked.
        "disabled:cursor-not-allowed disabled:border-border disabled:bg-muted disabled:text-muted-foreground disabled:shadow-none",
        // Invalid is carried by edge colour and a tinted fill as well as by the
        // error text the field is described by, never by colour alone.
        "aria-[invalid=true]:border-destructive aria-[invalid=true]:bg-destructive-surface",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

export { Input };
