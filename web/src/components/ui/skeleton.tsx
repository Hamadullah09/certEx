import { cn } from "@/lib/utils";

/**
 * Loading placeholder. The UI standard for this app is skeletons that mirror the
 * shape of the content, never a full-page spinner - a reviewer working through a
 * thousand rows should never lose their place to a blank screen.
 */
function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden="true"
      className={cn("animate-pulse rounded-md bg-muted", className)}
      {...props}
    />
  );
}

export { Skeleton };
