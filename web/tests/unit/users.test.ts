import { describe, expect, it } from "vitest";

import { displayName, initials } from "@/lib/schemas/users";

/**
 * Names on screen. The app showed an email address everywhere a name belonged, and an
 * account can still have no name at all - one made before names existed, or by a
 * seeding script - so neither helper is allowed to render nothing.
 */
describe("displayName", () => {
  it("uses the name when there is one", () => {
    expect(displayName({ full_name: "Ayesha Noor", email: "a@example.com" })).toBe("Ayesha Noor");
  });

  it("falls back to the part of the address before the @", () => {
    // Which reads as a name far more often than the whole address does.
    expect(displayName({ full_name: null, email: "ayesha.noor@office.gov.pk" })).toBe(
      "ayesha.noor",
    );
    expect(displayName({ email: "admin@example.com" })).toBe("admin");
  });

  it("ignores a name that is only spaces", () => {
    expect(displayName({ full_name: "   ", email: "admin@example.com" })).toBe("admin");
  });
});

describe("initials", () => {
  it("takes the first letter of the first two words", () => {
    expect(initials({ full_name: "Ayesha Noor Malik", email: "a@example.com" })).toBe("AN");
  });

  it("splits an address that uses dots or underscores as spaces", () => {
    expect(initials({ full_name: null, email: "ayesha.noor@office.gov.pk" })).toBe("AN");
    expect(initials({ full_name: null, email: "ayesha_noor@office.gov.pk" })).toBe("AN");
  });

  it("falls back to the first two characters of a single word", () => {
    expect(initials({ full_name: "Ayesha", email: "a@example.com" })).toBe("AY");
    expect(initials({ full_name: null, email: "admin@example.com" })).toBe("AD");
  });

  it("never renders nothing", () => {
    expect(initials({ full_name: null, email: "x@example.com" }).length).toBeGreaterThan(0);
  });
});
