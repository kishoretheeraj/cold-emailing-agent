import { describe, it, expect } from "vitest";
import { pickVerdictVariant } from "./applicationBadges";

describe("pickVerdictVariant", () => {
  it("maps strong to emerald", () => {
    expect(pickVerdictVariant("strong")).toBe("emerald");
  });
  it("maps maybe to amber", () => {
    expect(pickVerdictVariant("maybe")).toBe("amber");
  });
  it("maps no to red", () => {
    expect(pickVerdictVariant("no")).toBe("red");
  });
  it("maps null to default", () => {
    expect(pickVerdictVariant(null)).toBe("default");
  });
});
