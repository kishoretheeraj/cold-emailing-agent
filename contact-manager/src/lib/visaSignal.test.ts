import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { visaKey, visaSignal, type IntelRow, type StatsRow } from "./visaSignal";

describe("visaKey mirrors entity_resolution.normalize + canonicalize_alias_group", () => {
  const rows = JSON.parse(
    fs.readFileSync(path.join(__dirname, "../../../tests/fixtures/visa_names.json"), "utf-8"),
  ) as [string, string][];
  it.each(rows)("%j -> %j", (name, key) => {
    expect(visaKey(name)).toBe(key);
  });
  it("handles null", () => {
    expect(visaKey(null)).toBe("");
  });
});

const intel = (over: Partial<IntelRow>): IntelRow =>
  ({ normalized_name: "acme", sponsors_h1b: null, match_status: "unknown", h1b_recent_count: null, ...over });
const stats = (n: number | null): StatsRow => ({ normalized_name: "acme", lca_recent_2fy: n, latest_filing_fy: 2026 });

describe("visaSignal", () => {
  it.each([
    ["a human-reviewed sponsor", intel({ sponsors_h1b: true, match_status: "confirmed", h1b_recent_count: 12 }), undefined,
      { label: "Sponsors H-1B (12 recent filings)", tone: "good" }],
    ["an automatic match", intel({ sponsors_h1b: true, match_status: "auto" }), undefined, { label: "Sponsors H-1B", tone: "good" }],
    ["a match awaiting review", intel({ match_status: "needs_review" }), stats(40), { label: "H-1B: needs your review", tone: "review" }],
    ["a human-confirmed no", intel({ sponsors_h1b: false, match_status: "confirmed" }), stats(40),
      { label: "No H-1B record (you confirmed)", tone: "none" }],
    ["a rejected match, even with an exact corpus row", intel({ match_status: "rejected" }), stats(40),
      { label: "No H-1B data", tone: "none" }],
    ["only an exact corpus row", undefined, stats(7), { label: "H-1B filings: 7 in 2 years", tone: "good" }],
    ["an unknown intel row falls through to the corpus", intel({}), stats(3), { label: "H-1B filings: 3 in 2 years", tone: "good" }],
    ["a corpus row without recent filings", undefined, stats(0), { label: "No H-1B data", tone: "none" }],
    ["nothing", undefined, undefined, { label: "No H-1B data", tone: "none" }],
  ] as const)("%s", (_name, i, s, expected) => {
    expect(visaSignal(i, s)).toEqual(expected);
  });

  it("never says a company does not sponsor", () => {
    const labels = [visaSignal(undefined, undefined), visaSignal(intel({ match_status: "rejected" }), undefined),
      visaSignal(intel({ sponsors_h1b: false, match_status: "confirmed" }), undefined)].map((v) => v.label);
    for (const l of labels) expect(l).not.toMatch(/does not|doesn't|no sponsorship|won't/i);
  });
});
