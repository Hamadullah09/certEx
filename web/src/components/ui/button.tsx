"use client";

import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

/*
 * Every size clears the 44px minimum comfortable hit target from WCAG 2.5.8, so
 * there is no size in this file that a shaky hand on a trackpad will miss.
 *
 * Focus is left to the global `:focus-visible` rule rather than a box-shadow
 * ring: a real outline survives forced-colours mode and never gets clipped by a
 * parent's `overflow`, which a shadow does.
 */
const buttonVariants = cva(
  [
    "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg font-semibold",
    "transition-[background-color,border-color,color,box-shadow] duration-150",
    "[&_svg]:size-5 [&_svg]:shrink-0",
    // Disabled reads as a real state - a flat muted fill with legible ink -
    // rather than as 50% opacity, which drops label contrast below AA exactly
    // when a user most needs to read why nothing is happening.
    "disabled:pointer-events-none disabled:border-transparent disabled:bg-muted",
    "disabled:text-muted-foreground disabled:shadow-none",
  ],
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground shadow-soft hover:bg-primary/88 active:bg-primary/95",
        destructive:
          "bg-destructive text-destructive-foreground shadow-soft hover:bg-destructive/88 active:bg-destructive/95",
        // A two-pixel edge is what makes an outline button read as pressable
        // next to the filled one instead of as a label with a box round it.
        outline:
          "border-2 border-input bg-card text-foreground hover:border-primary hover:bg-accent hover:text-accent-foreground",
        secondary:
          "border-2 border-secondary-border/40 bg-secondary text-secondary-foreground hover:border-secondary-border hover:bg-secondary/70",
        ghost: "text-foreground hover:bg-accent hover:text-accent-foreground",
        // Links stay underlined at rest. Colour alone is not enough of a cue
        // that something is clickable for a reader who is new to the app.
        link: "text-primary underline decoration-2 underline-offset-4 hover:decoration-[3px]",
      },
      size: {
        default: "h-12 px-5 text-base",
        sm: "h-11 px-4 text-sm",
        lg: "h-14 px-8 text-lg [&_svg]:size-6",
        icon: "size-12",
        "icon-sm": "size-11",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, type, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        className={cn(buttonVariants({ variant, size }), className)}
        // Buttons inside forms default to submit, which silently submits the
        // form on click. Being explicit avoids that class of bug.
        type={asChild ? undefined : (type ?? "button")}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };
