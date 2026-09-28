import { describe, expect, it } from "vitest";

import {
  MANUAL_CONFIDENCE,
  MANUAL_METHOD,
  applyApproval,
  applyFieldEdit,
  applyFieldEditToDetail,
  cleanFieldValue,
  flattenRowPages,
  isExtraField,
  mapRowsInPages,
  replaceRowInPages,
  totalFromPages,
} from "@/lib/rows-optimistic";
import type { RowDetail, RowPage, RowSummary } from "@/lib/schemas/rows";

const NOW = "2026-02-02T10:00:00.000Z";

function makeRow(overrides: Partial<RowSummary> = {}): RowSummary {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    unit_id: "22222222-2222-4222-8222-222222222222",
    document_id: "33333333-3333-4333-8333-333333333333",
    batch_id: "44444444-4444-4444-8444-444444444444",
    serial_no: 7,
    file_name: "births-1998.pdf",
    page_start: 3,
    page_end: 3,
    certificate_type: "BIRTH",
    review_status: "NEEDS_REVIEW",
    row_confidence: 0.72,
    flags: ["LOW_FIELD_CONFIDENCE"],
    fields: { child_full_name: "Tarig Ahmed", date_of_birth: "1998-03-04" },
    field_confidences: { child_full_name: 0.61, date_of_birth: 0.93 },
    extra_fields: { form_number: "B-12" },
    ocr_used: true,
    detected_language: "urd",
    reviewed_at: null,
    updated_at: "2026-01-01T00:00:00.000Z",
    ...overrides,
  };
}

function makePage(items: RowSummary[], total?: number): RowPage {
  return {
    items,
    meta: { next_cursor: null, has_more: false, limit: 100, ...(total === undefined ? {} : { total }) },
  };
}

describe("cleanFieldValue", () => {
  it("trims, and treats an emptied box as a cleared field", () => {
    expect(cleanFieldValue("  Tariq  ")).toBe("Tariq");
    expect(cleanFieldValue("   ")).toBeNull();
    expect(cleanFieldValue(null)).toBeNull();
  });
});

describe("isExtraField", () => {
  it("knows a field this certificate type does not declare", () => {
    const row = makeRow();
    expect(isExtraField(row, "child_full_name")).toBe(false);
    expect(isExtraField(row, "form_number")).toBe(true);
    // Declared by DEATH, not by BIRTH.
    expect(isExtraField(row, "cause_of_death")).toBe(true);
  });
});

describe("applyFieldEdit", () => {
  it("stores a typed value at full confidence, as the server does", () => {
    const edited = applyFieldEdit(makeRow(), "child_full_name", "  Tariq Ahmed ", NOW);
    expect(edited.fields.child_full_name).toBe("Tariq Ahmed");
    expect(edited.field_confidences.child_full_name).toBe(MANUAL_CONFIDENCE);
    expect(edited.reviewed_at).toBe(NOW);
  });

  it("removes the field and its confidence when the box is emptied", () => {
    const edited = applyFieldEdit(makeRow(), "date_of_birth", "", NOW);
    expect("date_of_birth" in edited.fields).toBe(false);
    expect("date_of_birth" in edited.field_confidences).toBe(false);
  });

  it("leaves every other field alone", () => {
    const edited = applyFieldEdit(makeRow(), "child_full_name", "Tariq", NOW);
    expect(edited.fields.date_of_birth).toBe("1998-03-04");
    expect(edited.field_confidences.date_of_birth).toBe(0.93);
  });

  it("does not guess at the review status, which the server re-scores", () => {
    expect(applyFieldEdit(makeRow(), "child_full_name", "Tariq", NOW).review_status).toBe(
      "NEEDS_REVIEW",
    );
  });

  it("writes an undeclared field into the extras, not into the columns", () => {
    const edited = applyFieldEdit(makeRow(), "form_number", "B-13", NOW);
    expect(edited.extra_fields.form_number).toBe("B-13");
    expect("form_number" in edited.fields).toBe(false);
  });

  it("clears an extra field rather than leaving an empty one behind", () => {
    const edited = applyFieldEdit(makeRow(), "form_number", "  ", NOW);
    expect("form_number" in edited.extra_fields).toBe(false);
  });

  it("does not mutate the row it was given, so a rollback still has the old one", () => {
    const row = makeRow();
    applyFieldEdit(row, "child_full_name", "Tariq", NOW);
    expect(row.fields.child_full_name).toBe("Tarig Ahmed");
    expect(row.field_confidences.child_full_name).toBe(0.61);
  });
});

describe("applyFieldEditToDetail", () => {
  const detail: RowDetail = {
    ...makeRow(),
    values: [
      {
        name: "child_full_name",
        value: "Tarig Ahmed",
        confidence: 0.61,
        method: "rule",
        page_number: 3,
        bbox: { x0: 0.1, y0: 0.2, x1: 0.5, y1: 0.25 },
        snippet: "Tarig Ahmed",
        label: "Name of child",
        flags: [],
        issues: [],
      },
      {
        name: "date_of_birth",
        value: "1998-03-04",
        confidence: 0.93,
        method: "rule",
        page_number: 3,
        bbox: null,
        snippet: null,
        label: "Date of birth",
        flags: [],
        issues: [],
      },
    ],
    issues: [],
    page_numbers: [3],
  };

  it("marks the edited value as a person's, and stops pointing at the old text", () => {
    const edited = applyFieldEditToDetail(detail, "child_full_name", "Tariq Ahmed", NOW);
    const value = edited.values.find((entry) => entry.name === "child_full_name");
    expect(value?.value).toBe("Tariq Ahmed");
    expect(value?.method).toBe(MANUAL_METHOD);
    expect(value?.confidence).toBe(MANUAL_CONFIDENCE);
    expect(value?.bbox).toBeNull();
    expect(value?.snippet).toBeNull();
  });

  it("keeps the box on a value that was cleared rather than retyped", () => {
    const edited = applyFieldEditToDetail(detail, "child_full_name", "", NOW);
    const value = edited.values.find((entry) => entry.name === "child_full_name");
    expect(value?.value).toBeNull();
    expect(value?.bbox).not.toBeNull();
  });

  it("leaves the other fields and the row's page list untouched", () => {
    const edited = applyFieldEditToDetail(detail, "child_full_name", "Tariq", NOW);
    expect(edited.values.find((entry) => entry.name === "date_of_birth")?.confidence).toBe(0.93);
    expect(edited.page_numbers).toEqual([3]);
  });
});

describe("applyApproval", () => {
  it("marks the row as checked by a person and stamps when", () => {
    const approved = applyApproval(makeRow(), NOW);
    expect(approved.review_status).toBe("MANUALLY_APPROVED");
    expect(approved.reviewed_at).toBe(NOW);
  });
});

describe("page helpers", () => {
  const first = makeRow({ id: "row-1", serial_no: 1 });
  const second = makeRow({ id: "row-2", serial_no: 2 });
  const data = {
    pages: [makePage([first], 2), makePage([second])],
    pageParams: [null, "cursor-1"] as (string | null)[],
  };

  it("replaces one row wherever it happens to sit", () => {
    const updated = replaceRowInPages(data, { ...second, serial_no: 99 });
    expect(updated.pages[1]?.items[0]?.serial_no).toBe(99);
    expect(updated.pages[0]?.items[0]?.serial_no).toBe(1);
    expect(updated.pageParams).toEqual([null, "cursor-1"]);
  });

  it("changes only the chosen rows on a bulk approval", () => {
    const updated = mapRowsInPages(data, new Set(["row-2"]), (row) => applyApproval(row, NOW));
    expect(updated.pages[0]?.items[0]?.review_status).toBe("NEEDS_REVIEW");
    expect(updated.pages[1]?.items[0]?.review_status).toBe("MANUALLY_APPROVED");
  });

  it("flattens the loaded pages in order", () => {
    expect(flattenRowPages(data).map((row) => row.id)).toEqual(["row-1", "row-2"]);
    expect(flattenRowPages(undefined)).toEqual([]);
  });

  it("finds the total on whichever page carries it", () => {
    expect(totalFromPages(data)).toBe(2);
    expect(totalFromPages({ pages: [makePage([first])] })).toBeNull();
    expect(totalFromPages(undefined)).toBeNull();
  });
});
