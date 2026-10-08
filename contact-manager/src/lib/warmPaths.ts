// Warm paths: people linked to a job application (docs/superpowers/specs/2026-10-08-warm-paths-design.md).
// Pure helpers shared by the people routes and the People panel. The database trigger
// contacts_link_guard (migration 20261010000000) enforces the link rules; these mirror them so
// the UI can explain a refusal before the write.

export const RELATIONSHIPS = ["hiring_manager", "leader", "recruiter", "alum", "team_member", "other"] as const;
export type Relationship = (typeof RELATIONSHIPS)[number];

export const RELATIONSHIP_LABELS: Record<Relationship, string> = {
  hiring_manager: "Hiring manager",
  leader: "Department lead",
  recruiter: "Recruiter",
  alum: "Alum",
  team_member: "Someone on the team",
  other: "Other",
};

// Mirrors config.WARM_MAX_PEOPLE_PER_APPLICATION and the trigger (a static test compares them).
export const MAX_PEOPLE_PER_APPLICATION = 3;
export const DEFAULT_COMPANY_CAP_30D = 5;
export const DEFAULT_HOLD_DAYS = 10;
export const MAX_HOLD_DAYS = 14;
export const JOB_DESCRIPTION_CHARS = 1500;

export const SCHOOLS = [
  { id: "dartmouth", label: "Dartmouth", search: "Dartmouth", dartmouth: true },
  { id: "thayer", label: "Dartmouth Thayer", search: "Thayer Dartmouth", dartmouth: true },
  { id: "tuck", label: "Dartmouth Tuck", search: "Tuck Dartmouth", dartmouth: true },
  { id: "anna", label: "Anna University", search: "Anna University", dartmouth: false },
] as const;
export type SchoolId = (typeof SCHOOLS)[number]["id"];

const APPLIED_RELATIONSHIPS: ReadonlySet<Relationship> = new Set(["hiring_manager", "leader", "recruiter"]);
const CLOSED_STAGES = new Set(["rejected", "withdrawn"]);

// ── Company identity (mirror of job_identity.company_key) ─────────────────────

const COMPANY_SUFFIXES = new Set([
  "inc", "incorporated", "llc", "llp", "ltd", "limited", "corp", "corporation",
  "co", "company", "plc", "gmbh", "ag", "sa", "nv", "bv", "holdings", "group",
]);

function words(text: string | null | undefined): string[] {
  return String(text ?? "").toLowerCase().replaceAll("&", " and ").split(/[^a-z0-9]+/).filter(Boolean);
}

export function companyKey(name: string | null | undefined): string {
  let w = words(name);
  if (w[0] === "the") w = w.slice(1);
  while (w.length > 1 && COMPANY_SUFFIXES.has(w[w.length - 1])) w = w.slice(0, -1);
  return w.filter((x) => x !== "and").join("");
}

// The most distinctive word of a company name, for a cheap ilike pre-filter before companyKey.
export function companySearchWord(name: string | null | undefined): string {
  const w = words(name).filter((x) => x !== "the" && x !== "and" && !COMPANY_SUFFIXES.has(x));
  return (w.sort((a, b) => b.length - a.length)[0] ?? "").slice(0, 40);
}

// ── Mode and fields ────────────────────────────────────────────────────────────

export function modeFor(relationship: Relationship): "applied" | "networking" {
  return APPLIED_RELATIONSHIPS.has(relationship) ? "applied" : "networking";
}

// A networking hook never names the role: the networking prompt forbids mentioning roles,
// openings or applying, and the hook would go stale once the application closes.
export function hookSuggestion(relationship: Relationship, opts: { company: string; school?: SchoolId | null; title?: string | null }): string {
  if (relationship === "alum") {
    const school = SCHOOLS.find((s) => s.id === opts.school);
    return school ? `Fellow ${school.label} alum` : "";
  }
  if (relationship === "team_member") {
    const title = (opts.title ?? "").trim();
    return title ? `Works as ${title} at ${opts.company}` : `Works at ${opts.company}`;
  }
  return "";
}

export type PersonInput = {
  name: string;
  email: string;
  relationship: Relationship;
  title: string;
  school: SchoolId | null;
  tier: 1 | 2 | 3;
  linkedin: string;
  connection_context: string;
};

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function str(v: unknown, max: number): string {
  return typeof v === "string" ? v.trim().slice(0, max) : "";
}

export function parsePerson(raw: unknown): { ok: true; person: PersonInput } | { ok: false; error: string } {
  if (typeof raw !== "object" || raw === null) return { ok: false, error: "name, email and relationship are required" };
  const b = raw as Record<string, unknown>;
  const name = str(b.name, 200);
  const email = str(b.email, 320).toLowerCase();
  const relationship = b.relationship as Relationship;
  if (!name || !email || !RELATIONSHIPS.includes(relationship)) {
    return { ok: false, error: "name, email and relationship are required" };
  }
  if (!EMAIL_RE.test(email)) return { ok: false, error: "That email address does not look valid" };
  const tier = b.tier === undefined || b.tier === null ? 2 : Number(b.tier);
  if (![1, 2, 3].includes(tier)) return { ok: false, error: "tier must be 1, 2 or 3" };
  const school = typeof b.school === "string" && SCHOOLS.some((s) => s.id === b.school) ? (b.school as SchoolId) : null;
  if (relationship === "alum" && !school) return { ok: false, error: "Pick the school you share" };
  const linkedin = str(b.linkedin, 300);
  if (linkedin && !/^https:\/\/([a-z]+\.)?linkedin\.com\//i.test(linkedin)) {
    return { ok: false, error: "The LinkedIn link must start with https://www.linkedin.com/" };
  }
  return {
    ok: true,
    person: {
      name, email, relationship, school, linkedin, tier: tier as 1 | 2 | 3,
      title: str(b.title, 200),
      connection_context: str(b.connection_context, 500),
    },
  };
}

export type ApplicationForPeople = {
  id: number | string;
  company: string;
  role: string;
  job_url?: string | null;
  stage?: string | null;
  posting_snapshot?: Record<string, unknown> | null;
};

export function isClosed(app: { stage?: string | null }): boolean {
  return CLOSED_STAGES.has(app.stage ?? "");
}

function postingDescription(app: ApplicationForPeople): string {
  const d = app.posting_snapshot?.description;
  return typeof d === "string" ? d : "";
}

// The contacts row the cold-email agent drafts from overnight. Applied-mode fields are persisted
// here so the Prompt Lab preview matches; applied_date is left to the agent, which fills it from
// the application only after the application has been submitted.
export function contactFromApplication(app: ApplicationForPeople, p: PersonInput): Record<string, unknown> {
  const mode = modeFor(p.relationship);
  const school = SCHOOLS.find((s) => s.id === p.school);
  const row: Record<string, unknown> = {
    name: p.name,
    email: p.email,
    company: app.company,
    role: p.title || null,
    tier: p.tier,
    mode,
    stage: "new",
    reply_status: "no_reply",
    relationship: p.relationship,
    job_application_id: Number(app.id),
    dartmouth: Boolean(school?.dartmouth),
    notes: p.linkedin ? `LinkedIn: ${p.linkedin}` : null,
  };
  if (mode === "applied") {
    row.job_title = app.role;
    row.job_description = postingDescription(app).slice(0, JOB_DESCRIPTION_CHARS) || null;
  } else {
    row.connection_context = p.connection_context || null;
  }
  return row;
}

// ── Finding people ────────────────────────────────────────────────────────────

export function roleFunction(role: string): string {
  const r = role.toLowerCase();
  if (r.includes("product")) return "product";
  if (/analyt|data/.test(r)) return "analytics";
  if (r.includes("program")) return "program management";
  if (r.includes("strategy") || r.includes("operations")) return "strategy";
  return role;
}

export function searchLinks(app: ApplicationForPeople): { label: string; url: string }[] {
  const fn = roleFunction(app.role);
  const queries: [string, string][] = [
    ["Dartmouth alumni", `${app.company} Dartmouth`],
    ["Anna University alumni", `${app.company} Anna University`],
    [`${fn} team`, `${app.company} ${fn}`],
  ];
  return queries.flatMap(([label, q]) => [
    { label: `LinkedIn: ${label}`, url: `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(q)}` },
    {
      label: `Google: ${label}`,
      url: `https://www.google.com/search?q=${encodeURIComponent(`site:linkedin.com/in "${app.company}" "${q.slice(app.company.length + 1)}"`)}`,
    },
  ]);
}

const INBOX_LOCALS = /^(careers?|jobs?|no-?reply|recruit(ing|ment)?|talent|hr|people|privacy|accommodations?|hiring|apply|info|support|hello)([._+-]|$)/i;

export function postingEmails(snapshot: Record<string, unknown> | null | undefined): { email: string; kind: "person" | "inbox" }[] {
  const text = Object.values(snapshot ?? {})
    .flatMap((v) => (Array.isArray(v) ? v : [v]))
    .filter((v): v is string => typeof v === "string")
    .join("\n");
  const found = new Set((text.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g) ?? []).map((e) => e.toLowerCase()));
  return [...found].sort().map((email) => ({ email, kind: INBOX_LOCALS.test(email.split("@")[0]) ? "inbox" : "person" }));
}

const PATTERNS: Record<string, (f: string, l: string) => string> = {
  "first.last": (f, l) => `${f}.${l}`,
  first_last: (f, l) => `${f}_${l}`,
  firstlast: (f, l) => `${f}${l}`,
  flast: (f, l) => `${f[0]}${l}`,
  firstl: (f, l) => `${f}${l[0]}`,
  first: (f) => f,
  "last.first": (f, l) => `${l}.${f}`,
};

function nameParts(name: string): [string, string] | null {
  const w = name.toLowerCase().normalize("NFKD").replace(/[^a-z\s-]/g, "").split(/[\s-]+/).filter(Boolean);
  return w.length >= 2 ? [w[0], w[w.length - 1]] : null;
}

// Offered only when at least two known addresses on one domain agree on a pattern; a wrong
// guess bounces and email_verify only checks the domain. Never auto-filled.
export function guessEmail(name: string, known: { name: string | null; email: string | null }[]):
  { email: string; pattern: string; basis: number } | null {
  const target = nameParts(name);
  if (!target) return null;
  const votes = new Map<string, number>();
  for (const k of known) {
    const parts = nameParts(k.name ?? "");
    const [local, domain] = (k.email ?? "").toLowerCase().split("@");
    if (!parts || !local || !domain) continue;
    for (const [pattern, build] of Object.entries(PATTERNS)) {
      if (build(...parts) === local) {
        const key = `${domain}|${pattern}`;
        votes.set(key, (votes.get(key) ?? 0) + 1);
      }
    }
  }
  const best = [...votes.entries()].filter(([, n]) => n >= 2).sort((a, b) => b[1] - a[1]);
  if (best.length === 0 || (best.length > 1 && best[1][1] === best[0][1])) return null;
  const [domain, pattern] = best[0][0].split("|");
  return { email: `${PATTERNS[pattern](...target)}@${domain}`, pattern, basis: best[0][1] };
}

// ── Link rules (mirror of contacts_link_guard) ────────────────────────────────

export type ContactBrief = {
  id: number | string;
  name: string | null;
  email: string | null;
  company: string | null;
  mode: string | null;
  stage: string | null;
  reply_status: string | null;
  job_application_id: number | null;
  relationship?: string | null;
  role?: string | null;
  classifier_status?: string | null;
  created_at?: string | null;
};

export function linkBlocker(c: ContactBrief): string | null {
  if (c.job_application_id) return "Already linked to another application";
  if (c.mode !== "applied" && c.mode !== "networking") return "Outreach contacts are not linked; change the mode first";
  if ((c.stage ?? "new") !== "new" || (c.reply_status ?? "no_reply") !== "no_reply") return "Already in touch";
  return null;
}

export function holdDays(prefsRaw: unknown): number {
  try {
    const parsed: unknown = typeof prefsRaw === "string" ? JSON.parse(prefsRaw) : null;
    const days = parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>).referral_hold_days : undefined;
    if (typeof days === "number" && Number.isInteger(days)) return Math.min(Math.max(days, 1), MAX_HOLD_DAYS);
  } catch {
    // fall through to the default
  }
  return DEFAULT_HOLD_DAYS;
}

export function companyCap(prefsRaw: unknown): number {
  try {
    const parsed: unknown = typeof prefsRaw === "string" ? JSON.parse(prefsRaw) : null;
    const cap = parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>).outreach_per_company_30d : undefined;
    if (typeof cap === "number" && Number.isInteger(cap) && cap >= 0) return cap;
  } catch {
    // fall through to the default
  }
  return DEFAULT_COMPANY_CAP_30D;
}
