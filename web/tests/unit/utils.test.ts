import { describe, expect, it } from "vitest";

import { confidenceBand, formatBytes, formatConfidence } from "@/lib/utils";

describe("confidenceBand", () => {
  it("bands at the server's review thresholds", () => {
    expect(confidenceBand(0.95)).toBe("high");
    expect(confidenceBand(0.9)).toBe("high");
    expect(confidenceBand(0.89)).toBe("medium");
    expect(confidenceBand(0.6)).toBe("medium");
    expect(confidenceBand(0.59)).toBe("low");
    expect(confidenceBand(0)).toBe("low");
  });

  it("treats a missing score as unknown rather than low", () => {
    expect(confidenceBand(null)).toBe("unknown");
    expect(confidenceBand(undefined)).toBe("unknown");
    expect(confidenceBand(Number.NaN)).toBe("unknown");
  });
});

describe("formatConfidence", () => {
  it("renders whole percentages", () => {
    expect(formatConfidence(0.9)).toBe("90%");
    expect(formatConfidence(0.876)).toBe("88%");
    expect(formatConfidence(1)).toBe("100%");
  });

  it("renders a dash when there is no score", () => {
    expect(formatConfidence(null)).toBe("-");
  });
});

describe("formatBytes", () => {
  it("scales units", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});
