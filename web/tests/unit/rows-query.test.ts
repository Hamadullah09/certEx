import { describe, expect, it } from "vitest";

import {
  ROWS_PAGE_SIZE,
  buildRowsQuery,
  hasAnyRowFilter,
  normalizeRowFilters,
} from "@/lib/rows-query";

function parse(query: string): URLSearchParams {
  return new URLSearchParams(query);
}

describe("normalizeRowFilters", () => {
  it("drops empty filters so an untouched search box shares one cache entry", () => {
    expect(normalizeRowFilters({ search: "", flag: "   " })).toEqual({});
    expect(normalizeRowFilters({})).toEqual({});
  });

  it("trims the text filters", () => {
    expect(normalizeRowFilters({ search: "  Ayesha  " })).toEqual({ search: "Ayesha" });
  });

  it("keeps the enumerated filters as they are", () => {
    expect(normalizeRowFilters({ status: "NEEDS_REVIEW", type: "BIRTH" })).toEqual({
      status: "NEEDS_REVIEW",
      type: "BIRTH",
    });
  });
});

describe("hasAnyRowFilter", () => {
  it("ignores filters that are only whitespace", () => {
    expect(hasAnyRowFilter({ search: "  " })).toBe(false);
    expect(hasAnyRowFilter({ flag: "CNIC_INVALID" })).toBe(true);
  });
});

describe("buildRowsQuery", () => {
  it("always sends a limit", () => {
    expect(parse(buildRowsQuery({})).get("limit")).toBe(String(ROWS_PAGE_SIZE));
  });

  it("sends each filter under the name the API expects", () => {
    const params = parse(
      buildRowsQuery({
        status: "NEEDS_REVIEW",
        type: "MARRIAGE",
        flag: "CNIC_INVALID",
        search: "Ayesha",
      }),
    );
    expect(params.get("filter[status]")).toBe("NEEDS_REVIEW");
    expect(params.get("filter[type]")).toBe("MARRIAGE");
    expect(params.get("filter[flag]")).toBe("CNIC_INVALID");
    expect(params.get("search")).toBe("Ayesha");
  });

  it("omits what was not filtered on", () => {
    const params = parse(buildRowsQuery({ search: "" }));
    expect(params.has("search")).toBe(false);
    expect(params.has("filter[status]")).toBe(false);
    expect(params.has("cursor")).toBe(false);
  });

  it("asks for the total on the first page only", () => {
    expect(parse(buildRowsQuery({}, { includeTotal: true })).get("include_total")).toBe("true");
    expect(
      parse(buildRowsQuery({}, { includeTotal: true, cursor: "abc" })).has("include_total"),
    ).toBe(false);
  });

  it("passes the cursor through untouched", () => {
    const cursor = "eyJ0IjoiMjAyNi0wMS0wMSIsImkiOiJhYmMifQ";
    expect(parse(buildRowsQuery({}, { cursor })).get("cursor")).toBe(cursor);
  });

  it("honours a smaller page size", () => {
    expect(parse(buildRowsQuery({}, { limit: 25 })).get("limit")).toBe("25");
  });
});
