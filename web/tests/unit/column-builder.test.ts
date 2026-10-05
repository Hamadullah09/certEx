import { describe, expect, it } from "vitest";

import {
  columnLetter,
  keyFromLabel,
  newColumn,
  problemsWith,
  type ColumnDraft,
} from "@/components/batches/column-builder";

/**
 * The column builder decides what a batch extracts for the rest of its life, so the
 * rules it enforces before saving are the ones that would otherwise surface as a
 * server refusal naming a field the operator never typed - or worse, as a batch that
 * reads a million documents into a CSV nobody can use.
 */

function draft(overrides: Partial<ColumnDraft> = {}): ColumnDraft {
  return newColumn({
    label: "Father Name",
    name: "father_name",
    isIdentifier: false,
    keyFollowsLabel: false,
    ...overrides,
  });
}

describe("keyFromLabel", () => {
  it("turns what a person types into what the CSV can carry", () => {
    expect(keyFromLabel("Father Name")).toBe("father_name");
    expect(keyFromLabel("Certificate No")).toBe("certificate_no");
    expect(keyFromLabel("DOB")).toBe("dob");
  });

  it("drops the apostrophes a records office writes", () => {
    // Both the typed apostrophe and the one a word processor substitutes.
    expect(keyFromLabel("Father's Name")).toBe("fathers_name");
    expect(keyFromLabel("Father’s Name")).toBe("fathers_name");
  });

  it("collapses punctuation and spacing rather than leaving it in the header", () => {
    expect(keyFromLabel("Place  of   Birth")).toBe("place_of_birth");
    expect(keyFromLabel("Address (permanent)")).toBe("address_permanent");
    expect(keyFromLabel("  Name  ")).toBe("name");
  });

  it("gives a key that starts with a letter, because the server requires one", () => {
    expect(keyFromLabel("2020 register")).toBe("f_2020_register");
  });

  it("returns nothing for a label with no usable characters", () => {
    // The operator then types a key themselves; silently inventing one would put a
    // column in the CSV under a name nobody chose.
    expect(keyFromLabel("")).toBe("");
    expect(keyFromLabel("---")).toBe("");
  });
});

describe("columnLetter", () => {
  it("numbers columns the way a spreadsheet does", () => {
    expect(columnLetter(0)).toBe("A");
    expect(columnLetter(5)).toBe("F");
    expect(columnLetter(25)).toBe("Z");
  });

  it("carries past the alphabet", () => {
    expect(columnLetter(26)).toBe("AA");
    expect(columnLetter(27)).toBe("AB");
    expect(columnLetter(51)).toBe("AZ");
    expect(columnLetter(52)).toBe("BA");
  });
});

describe("problemsWith", () => {
  it("accepts a usable set of columns", () => {
    expect(
      problemsWith([
        draft({ label: "Certificate No", name: "certificate_no", isIdentifier: true }),
        draft(),
      ]),
    ).toEqual([]);
  });

  it("asks for at least one column", () => {
    expect(problemsWith([])).toEqual(["Add at least one column."]);
  });

  it("insists that one column is the certificate number", () => {
    const problems = problemsWith([draft()]);
    expect(problems).toContain("Mark which column holds the certificate number.");
  });

  it("refuses two columns claiming to be the certificate number", () => {
    const problems = problemsWith([
      draft({ name: "a_no", isIdentifier: true }),
      draft({ name: "b_no", isIdentifier: true }),
    ]);
    expect(problems).toContain("Only one column can be the certificate number.");
  });

  it("catches two columns sharing a key before the server does", () => {
    const problems = problemsWith([
      draft({ name: "father_name", isIdentifier: true }),
      draft({ name: "father_name" }),
    ]);
    expect(problems.some((problem) => problem.includes("father_name"))).toBe(true);
  });

  it("catches a key the server would reject", () => {
    const problems = problemsWith([draft({ name: "Father Name", isIdentifier: true })]);
    expect(problems.some((problem) => problem.includes("Father Name"))).toBe(true);
  });

  it("asks for a name on every column", () => {
    const problems = problemsWith([draft({ label: "  ", isIdentifier: true })]);
    expect(problems).toContain("Every column needs a name.");
  });

  it("asks for a key when the label produced none", () => {
    const problems = problemsWith([draft({ label: "---", name: "", isIdentifier: true })]);
    expect(problems).toContain(
      "Every column needs a key for the CSV. Type one, or change the name.",
    );
  });
});

describe("newColumn", () => {
  it("gives each column an id that survives reordering", () => {
    expect(newColumn().id).not.toBe(newColumn().id);
  });

  it("starts with the key following the label, so typing one fills the other", () => {
    expect(newColumn().keyFollowsLabel).toBe(true);
  });
});

describe("carrying the wordings a form prints", () => {
  it("starts a column with no extra wordings", () => {
    expect(newColumn().otherWordings).toEqual([]);
  });

  it("keeps them on a column seeded from an existing schema", () => {
    // The case that broke a live batch: the standard "Certificate number" column
    // matches a form printing "Certificate No." only through these, and without them
    // the value was filed as an unrecognised extra - leaving the record with no
    // certificate number, which the register cannot file at all.
    const seeded = newColumn({
      label: "Certificate number",
      name: "certificate_number",
      otherWordings: ["certificate no", "cert no"],
    });
    expect(seeded.otherWordings).toEqual(["certificate no", "cert no"]);
  });
});
