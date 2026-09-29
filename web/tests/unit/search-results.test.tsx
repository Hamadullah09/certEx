import * as React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SearchResults } from "@/components/register/search-results";
import type {
  CertificateSummary,
  MatchKind,
  SearchHit,
  SearchResponse,
} from "@/lib/schemas/certificates";

/*
 * The result list is the screen a clerk reads under pressure, so what it says about
 * *how* a result was found matters as much as the result. An exact number match and a
 * similar-spelling guess must not look the same.
 */

function summary(overrides: Partial<CertificateSummary> = {}): CertificateSummary {
  return {
    id: "cert-1",
    certificate_type_id: "type-1",
    certificate_number: "BC/LHR/2019/1001",
    registration_number: null,
    primary_name: "Ayesha Noor Malik",
    secondary_name: null,
    event_date: "2019-04-03",
    event_date_role: "birth_date",
    registration_date: null,
    issue_date: null,
    issuing_authority: null,
    status: "ACTIVE",
    duplicate_status: "NONE",
    needs_review: false,
    row_confidence: 1,
    source: "MANUAL",
    created_at: "2026-09-01T10:00:00Z",
    ...overrides,
  };
}

function hit(overrides: Partial<SearchHit> = {}): SearchHit {
  return {
    certificate: summary(),
    match: "certificate_number",
    same_name_count: 1,
    ...overrides,
  };
}

function response(overrides: Partial<SearchResponse> = {}): SearchResponse {
  const items = overrides.items ?? [hit()];
  return {
    items,
    match: (overrides.match ?? "certificate_number") as MatchKind,
    total: overrides.total ?? items.length,
    limit: overrides.limit ?? 25,
    offset: overrides.offset ?? 0,
    has_more: overrides.has_more ?? false,
  };
}

function show(results: SearchResponse, onOffsetChange = vi.fn()) {
  render(
    <SearchResults
      results={results}
      isPending={false}
      error={null}
      offset={results.offset}
      onOffsetChange={onOffsetChange}
    />,
  );
  return onOffsetChange;
}

describe("what the list says about the match", () => {
  it("calls an exact number match an exact match", () => {
    show(response({ match: "certificate_number" }));
    expect(screen.getByRole("status")).toHaveTextContent("exactly");
  });

  it("says a similar name needs checking", () => {
    show(response({ match: "similar_name" }));
    expect(screen.getByRole("status")).toHaveTextContent("check carefully");
  });

  it("says a prefix match is not exact", () => {
    show(response({ match: "number_prefix" }));
    expect(screen.getByRole("status")).toHaveTextContent("No exact match");
  });

  it("explains that a name search found no such number", () => {
    show(response({ match: "name" }));
    expect(screen.getByRole("status")).toHaveTextContent("No certificate with that number");
  });

  it("counts what it is showing out of what there is", () => {
    show(response({ total: 40, limit: 25, items: [hit()], has_more: true }));
    expect(screen.getByRole("status")).toHaveTextContent("Showing 1–1 of 40");
  });
});

describe("telling namesakes apart", () => {
  it("says how many share a name", () => {
    show(response({ items: [hit({ same_name_count: 4 })] }));
    expect(screen.getByText("4 with this name")).toBeInTheDocument();
  });

  it("says nothing when the name is the only one", () => {
    show(response({ items: [hit({ same_name_count: 1 })] }));
    expect(screen.queryByText(/with this name/)).not.toBeInTheDocument();
  });

  it("shows the date, which is what distinguishes two people with one name", () => {
    // Formatted the way the component formats it, so the assertion does not depend
    // on the locale the tests happen to run under.
    const printed = new Date("2019-04-03T00:00:00").toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
    show(response());
    expect(screen.getByText(printed)).toBeInTheDocument();
  });

  it("shows the certificate number for every result", () => {
    show(response());
    expect(screen.getByText(/BC\/LHR\/2019\/1001/)).toBeInTheDocument();
  });
});

describe("flags on a result", () => {
  it("marks an entry that may be a duplicate", () => {
    show(response({ items: [hit({ certificate: summary({ duplicate_status: "SUSPECTED" }) })] }));
    expect(screen.getByText("Possible duplicate")).toBeInTheDocument();
  });

  it("marks an entry waiting for review", () => {
    show(response({ items: [hit({ certificate: summary({ needs_review: true }) })] }));
    expect(screen.getByText("Needs review")).toBeInTheDocument();
  });

  it("marks a void entry, which must never be used as an answer", () => {
    show(response({ items: [hit({ certificate: summary({ status: "VOID" }) })] }));
    expect(screen.getByText("void")).toBeInTheDocument();
  });

  it("says when a name was never recorded rather than leaving a blank", () => {
    show(response({ items: [hit({ certificate: summary({ primary_name: null }) })] }));
    expect(screen.getByText("Name not recorded")).toBeInTheDocument();
  });
});

describe("Urdu values", () => {
  it("lays a Urdu name out right to left", () => {
    show(
      response({
        items: [hit({ certificate: summary({ primary_name: "عائشہ نور ملک" }) })],
      }),
    );
    const name = screen.getByText("عائشہ نور ملک");
    expect(name).toHaveAttribute("dir", "rtl");
    expect(name).toHaveAttribute("lang", "ur");
  });

  it("leaves a Latin name alone", () => {
    show(response());
    expect(screen.getByText("Ayesha Noor Malik")).toHaveAttribute("dir", "ltr");
  });
});

describe("paging", () => {
  it("offers no paging when everything fits on one page", () => {
    show(response({ total: 3, limit: 25 }));
    expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
  });

  it("moves forward by a page", () => {
    const onOffsetChange = show(response({ total: 40, limit: 25, has_more: true }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(onOffsetChange).toHaveBeenCalledWith(25);
  });

  it("will not go back past the first page", () => {
    show(response({ total: 40, limit: 25, has_more: true }));
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
  });

  it("moves back by a page", () => {
    const onOffsetChange = show(
      response({ total: 40, limit: 25, offset: 25, has_more: false }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(onOffsetChange).toHaveBeenCalledWith(0);
  });

  it("will not go forward past the last page", () => {
    show(response({ total: 40, limit: 25, offset: 25, has_more: false }));
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });
});

describe("nothing found", () => {
  it("suggests what to try instead of showing an empty list", () => {
    show(response({ items: [], match: "none", total: 0 }));
    expect(screen.getByText("Nothing found")).toBeInTheDocument();
    expect(screen.getByText(/partial number of three characters/)).toBeInTheDocument();
  });
});

describe("failure", () => {
  it("shows the server's message rather than an empty list", () => {
    render(
      <SearchResults
        results={undefined}
        isPending={false}
        error={new Error("The register is unreachable")}
        offset={0}
        onOffsetChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("The register is unreachable");
  });
});
