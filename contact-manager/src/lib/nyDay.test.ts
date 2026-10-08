import { describe, it, expect } from "vitest";
import { newYorkDate, startOfNewYorkDay } from "./nyDay";

describe("startOfNewYorkDay", () => {
  it("is midnight in New York during daylight time", () => {
    expect(startOfNewYorkDay(new Date("2026-10-09T15:30:00Z")).toISOString()).toBe("2026-10-09T04:00:00.000Z");
  });

  it("is midnight in New York during standard time", () => {
    expect(startOfNewYorkDay(new Date("2026-12-01T03:00:00Z")).toISOString()).toBe("2026-11-30T05:00:00.000Z");
  });

  it("uses New York's date, not UTC's, late in the evening", () => {
    // 23:30 in New York on Oct 9 is already Oct 10 in UTC.
    expect(startOfNewYorkDay(new Date("2026-10-10T03:30:00Z")).toISOString()).toBe("2026-10-09T04:00:00.000Z");
  });
});

describe("newYorkDate", () => {
  it("uses the New York calendar day, not UTC", () => {
    expect(newYorkDate(new Date("2026-10-09T03:30:00Z"))).toBe("2026-10-08");
    expect(newYorkDate(new Date("2026-10-09T04:30:00Z"))).toBe("2026-10-09");
    expect(newYorkDate(new Date("2026-01-15T04:59:00Z"))).toBe("2026-01-14");
  });
});
