/**
 * Optimistic updates for the review grid.
 *
 * A reviewer correcting a mis-read name types, presses Enter and moves on; they
 * should not watch a cell flicker while a round trip happens. These reducers
 * predict what `PATCH /rows/{id}` will do so the cell can settle immediately, and
 * the caller keeps the pre-edit snapshot to roll back to if the call fails.
 *
 * They mirror `row_service.correct_row` deliberately: a manually typed value is
 * stored with confidence 1.0 and the method `manual`, an emptied value removes the
 * field and its confidence, and a field the certificate type does not declare is
 * an extra field and is stored as typed.
 *
 * What they do *not* predict is `review_status` after a plain edit: the server
 * re-validates and re-scores the row, which can move it between "needs checking"
 * and "accepted", and guessing at that would show a reviewer a verdict the server
 * never reached. The response replaces the row and carries the real one.
 */

import { fieldsFor } from "@/lib/schemas/fields.generated";
import type { RowDetail, RowPage, RowSummary } from "@/lib/schemas/rows";

/** Confidence the server records for a value a person typed. */
export const MANUAL_CONFIDENCE = 1;

export const MANUAL_METHOD = "manual";

function withoutKey<T>(record: Record<string, T>, key: string): Record<string, T> {
  const next = { ...record };
  delete next[key];
  return next;
}

/** True when this certificate type does not declare the field, making it an extra. */
export function isExtraField(row: Pick<RowSummary, "certificate_type">, name: string): boolean {
  return !fieldsFor(row.certificate_type).some((spec) => spec.name === name);
}

/** Trim as the server does: surrounding space goes, and empty means "cleared". */
export function cleanFieldValue(raw: string | null): string | null {
  if (raw === null) return null;
  return raw.trim() || null;
}

export function applyFieldEdit(
  row: RowSummary,
  name: string,
  raw: string | null,
  now: string = new Date().toISOString(),
): RowSummary {
  const value = cleanFieldValue(raw);

  if (isExtraField(row, name)) {
    return {
      ...row,
      extra_fields:
        value === null ? withoutKey(row.extra_fields, name) : { ...row.extra_fields, [name]: value },
      reviewed_at: now,
      updated_at: now,
    };
  }

  return {
    ...row,
    fields: value === null ? withoutKey(row.fields, name) : { ...row.fields, [name]: value },
    field_confidences:
      value === null
        ? withoutKey(row.field_confidences, name)
        : { ...row.field_confidences, [name]: MANUAL_CONFIDENCE },
    reviewed_at: now,
    updated_at: now,
  };
}

/** The same edit on an opened row, keeping its per-field provenance in step. */
export function applyFieldEditToDetail(
  row: RowDetail,
  name: string,
  raw: string | null,
  now: string = new Date().toISOString(),
): RowDetail {
  const value = cleanFieldValue(raw);
  const updated = applyFieldEdit(row, name, raw, now);
  return {
    ...updated,
    values: row.values.map((field) =>
      field.name === name
        ? {
            ...field,
            value,
            confidence: value === null ? null : MANUAL_CONFIDENCE,
            method: value === null ? null : MANUAL_METHOD,
            // A typed value has no position on the page, so the preview stops
            // drawing a box rather than pointing at where the old value was.
            bbox: value === null ? field.bbox : null,
            snippet: value === null ? field.snippet : null,
          }
        : field,
    ),
    issues: row.issues,
    page_numbers: row.page_numbers,
  };
}

export function applyApproval(
  row: RowSummary,
  now: string = new Date().toISOString(),
): RowSummary {
  return { ...row, review_status: "MANUALLY_APPROVED", reviewed_at: now, updated_at: now };
}

/**
 * Replace one row wherever it sits in the loaded pages.
 *
 * Generic over the page-param type so it can be handed straight to TanStack
 * Query's `setQueryData` for an infinite query without a cast.
 */
export function replaceRowInPages<TParam>(
  data: { pages: RowPage[]; pageParams: TParam[] },
  row: RowSummary,
): { pages: RowPage[]; pageParams: TParam[] } {
  return {
    pageParams: data.pageParams,
    pages: data.pages.map((page) => ({
      ...page,
      items: page.items.map((item) => (item.id === row.id ? row : item)),
    })),
  };
}

/** Apply a change to every row whose id is in `ids` - used by bulk approve. */
export function mapRowsInPages<TParam>(
  data: { pages: RowPage[]; pageParams: TParam[] },
  ids: ReadonlySet<string>,
  change: (row: RowSummary) => RowSummary,
): { pages: RowPage[]; pageParams: TParam[] } {
  return {
    pageParams: data.pageParams,
    pages: data.pages.map((page) => ({
      ...page,
      items: page.items.map((item) => (ids.has(item.id) ? change(item) : item)),
    })),
  };
}

export function flattenRowPages(
  data: { pages: RowPage[] } | undefined,
): RowSummary[] {
  return data?.pages.flatMap((page) => page.items) ?? [];
}

/**
 * The total the grid shows.
 *
 * `include_total` is only requested for the first page, so the count lives on
 * whichever loaded page carries it rather than on the last one fetched.
 */
export function totalFromPages(data: { pages: RowPage[] } | undefined): number | null {
  for (const page of data?.pages ?? []) {
    if (typeof page.meta.total === "number") return page.meta.total;
  }
  return null;
}
