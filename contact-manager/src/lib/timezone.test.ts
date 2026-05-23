import { describe, it, expect } from "vitest";
import {
  US_STATES,
  STATE_TO_TIMEZONE,
  getTimezoneForState,
  getTimezoneLabel,
  ianaToTimezoneLabel,
  formatLocalTime,
  getTimezoneDistribution,
} from "./timezone";

describe("US_STATES", () => {
  it("has 51 entries", () => {
    expect(US_STATES).toHaveLength(51);
  });

  it("includes DC", () => {
    expect(US_STATES.some((s) => s.code === "DC")).toBe(true);
  });

  it("is sorted alphabetically by name", () => {
    for (let i = 1; i < US_STATES.length; i++) {
      expect(US_STATES[i].name >= US_STATES[i - 1].name).toBe(true);
    }
  });
});

describe("getTimezoneForState", () => {
  it("returns null for null input", () => {
    expect(getTimezoneForState(null)).toBeNull();
  });

  it("returns null for empty string", () => {
    expect(getTimezoneForState("")).toBeNull();
  });

  it("returns null for unknown code", () => {
    expect(getTimezoneForState("XX")).toBeNull();
  });

  it("maps NY to America/New_York", () => {
    expect(getTimezoneForState("NY")).toBe("America/New_York");
  });

  it("maps CA to America/Los_Angeles", () => {
    expect(getTimezoneForState("CA")).toBe("America/Los_Angeles");
  });

  it("maps AZ to America/Phoenix", () => {
    expect(getTimezoneForState("AZ")).toBe("America/Phoenix");
  });

  it("is case-insensitive", () => {
    expect(getTimezoneForState("ny")).toBe("America/New_York");
  });
});

describe("getTimezoneLabel", () => {
  it("returns null for null input", () => {
    expect(getTimezoneLabel(null)).toBeNull();
  });

  it("returns null for unknown state", () => {
    expect(getTimezoneLabel("XX")).toBeNull();
  });

  it("NY → ET", () => expect(getTimezoneLabel("NY")).toBe("ET"));
  it("MA → ET", () => expect(getTimezoneLabel("MA")).toBe("ET"));
  it("IL → CT", () => expect(getTimezoneLabel("IL")).toBe("CT"));
  it("CO → MT", () => expect(getTimezoneLabel("CO")).toBe("MT"));
  it("CA → PT", () => expect(getTimezoneLabel("CA")).toBe("PT"));
  it("HI → HT", () => expect(getTimezoneLabel("HI")).toBe("HT"));
  it("AK → AK", () => expect(getTimezoneLabel("AK")).toBe("AK"));

  it("AZ → AZ (not MT or PT — no DST zone)", () => {
    expect(getTimezoneLabel("AZ")).toBe("AZ");
  });

  it("IN → ET (America/Indianapolis is Eastern)", () => {
    expect(getTimezoneLabel("IN")).toBe("ET");
  });

  it("MI → ET (America/Detroit is Eastern)", () => {
    expect(getTimezoneLabel("MI")).toBe("ET");
  });
});

describe("split-zone majority mappings", () => {
  it("FL → ET (panhandle is minority CT)", () => {
    expect(getTimezoneLabel("FL")).toBe("ET");
  });

  it("TN → CT (Knoxville is minority ET)", () => {
    expect(getTimezoneLabel("TN")).toBe("CT");
  });

  it("KY → ET (Louisville is majority)", () => {
    expect(getTimezoneLabel("KY")).toBe("ET");
  });

  it("TX → CT (El Paso is minority MT)", () => {
    expect(getTimezoneLabel("TX")).toBe("CT");
  });

  it("ID → MT (Boise is majority)", () => {
    expect(getTimezoneLabel("ID")).toBe("MT");
  });

  it("OR → PT (Malheur County is minority MT)", () => {
    expect(getTimezoneLabel("OR")).toBe("PT");
  });

  it("KS → CT (small western minority)", () => {
    expect(getTimezoneLabel("KS")).toBe("CT");
  });

  it("NE → CT (panhandle minority)", () => {
    expect(getTimezoneLabel("NE")).toBe("CT");
  });

  it("ND → CT (small western minority)", () => {
    expect(getTimezoneLabel("ND")).toBe("CT");
  });

  it("SD → CT (small western minority)", () => {
    expect(getTimezoneLabel("SD")).toBe("CT");
  });
});

describe("ianaToTimezoneLabel", () => {
  it("maps America/New_York → ET", () => {
    expect(ianaToTimezoneLabel("America/New_York")).toBe("ET");
  });

  it("maps America/Chicago → CT", () => {
    expect(ianaToTimezoneLabel("America/Chicago")).toBe("CT");
  });

  it("maps America/Phoenix → AZ", () => {
    expect(ianaToTimezoneLabel("America/Phoenix")).toBe("AZ");
  });

  it("returns null for unknown zone", () => {
    expect(ianaToTimezoneLabel("Europe/London")).toBeNull();
  });
});

describe("formatLocalTime", () => {
  // Fixed moment: 2026-05-23 14:30 UTC
  const FIXED = new Date("2026-05-23T14:30:00Z");

  it("returns null for null state", () => {
    expect(formatLocalTime(null, FIXED)).toBeNull();
  });

  it("returns null for unknown state", () => {
    expect(formatLocalTime("XX", FIXED)).toBeNull();
  });

  it("returns ET time for NY (UTC-4 in May)", () => {
    // 14:30 UTC = 10:30 AM ET (EDT, UTC-4)
    expect(formatLocalTime("NY", FIXED)).toBe("10:30 AM");
  });

  it("returns CT time for IL (UTC-5 in May)", () => {
    // 14:30 UTC = 9:30 AM CT (CDT, UTC-5)
    expect(formatLocalTime("IL", FIXED)).toBe("9:30 AM");
  });

  it("returns MT time for CO (UTC-6 in May)", () => {
    // 14:30 UTC = 8:30 AM MT (MDT, UTC-6)
    expect(formatLocalTime("CO", FIXED)).toBe("8:30 AM");
  });

  it("returns PT time for CA (UTC-7 in May)", () => {
    // 14:30 UTC = 7:30 AM PT (PDT, UTC-7)
    expect(formatLocalTime("CA", FIXED)).toBe("7:30 AM");
  });

  it("returns AZ time (UTC-7, no DST) for AZ", () => {
    // 14:30 UTC = 7:30 AM MST (AZ, always UTC-7)
    expect(formatLocalTime("AZ", FIXED)).toBe("7:30 AM");
  });

  it("uses current time when no date passed", () => {
    // Should not throw and should return a time string
    const result = formatLocalTime("NY");
    expect(result).toMatch(/\d{1,2}:\d{2} [AP]M/);
  });
});

describe("getTimezoneDistribution", () => {
  it("returns empty array for all-null states", () => {
    expect(getTimezoneDistribution([null, null, null])).toEqual([]);
  });

  it("returns empty array for empty input", () => {
    expect(getTimezoneDistribution([])).toEqual([]);
  });

  it("counts and groups by tz label", () => {
    const result = getTimezoneDistribution(["NY", "MA", "CT", "IL", "TX"]);
    const et = result.find((r) => r.label === "ET");
    const ct = result.find((r) => r.label === "CT");
    expect(et?.count).toBe(3); // NY, MA, CT
    expect(ct?.count).toBe(2); // IL, TX
  });

  it("sorts by count descending", () => {
    const result = getTimezoneDistribution(["CA", "NY", "MA", "CT"]);
    expect(result[0].label).toBe("ET"); // 3 ET
    expect(result[1].label).toBe("PT"); // 1 PT
  });

  it("omits zero-count groups", () => {
    const result = getTimezoneDistribution(["NY"]);
    expect(result).toHaveLength(1);
    expect(result[0]).toEqual({ label: "ET", count: 1 });
  });

  it("ignores null states in distribution", () => {
    const result = getTimezoneDistribution([null, "NY", null, "CA"]);
    expect(result).toHaveLength(2);
    const total = result.reduce((s, r) => s + r.count, 0);
    expect(total).toBe(2);
  });

  it("AZ counts separately, not as MT", () => {
    const result = getTimezoneDistribution(["AZ", "CO"]);
    const az = result.find((r) => r.label === "AZ");
    const mt = result.find((r) => r.label === "MT");
    expect(az?.count).toBe(1);
    expect(mt?.count).toBe(1);
  });

  it("covers all 51 STATE_TO_TIMEZONE entries without throwing", () => {
    const allStates = Object.keys(STATE_TO_TIMEZONE);
    expect(() => getTimezoneDistribution(allStates)).not.toThrow();
  });
});
