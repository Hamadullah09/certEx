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

/**
 * Confidence-ramp fill and ink for a result cell.
 *
 * Shared by the grid and the review pane so one field cannot be shaded two ways.
 * The shading is never the only signal: every cell that carries it also prints the
 * percentage and names the band in its `aria-label`.
 */
const CONFIDENCE_CLASS: Record<ConfidenceBand, string> = {
  high: "bg-confidence-high text-confidence-high-foreground",
  medium: "bg-confidence-medium text-confidence-medium-foreground",
  low: "bg-confidence-low text-confidence-low-foreground",
  unknown: "",
};

export function confidenceClass(value: number | null | undefined): string {
  return CONFIDENCE_CLASS[confidenceBand(value)];
}

/** How the band is said out loud, for a title and an aria-label. */
const CONFIDENCE_WORD: Record<ConfidenceBand, string> = {
  high: "high confidence",
  medium: "medium confidence, worth checking",
  low: "low confidence, please check",
  unknown: "no confidence score",
};

export function describeConfidence(value: number | null | undefined): string {
  const band = confidenceBand(value);
  if (band === "unknown") return CONFIDENCE_WORD.unknown;
  return `${formatConfidence(value)} - ${CONFIDENCE_WORD[band]}`;
}
