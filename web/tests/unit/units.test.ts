import { describe, expect, it } from "vitest";

import type { RowSummary } from "@/lib/schemas/rows";
import { canSplitRow, checkMerge, splitPageOptions } from "@/lib/units";

function makeRow(overrides: Partial<RowSummary>): RowSummary {
  return {
    id: "row",
    unit_id: "unit",
    document_id: "doc-a",
    batch_id: "batch",
    serial_no: 1,
    file_name: "births.pdf",
    page_start: 1,
    page_end: 1,
    certificate_type: "BIRTH",
    review_status: "NEEDS_REVIEW",
    row_confidence: 0.8,
    flags: [],
    fields: {},
    field_confidences: {},
    extra_fields: {},
    ocr_used: false,
    detected_language: null,
    reviewed_at: null,
    updated_at: "2026-01-01T00:00:00.000Z",
    ...overrides,
  };
}

describe("splitPageOptions", () => {
  it("offers every page after the first one the certificate covers", () => {
    expect(splitPageOptions({ page_start: 1, page_end: 3 })).toEqual([2, 3]);
    expect(splitPageOptions({ page_start: 4, page_end: 6 })).toEqual([5, 6]);
  });

  it("offers nothing for a single-page certificate, which cannot be split", () => {
    expect(splitPageOptions({ page_start: 5, page_end: 5 })).toEqual([]);
    expect(canSplitRow({ page_start: 5, page_end: 5 })).toBe(false);
    expect(canSplitRow({ page_start: 5, page_end: 6 })).toBe(true);
  });

  it("never offers page one, which the server refuses", () => {
    expect(splitPageOptions({ page_start: 1, page_end: 2 })).toEqual([2]);
  });
});

describe("checkMerge", () => {
  const first = makeRow({ id: "r1", unit_id: "u1", page_start: 1, page_end: 2 });
  const second = makeRow({ id: "r2", unit_id: "u2", page_start: 3, page_end: 3 });
  const third = makeRow({ id: "r3", unit_id: "u3", page_start: 4, page_end: 4 });

  it("joins adjacent certificates from one file, in page order", () => {
    const result = checkMerge([second, first]);
    expect(result).toEqual({ ok: true, unitIds: ["u1", "u2"] });
  });

  it("joins a run of three", () => {
    expect(checkMerge([first, second, third])).toEqual({
      ok: true,
      unitIds: ["u1", "u2", "u3"],
    });
  });

  it("asks for at least two", () => {
    const result = checkMerge([first]);
    expect(result.ok).toBe(false);
    expect(result.ok === false && result.reason).toContain("at least two");
  });

  it("refuses certificates from different files", () => {
    const other = makeRow({ id: "r9", unit_id: "u9", document_id: "doc-b", page_start: 3 });
    const result = checkMerge([first, other]);
    expect(result.ok).toBe(false);
    expect(result.ok === false && result.reason).toContain("same file");
  });

  it("refuses a gap, which would swallow the certificate in between", () => {
    const result = checkMerge([first, third]);
    expect(result.ok).toBe(false);
    expect(result.ok === false && result.reason).toContain("next to each other");
  });

  it("refuses more than the server accepts in one call", () => {
    const many = Array.from({ length: 51 }, (_unused, index) =>
      makeRow({
        id: `r${index}`,
        unit_id: `u${index}`,
        page_start: index + 1,
        page_end: index + 1,
      }),
    );
    const result = checkMerge(many);
    expect(result.ok).toBe(false);
    expect(result.ok === false && result.reason).toContain("50");
  });
});
