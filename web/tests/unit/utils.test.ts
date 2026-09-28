import { describe, expect, it } from "vitest";

import {
  confidenceBand,
  confidenceClass,
  describeConfidence,
  formatBytes,
  formatConfidence,
} from "@/lib/utils";

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

describe("confidenceClass", () => {
  it("shades from the confidence ramp, and only from it", () => {
    expect(confidenceClass(0.95)).toContain("bg-confidence-high");
    expect(confidenceClass(0.7)).toContain("bg-confidence-medium");
    expect(confidenceClass(0.2)).toContain("bg-confidence-low");
  });

  it("pairs every fill with its own ink, so a shaded cell is always legible", () => {
    expect(confidenceClass(0.95)).toContain("text-confidence-high-foreground");
    expect(confidenceClass(0.7)).toContain("text-confidence-medium-foreground");
    expect(confidenceClass(0.2)).toContain("text-confidence-low-foreground");
  });

  it("shades nothing when there is no score to shade by", () => {
    expect(confidenceClass(null)).toBe("");
  });
});

describe("describeConfidence", () => {
  it("says the number and the band, which is what the aria-label carries", () => {
    expect(describeConfidence(0.95)).toBe("95% - high confidence");
    expect(describeConfidence(0.7)).toContain("worth checking");
    expect(describeConfidence(0.2)).toContain("please check");
  });

  it("says so plainly when nothing was scored", () => {
    expect(describeConfidence(null)).toBe("no confidence score");
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
