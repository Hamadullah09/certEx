import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge conditional class names, resolving Tailwind conflicts left-to-right. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Format a byte count for display, e.g. `4.2 MB`. */
export function formatBytes(bytes: number, decimals = 1): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"] as const;
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** index;
  return `${value.toFixed(index === 0 ? 0 : decimals)} ${units[index]}`;
}

/** Format a 0-1 confidence as a whole-number percentage. */
export function formatConfidence(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return `${Math.round(value * 100)}%`;
}

export type ConfidenceBand = "high" | "medium" | "low" | "unknown";

/**
 * Band a confidence score for display.
 *
 * Thresholds mirror the server's review routing: >= 0.90 auto-approve,
 * 0.60-0.90 needs review, below that failed.
 */
export function confidenceBand(value: number | null | undefined): ConfidenceBand {
  if (value === null || value === undefined || Number.isNaN(value)) return "unknown";
  if (value >= 0.9) return "high";
  if (value >= 0.6) return "medium";
  return "low";
}
