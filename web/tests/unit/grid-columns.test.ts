import { describe, expect, it } from "vitest";

import {
  type GridColumn,
  SERIAL_COLUMN_ID,
  TYPE_COLUMN_ID,
  buildGridColumns,
  columnOffset,
  distinctCertificateTypes,
  fieldSpecsFor,
  keepColumnInView,
  totalColumnWidth,
} from "@/lib/grid-columns";
import { COMMON_FIELDS, fieldsFor } from "@/lib/schemas/fields.generated";

describe("buildGridColumns", () => {
  it("puts the serial number first and the certificate type second", () => {
    const columns = buildGridColumns(["BIRTH"]);
    expect(columns[0]?.id).toBe(SERIAL_COLUMN_ID);
    expect(columns[1]?.id).toBe(TYPE_COLUMN_ID);
  });

  it("takes every other column, and its label, from the generated field specs", () => {
    const columns = buildGridColumns(["BIRTH"]);
    const fieldColumns = columns.filter((column) => column.kind === "field");
    expect(fieldColumns.map((column) => column.id)).toEqual(
      fieldsFor("BIRTH").map((spec) => spec.name),
    );
    expect(fieldColumns.map((column) => column.label)).toEqual(
      fieldsFor("BIRTH").map((spec) => spec.label),
    );
  });

  it("gives every column a width, so the header and the virtualised rows line up", () => {
    for (const column of buildGridColumns(["MARRIAGE"])) {
      expect(column.width).toBeGreaterThan(0);
    }
  });

  it("marks the numeric columns, which are the ones set right-aligned", () => {
    const columns = buildGridColumns(["DEATH"]);
    expect(columns.find((column) => column.id === SERIAL_COLUMN_ID)?.numeric).toBe(true);
    expect(columns.find((column) => column.id === "age_at_death")?.numeric).toBe(true);
    expect(columns.find((column) => column.id === "place_of_death")?.numeric).toBeFalsy();
  });
});

describe("fieldSpecsFor", () => {
  it("unions several types without repeating a shared field", () => {
    const specs = fieldSpecsFor(["BIRTH", "DEATH"]);
    const names = specs.map((spec) => spec.name);
    expect(new Set(names).size).toBe(names.length);
    // `sex` and `date_of_birth` are declared by both types.
    expect(names.filter((name) => name === "sex")).toHaveLength(1);
    expect(names).toContain("child_full_name");
    expect(names).toContain("cause_of_death");
  });

  it("keeps the common fields in front, because export order starts with them", () => {
    const names = fieldSpecsFor(["MARRIAGE", "BIRTH"]).map((spec) => spec.name);
    expect(names.slice(0, COMMON_FIELDS.length)).toEqual(COMMON_FIELDS.map((spec) => spec.name));
  });

  it("falls back to the common fields when no type is known yet", () => {
    expect(fieldSpecsFor([])).toEqual([...COMMON_FIELDS]);
  });
});

describe("distinctCertificateTypes", () => {
  it("reports the types present in the declared order, not the order rows arrived", () => {
    expect(
      distinctCertificateTypes([
        { certificate_type: "DEATH" },
        { certificate_type: "BIRTH" },
        { certificate_type: "DEATH" },
      ]),
    ).toEqual(["BIRTH", "DEATH"]);
  });

  it("is empty for no rows", () => {
    expect(distinctCertificateTypes([])).toEqual([]);
  });
});

describe("columnOffset and totalColumnWidth", () => {
  const columns: GridColumn[] = [
    { id: "a", label: "A", kind: "serial", width: 100 },
    { id: "b", label: "B", kind: "type", width: 150 },
    { id: "c", label: "C", kind: "field", width: 200 },
  ];

  it("measures from the left edge of the grid", () => {
    expect(columnOffset(columns, 0)).toBe(0);
    expect(columnOffset(columns, 1)).toBe(100);
    expect(columnOffset(columns, 2)).toBe(250);
  });

  it("totals every column", () => {
    expect(totalColumnWidth(columns)).toBe(450);
  });
});

describe("keepColumnInView", () => {
  const viewport = { scrollLeft: 0, viewportWidth: 500, frozenWidth: 150 };

  it("leaves the scroller alone when the column is already visible", () => {
    expect(keepColumnInView({ offset: 200, width: 100, ...viewport })).toBe(0);
  });

  it("scrolls right just enough to reveal the column's right edge", () => {
    expect(keepColumnInView({ offset: 600, width: 100, ...viewport })).toBe(200);
  });

  it("scrolls left far enough that the column clears the frozen columns", () => {
    expect(
      keepColumnInView({ offset: 300, width: 100, scrollLeft: 400, viewportWidth: 500, frozenWidth: 150 }),
    ).toBe(150);
  });

  it("never scrolls to a negative position for a column inside the frozen strip", () => {
    expect(keepColumnInView({ offset: 0, width: 100, ...viewport })).toBe(0);
  });
});
