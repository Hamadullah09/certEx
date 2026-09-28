import { cn } from "@/lib/utils";

/**
 * Loading placeholder. The UI standard for this app is skeletons that mirror the
 * shape of the content, never a full-page spinner - a reviewer working through a
 * thousand rows should never lose their place to a blank screen.
 *
 * The radius matches the primitives it stands in for, so the page does not
 * change shape when real content arrives.
 */
function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden="true"
      className={cn("animate-pulse rounded-lg bg-muted", className)}
      {...props}
    />
  );
}

export { Skeleton };
