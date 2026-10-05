import { describe, expect, it } from "vitest";

import { certificateDetailSchema } from "@/lib/schemas/certificates";

/**
 * The record screen parses what the server sends before it renders anything, so a
 * schema that is stricter than the server is indistinguishable from an outage: the
 * parse throws, the query retries, and the screen shows a skeleton forever.
 *
 * The payload below is a real one, trimmed - an entry filed by the pipeline rather
 * than typed in by a clerk. Entries typed in carry no provenance at all, which is why
 * a suite built on them missed this.
 */
const READ_FROM_A_SCAN = {
  id: "0e4641be-f11a-4ce4-a614-efff9a33b869",
  certificate_type_id: "3fc1b5aa-b37d-4f23-bc96-fd1d73a5ea64",
  certificate_number: "BC-2019-004471",
  registration_number: "REG/LHR/2019/88213",
  primary_name: "Ayesha Noor Malik",
  secondary_name: null,
  event_date: "1987-03-14",
  event_date_role: "birth_date",
  registration_date: "2019-04-02",
  issue_date: "2019-04-09",
  issuing_authority: "Union Council 42, Lahore",
  status: "ACTIVE",
  duplicate_status: "NONE",
  needs_review: true,
  row_confidence: 0.9025,
  source: "EXTRACTION",
  created_at: "2026-10-05T05:20:02.818202Z",
  updated_at: "2026-10-05T05:20:02.818202Z",
  schema_version_id: "b45ee689-d3bb-4ed1-a30f-aa2d7958b4d7",
  record_version: 1,
  duplicate_of_id: null,
  superseded_by_id: null,
  values: { sex: "F", date_of_birth: "1987-03-14" },
  confidences: { sex: 1, date_of_birth: 1 },
  provenance: {
    sex: {
      method: "rule",
      label: "sex",
      snippet: "Female",
      page_number: 1,
      bbox: { x0: 0.45238, x1: 0.50839, y0: 0.31045, y1: 0.32232 },
    },
  },
  names: [],
  dates: [],
  documents: [],
};

describe("certificateDetailSchema", () => {
  it("accepts an entry the pipeline filed, bounding box and all", () => {
    const parsed = certificateDetailSchema.parse(READ_FROM_A_SCAN);
    expect(parsed.provenance.sex?.bbox).toEqual({
      x0: 0.45238,
      x1: 0.50839,
      y0: 0.31045,
      y1: 0.32232,
    });
  });

  it("keeps the keys the screen reads back out of provenance", () => {
    // The screen looks for exactly these three. It once looked for "text" and "page",
    // which no stage writes, so every value rendered without its provenance line.
    const { provenance } = certificateDetailSchema.parse(READ_FROM_A_SCAN);
    expect(provenance.sex?.method).toBe("rule");
    expect(provenance.sex?.snippet).toBe("Female");
    expect(provenance.sex?.page_number).toBe(1);
  });

  it("still accepts an entry a clerk typed, which carries no provenance", () => {
    const typedIn = { ...READ_FROM_A_SCAN, source: "MANUAL", provenance: {} };
    expect(certificateDetailSchema.parse(typedIn).provenance).toEqual({});
  });
});
