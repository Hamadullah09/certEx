/**
 * When a reviewer may split or merge certificates, and at which page.
 *
 * The API refuses a bad split or merge with a problem document, but a reviewer
 * should not have to press a button to be told no. These checks mirror the
 * server's rules - a split page must be inside the unit and after its first page,
 * and units may only merge if they are adjacent pages of one file - so the button
 * is either enabled or carries the reason it is not.
 */

import type { RowSummary } from "@/lib/schemas/rows";

/** The server accepts at most fifty units in one merge. */
export const MAX_MERGE_UNITS = 50;

export type MergeCheck =
  | { ok: true; unitIds: string[] }
  | { ok: false; reason: string };

/** Pages a row could be split at: any page after the first one it covers. */
export function splitPageOptions(row: Pick<RowSummary, "page_start" | "page_end">): number[] {
  const pages: number[] = [];
  for (let page = Math.max(row.page_start + 1, 2); page <= row.page_end; page += 1) {
    pages.push(page);
  }
  return pages;
}

export function canSplitRow(row: Pick<RowSummary, "page_start" | "page_end">): boolean {
  return splitPageOptions(row).length > 0;
}

/**
 * Whether these rows are really one certificate, and in what order.
 *
 * Merging across files would produce a row whose pages come from two different
 * documents, and merging a gap would silently swallow the certificate in between,
 * so both are refused here with something a person can act on.
 */
export function checkMerge(rows: readonly RowSummary[]): MergeCheck {
  if (rows.length < 2) {
    return { ok: false, reason: "Choose at least two certificates to join." };
  }
  if (rows.length > MAX_MERGE_UNITS) {
    return { ok: false, reason: `Join at most ${MAX_MERGE_UNITS} certificates at a time.` };
  }

  const documentId = rows[0]?.document_id;
  if (rows.some((row) => row.document_id !== documentId)) {
    return { ok: false, reason: "They must all come from the same file." };
  }

  const ordered = [...rows].sort((left, right) => left.page_start - right.page_start);
  for (let index = 1; index < ordered.length; index += 1) {
    const previous = ordered[index - 1];
    const next = ordered[index];
    if (!previous || !next) continue;
    if (previous.page_end + 1 !== next.page_start) {
      return { ok: false, reason: "They must be next to each other in the file." };
    }
  }

  return { ok: true, unitIds: ordered.map((row) => row.unit_id) };
}
