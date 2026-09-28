import { describe, expect, it } from "vitest";

import {
  formatOcrLanguages,
  parseOcrLanguages,
  settingsFromBatchSettings,
  unknownOcrLanguages,
  workspaceSettingsSchema,
} from "@/lib/schemas/settings";

describe("parseOcrLanguages", () => {
  it("splits the form the engine is given", () => {
    expect(parseOcrLanguages("eng+urd")).toEqual(["eng", "urd"]);
  });

  it("tolerates spacing and empty segments", () => {
    expect(parseOcrLanguages(" eng + urd ")).toEqual(["eng", "urd"]);
    expect(parseOcrLanguages("eng++urd")).toEqual(["eng", "urd"]);
  });

  it("is empty when nothing was configured", () => {
    expect(parseOcrLanguages(null)).toEqual([]);
    expect(parseOcrLanguages("")).toEqual([]);
  });
});

describe("formatOcrLanguages", () => {
  it("round-trips", () => {
    expect(formatOcrLanguages(parseOcrLanguages("eng+urd"))).toBe("eng+urd");
  });
});

describe("unknownOcrLanguages", () => {
  it("names the codes the form has no box for, so they are not silently dropped", () => {
    expect(unknownOcrLanguages(["eng", "ara", "urd"])).toEqual(["ara"]);
    expect(unknownOcrLanguages(["eng", "urd"])).toEqual([]);
  });
});

describe("settingsFromBatchSettings", () => {
  it("reads what a batch records and nothing more", () => {
    expect(
      settingsFromBatchSettings({
        ocr_languages: "eng+urd",
        confidence_auto_approve: 0.95,
        confidence_review_floor: 0.6,
      }),
    ).toEqual({
      ocr_languages: ["eng", "urd"],
      confidence_auto_approve: 0.95,
      confidence_review_floor: 0.6,
    });
  });

  it("omits what the batch did not carry, rather than inventing a default", () => {
    expect(settingsFromBatchSettings({ ocr_languages: null })).toEqual({});
    expect(settingsFromBatchSettings(null)).toEqual({});
    expect(settingsFromBatchSettings({ confidence_auto_approve: 1 })).toEqual({
      confidence_auto_approve: 1,
    });
  });

  it("never reports retention, which is not readable from a batch", () => {
    expect(
      settingsFromBatchSettings({ ocr_languages: "eng", confidence_auto_approve: 1 }),
    ).not.toHaveProperty("retention_days");
  });
});

describe("workspaceSettingsSchema", () => {
  const valid = {
    confidence_auto_approve: 0.95,
    confidence_review_floor: 0.6,
    ocr_languages: ["eng", "urd"],
    retention_days: 90,
  };

  it("accepts a sound configuration", () => {
    expect(workspaceSettingsSchema.safeParse(valid).success).toBe(true);
  });

  it("refuses a review floor above the accept level, as the server does on boot", () => {
    const result = workspaceSettingsSchema.safeParse({
      ...valid,
      confidence_review_floor: 0.99,
      confidence_auto_approve: 0.9,
    });
    expect(result.success).toBe(false);
    expect(result.success === false && result.error.issues[0]?.path).toEqual([
      "confidence_review_floor",
    ]);
  });

  it("refuses no languages at all, which would leave scans unreadable", () => {
    expect(workspaceSettingsSchema.safeParse({ ...valid, ocr_languages: [] }).success).toBe(false);
  });

  it("refuses a retention of less than a day", () => {
    expect(workspaceSettingsSchema.safeParse({ ...valid, retention_days: 0 }).success).toBe(false);
  });
});
