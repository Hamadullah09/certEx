import { describe, expect, it } from "vitest";

import { isRtlText, textDirection, valueTextAttributes } from "@/lib/text-direction";

describe("textDirection", () => {
  it("reads an Urdu name right to left", () => {
    expect(textDirection("محمد علی")).toBe("rtl");
  });

  it("reads a Latin name left to right", () => {
    expect(textDirection("Muhammad Ali")).toBe("ltr");
  });

  it("leaves dates, CNICs and other all-digit values alone", () => {
    expect(textDirection("1998-03-04")).toBe("ltr");
    expect(textDirection("42101-1234567-8")).toBe("ltr");
    expect(textDirection("12/05/1998")).toBe("ltr");
  });

  it("follows the first strong character in a mixed value", () => {
    // A registry that prints "Lahore لاہور" reads as English; the other way round
    // it reads as Urdu, which is what the first-strong rule is for.
    expect(textDirection("Lahore لاہور")).toBe("ltr");
    expect(textDirection("لاہور Lahore")).toBe("rtl");
  });

  it("skips leading digits and punctuation before deciding", () => {
    expect(textDirection("12 محمد")).toBe("rtl");
    expect(textDirection("(محمد)")).toBe("rtl");
  });

  it("treats nothing at all as left to right", () => {
    expect(textDirection(null)).toBe("ltr");
    expect(textDirection(undefined)).toBe("ltr");
    expect(textDirection("")).toBe("ltr");
    expect(textDirection("   ")).toBe("ltr");
  });
});

describe("isRtlText", () => {
  it("agrees with textDirection", () => {
    expect(isRtlText("عائشہ")).toBe(true);
    expect(isRtlText("Ayesha")).toBe(false);
  });
});

describe("valueTextAttributes", () => {
  it("tags a right-to-left value as Urdu, which is what picks up the Nastaliq face", () => {
    expect(valueTextAttributes("عائشہ")).toEqual({ dir: "rtl", lang: "ur" });
  });

  it("never tags an English value with a language it is not in", () => {
    expect(valueTextAttributes("Ayesha")).toEqual({ dir: "ltr" });
    expect(valueTextAttributes(null)).toEqual({ dir: "ltr" });
  });
});
