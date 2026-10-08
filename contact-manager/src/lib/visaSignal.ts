// H-1B signal for a job application's company on the queue card. Decision support only, same
// governance as the visa gate (root CLAUDE.md): exact normalized-name matches only (no fuzzy tier,
// the same choice ingest_form_d made), a human /visa-review decision always wins, and no data reads
// as "No H-1B data", never as "does not sponsor".

// Mirror of entity_resolution.normalize() + canonicalize_alias_group(); both sides test
// tests/fixtures/visa_names.json, so a change to one without the other fails a test.
const LEGAL_SUFFIXES = new Set([
  "incorporated", "inc", "llc", "l.l.c", "corporation", "corp", "company", "co", "limited", "ltd",
  "llp", "l.l.p", "lp", "l.p", "plc", "pc", "pa", "na",
]);
const NOISE_WORDS = new Set(["the", "and"]);
const ALIAS_GROUPS: Record<string, string[]> = {
  amazon: ["amazon com services", "amazon web services", "amazon data services"],
};

export function visaKey(raw: string | null | undefined): string {
  if (!raw) return "";
  let name = raw.trim().toLowerCase().replaceAll("&", " and ");
  name = name.replace(/[.,'’]/g, " ").replace(/\s+/g, " ").trim();
  const tokens = name.split(" ");
  while (tokens.length && LEGAL_SUFFIXES.has(tokens[tokens.length - 1])) tokens.pop();
  while (tokens.length && NOISE_WORDS.has(tokens[0])) tokens.shift();
  while (tokens.length && NOISE_WORDS.has(tokens[tokens.length - 1])) tokens.pop();
  const normalized = tokens.join(" ");
  for (const [canonical, members] of Object.entries(ALIAS_GROUPS)) {
    if (normalized === canonical || members.includes(normalized)) return canonical;
  }
  return normalized;
}

export type IntelRow = { normalized_name: string; sponsors_h1b: boolean | null; match_status: string; h1b_recent_count: number | null };
export type StatsRow = { normalized_name: string; lca_recent_2fy: number | null; latest_filing_fy: number | null };
export type VisaSignal = { label: string; tone: "good" | "review" | "none" };

export function visaSignal(intel: IntelRow | undefined, stats: StatsRow | undefined): VisaSignal {
  if (intel) {
    if (intel.match_status === "rejected") return { label: "No H-1B data", tone: "none" };
    if (intel.match_status === "needs_review") return { label: "H-1B: needs your review", tone: "review" };
    if (intel.match_status === "confirmed" && intel.sponsors_h1b === false) {
      return { label: "No H-1B record (you confirmed)", tone: "none" };
    }
    if (intel.sponsors_h1b === true) {
      const n = intel.h1b_recent_count;
      return { label: n ? `Sponsors H-1B (${n} recent filings)` : "Sponsors H-1B", tone: "good" };
    }
  }
  const recent = stats?.lca_recent_2fy ?? 0;
  if (stats && recent > 0) return { label: `H-1B filings: ${recent} in 2 years`, tone: "good" };
  return { label: "No H-1B data", tone: "none" };
}
