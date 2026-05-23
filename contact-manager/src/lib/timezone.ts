// ── US States ──────────────────────────────────────────────────────────────────

export const US_STATES: { code: string; name: string }[] = [
  { code: "AL", name: "Alabama" },
  { code: "AK", name: "Alaska" },
  { code: "AZ", name: "Arizona" },
  { code: "AR", name: "Arkansas" },
  { code: "CA", name: "California" },
  { code: "CO", name: "Colorado" },
  { code: "CT", name: "Connecticut" },
  { code: "DE", name: "Delaware" },
  { code: "DC", name: "District of Columbia" },
  { code: "FL", name: "Florida" },
  { code: "GA", name: "Georgia" },
  { code: "HI", name: "Hawaii" },
  { code: "ID", name: "Idaho" },
  { code: "IL", name: "Illinois" },
  { code: "IN", name: "Indiana" },
  { code: "IA", name: "Iowa" },
  { code: "KS", name: "Kansas" },
  { code: "KY", name: "Kentucky" },
  { code: "LA", name: "Louisiana" },
  { code: "ME", name: "Maine" },
  { code: "MD", name: "Maryland" },
  { code: "MA", name: "Massachusetts" },
  { code: "MI", name: "Michigan" },
  { code: "MN", name: "Minnesota" },
  { code: "MS", name: "Mississippi" },
  { code: "MO", name: "Missouri" },
  { code: "MT", name: "Montana" },
  { code: "NE", name: "Nebraska" },
  { code: "NV", name: "Nevada" },
  { code: "NH", name: "New Hampshire" },
  { code: "NJ", name: "New Jersey" },
  { code: "NM", name: "New Mexico" },
  { code: "NY", name: "New York" },
  { code: "NC", name: "North Carolina" },
  { code: "ND", name: "North Dakota" },
  { code: "OH", name: "Ohio" },
  { code: "OK", name: "Oklahoma" },
  { code: "OR", name: "Oregon" },
  { code: "PA", name: "Pennsylvania" },
  { code: "RI", name: "Rhode Island" },
  { code: "SC", name: "South Carolina" },
  { code: "SD", name: "South Dakota" },
  { code: "TN", name: "Tennessee" },
  { code: "TX", name: "Texas" },
  { code: "UT", name: "Utah" },
  { code: "VT", name: "Vermont" },
  { code: "VA", name: "Virginia" },
  { code: "WA", name: "Washington" },
  { code: "WV", name: "West Virginia" },
  { code: "WI", name: "Wisconsin" },
  { code: "WY", name: "Wyoming" },
];

// ── State → IANA zone ──────────────────────────────────────────────────────────
// Split-zone states map to the majority zone (±1hr error in minority counties is acceptable).

export const STATE_TO_TIMEZONE: Record<string, string> = {
  AL: "America/Chicago",
  AK: "America/Anchorage",
  AZ: "America/Phoenix", // no DST
  AR: "America/Chicago",
  CA: "America/Los_Angeles",
  CO: "America/Denver",
  CT: "America/New_York",
  DC: "America/New_York",
  DE: "America/New_York",
  FL: "America/New_York",     // panhandle (CT) is minority
  GA: "America/New_York",
  HI: "America/Honolulu",
  ID: "America/Denver",       // Boise majority; Malheur County (PT) minority
  IL: "America/Chicago",
  IN: "America/Indianapolis", // Eastern; most of state
  IA: "America/Chicago",
  KS: "America/Chicago",      // small western (MT) minority
  KY: "America/New_York",     // Louisville majority; far west (CT) minority
  LA: "America/Chicago",
  ME: "America/New_York",
  MD: "America/New_York",
  MA: "America/New_York",
  MI: "America/Detroit",      // Eastern; 4 UP counties (CT) minority
  MN: "America/Chicago",
  MS: "America/Chicago",
  MO: "America/Chicago",
  MT: "America/Denver",
  NE: "America/Chicago",      // panhandle (MT) minority
  NV: "America/Los_Angeles",
  NH: "America/New_York",
  NJ: "America/New_York",
  NM: "America/Denver",
  NY: "America/New_York",
  NC: "America/New_York",
  ND: "America/Chicago",      // small western (MT) minority
  OH: "America/New_York",
  OK: "America/Chicago",
  OR: "America/Los_Angeles",  // Malheur County (MT) minority
  PA: "America/New_York",
  RI: "America/New_York",
  SC: "America/New_York",
  SD: "America/Chicago",      // small western (MT) minority
  TN: "America/Chicago",      // Knoxville/eastern TN (ET) minority
  TX: "America/Chicago",      // El Paso (MT) minority
  UT: "America/Denver",
  VT: "America/New_York",
  VA: "America/New_York",
  WA: "America/Los_Angeles",
  WV: "America/New_York",
  WI: "America/Chicago",
  WY: "America/Denver",
};

// ── IANA zone → short label ────────────────────────────────────────────────────

export const ZONE_TO_LABEL: Record<string, string> = {
  "America/New_York": "ET",
  "America/Chicago": "CT",
  "America/Denver": "MT",
  "America/Los_Angeles": "PT",
  "America/Phoenix": "AZ",
  "America/Honolulu": "HT",
  "America/Anchorage": "AK",
  "America/Indianapolis": "ET",
  "America/Detroit": "ET",
};

// ── Exported helpers ───────────────────────────────────────────────────────────

export function getTimezoneForState(code: string | null): string | null {
  if (!code) return null;
  return STATE_TO_TIMEZONE[code.toUpperCase()] ?? null;
}

export function ianaToTimezoneLabel(ianaZone: string): string | null {
  return ZONE_TO_LABEL[ianaZone] ?? null;
}

export function getTimezoneLabel(code: string | null): string | null {
  const zone = getTimezoneForState(code);
  if (!zone) return null;
  return ZONE_TO_LABEL[zone] ?? null;
}

export function formatLocalTime(state: string | null, now: Date = new Date()): string | null {
  const zone = getTimezoneForState(state);
  if (!zone) return null;
  try {
    return new Intl.DateTimeFormat("en-US", {
      hour: "numeric",
      minute: "2-digit",
      timeZone: zone,
    }).format(now);
  } catch {
    return null;
  }
}

export function getTimezoneDistribution(
  states: (string | null)[]
): { label: string; count: number }[] {
  const counts: Record<string, number> = {};
  for (const s of states) {
    const label = getTimezoneLabel(s);
    if (!label) continue;
    counts[label] = (counts[label] ?? 0) + 1;
  }
  return Object.entries(counts)
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count);
}
