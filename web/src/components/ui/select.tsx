"use client";

import * as React from "react";
import { ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";

export type SelectProps = React.SelectHTMLAttributes<HTMLSelectElement>;

/*
 * The platform's own dropdown, dressed to match `Input`.
 *
 * Deliberately not a Radix listbox: the filter bar sits above a horizontally
 * scrolling grid, and a native select is rendered by the operating system, so it
 * cannot be clipped by a scroll container, it works with a keyboard and a screen
 * reader without any code of ours, and it is the control the staff using this have
 * already met in every other Windows application.
 */
const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, children, ...props }, ref) => (
    <div className="relative">
      <select
        ref={ref}
        className={cn(
          "flex h-12 w-full appearance-none rounded-lg border-2 border-input bg-card pl-4 pr-11",
          "text-base text-foreground shadow-soft transition-[border-color] duration-150",
          "hover:border-primary-border focus-visible:border-ring",
          "disabled:cursor-not-allowed disabled:border-border disabled:bg-muted disabled:text-muted-foreground disabled:shadow-none",
          className,
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown
        aria-hidden="true"
        className="pointer-events-none absolute right-3.5 top-1/2 size-5 -translate-y-1/2 text-muted-foreground"
      />
    </div>
  ),
);
Select.displayName = "Select";

export { Select };
