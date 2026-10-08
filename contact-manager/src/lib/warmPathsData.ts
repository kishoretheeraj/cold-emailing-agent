// Supabase reads and writes for warm paths. Kept apart from the routes so route tests can mock
// one module, and so warmPathsData.stress.test.ts can run these exact queries against the local
// Postgres + PostgREST stack (scripts/stress/up.sh).
import type { SupabaseClient } from "@supabase/supabase-js";
import { companyKey, companySearchWord, type ApplicationForPeople, type ContactBrief } from "@/lib/warmPaths";
import type { IntelRow, StatsRow } from "@/lib/visaSignal";

export const CONTACT_COLUMNS =
  "id,name,email,company,mode,stage,reply_status,job_application_id,relationship,role,classifier_status,created_at";
const APPLICATION_COLUMNS = "id,company,role,job_url,stage,automation_status,posting_snapshot,referral_hold_until";

export type ApplicationRow = ApplicationForPeople & {
  automation_status?: string | null;
  referral_hold_until?: string | null;
};

export async function loadApplication(db: SupabaseClient, id: number): Promise<ApplicationRow | null> {
  const { data, error } = await db.from("job_applications").select(APPLICATION_COLUMNS).eq("id", id).maybeSingle();
  if (error) throw error;
  return (data as ApplicationRow | null) ?? null;
}

export async function loadLinked(db: SupabaseClient, id: number): Promise<ContactBrief[]> {
  const { data, error } = await db.from("contacts").select(CONTACT_COLUMNS)
    .eq("job_application_id", id).is("deleted_at", null).order("id");
  if (error) throw error;
  return (data ?? []) as ContactBrief[];
}

function escapeLike(word: string): string {
  return word.replace(/[\\%_,()]/g, "");
}

// Live contacts at the same company: an ilike pre-filter on the most distinctive word, then the
// exact companyKey comparison (the same folding job_identity.company_key applies).
export async function loadCompanyContacts(db: SupabaseClient, company: string): Promise<ContactBrief[]> {
  const key = companyKey(company);
  const word = escapeLike(companySearchWord(company));
  if (!key || !word) return [];
  // Paged: a common word ("bank", "health") can match more rows than PostgREST returns at once.
  const matches: ContactBrief[] = [];
  for (let start = 0; ; start += 1000) {
    const { data, error } = await db.from("contacts").select(CONTACT_COLUMNS)
      .ilike("company", `%${word}%`).is("deleted_at", null).order("id").range(start, start + 999);
    if (error) throw error;
    const rows = (data ?? []) as ContactBrief[];
    matches.push(...rows.filter((c) => companyKey(c.company) === key));
    if (rows.length < 1000) return matches;
  }
}

export type EmailMatch = ContactBrief & { deleted_at: string | null };

export async function findByEmail(db: SupabaseClient, email: string): Promise<EmailMatch | null> {
  const { data, error } = await db.from("contacts").select(`${CONTACT_COLUMNS},deleted_at`)
    .ilike("email", email.replace(/[\\%_]/g, "\\$&")).limit(1);
  if (error) throw error;
  return ((data ?? [])[0] as EmailMatch | undefined) ?? null;
}

export async function loadPreferences(db: SupabaseClient): Promise<unknown> {
  const { data } = await db.from("prompts").select("value").eq("key", "job_search_preferences").maybeSingle();
  return data?.value ?? null;
}

export type WriteResult = { ok: true; contact: ContactBrief } | { ok: false; conflict: boolean; error: string };

function writeError(error: { code?: string; message?: string }): WriteResult {
  const message = error.message ?? "write failed";
  // The trigger's refusals start with "warm_paths:"; a unique email is 23505. Both are the
  // person's or the application's state, not a server fault.
  const conflict = error.code === "23505" || message.includes("warm_paths:");
  return { ok: false, conflict, error: message.replace(/^warm_paths:\s*/, "") };
}

export async function insertContact(db: SupabaseClient, row: Record<string, unknown>): Promise<WriteResult> {
  const { data, error } = await db.from("contacts").insert(row).select(CONTACT_COLUMNS).single();
  if (error) return writeError(error);
  return { ok: true, contact: data as ContactBrief };
}

export async function linkContact(db: SupabaseClient, contactId: number, applicationId: number): Promise<WriteResult | null> {
  const { data, error } = await db.from("contacts").update({ job_application_id: applicationId })
    .eq("id", contactId).is("job_application_id", null).is("deleted_at", null)
    .select(CONTACT_COLUMNS).maybeSingle();
  if (error) return writeError(error);
  return data ? { ok: true, contact: data as ContactBrief } : null;
}

export async function unlinkContact(db: SupabaseClient, contactId: number, applicationId: number): Promise<boolean> {
  const { data, error } = await db.from("contacts").update({ job_application_id: null })
    .eq("id", contactId).eq("job_application_id", applicationId).select("id").maybeSingle();
  if (error) throw error;
  return Boolean(data);
}

// How many live contacts the user knows at each company, for the queue cards. One paged read of
// the company column; contacts can pass PostgREST's 1000-row cap.
export async function knownPeopleByCompany(db: SupabaseClient): Promise<Map<string, number>> {
  const counts = new Map<string, number>();
  for (let start = 0; ; start += 1000) {
    const { data, error } = await db.from("contacts").select("id,company")
      .is("deleted_at", null).not("company", "is", null).order("id").range(start, start + 999);
    if (error) throw error;
    const rows = (data ?? []) as { company: string | null }[];
    for (const r of rows) {
      const key = companyKey(r.company);
      if (key) counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    if (rows.length < 1000) return counts;
  }
}

export async function linkedCounts(db: SupabaseClient, applicationIds: number[]): Promise<Map<number, number>> {
  const counts = new Map<number, number>();
  for (let i = 0; i < applicationIds.length; i += 200) {
    const { data, error } = await db.from("contacts").select("job_application_id")
      .in("job_application_id", applicationIds.slice(i, i + 200)).is("deleted_at", null);
    if (error) throw error;
    for (const r of (data ?? []) as { job_application_id: number }[]) {
      counts.set(r.job_application_id, (counts.get(r.job_application_id) ?? 0) + 1);
    }
  }
  return counts;
}

// Applications per company in the last `days` that got documents built or went toward a
// submission: the same rows db.count_company_applications counts for the per-company cap.
export async function companyApplicationIds(db: SupabaseClient, keys: string[], days = 30): Promise<Map<string, number[]>> {
  const ids = new Map<string, number[]>();
  const unique = [...new Set(keys.filter(Boolean))];
  const cutoff = new Date(Date.now() - days * 86_400_000).toISOString();
  for (let i = 0; i < unique.length; i += 200) {
    const { data, error } = await db.from("job_applications").select("id,company_key")
      .in("company_key", unique.slice(i, i + 200)).gte("created_at", cutoff)
      .or("resume_file_ref.not.is.null,automation_status.in.(approved,submitting,submitted,needs_confirmation)")
      .limit(5000);
    if (error) throw error;
    for (const r of (data ?? []) as { id: number; company_key: string }[]) {
      ids.set(r.company_key, [...(ids.get(r.company_key) ?? []), Number(r.id)]);
    }
  }
  return ids;
}

export function perCompanyCap(prefsRaw: unknown): number {
  try {
    const parsed: unknown = typeof prefsRaw === "string" ? JSON.parse(prefsRaw) : null;
    const cap = parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>).per_company_cap_30d : undefined;
    if (typeof cap === "number" && Number.isInteger(cap) && cap >= 0) return cap;
  } catch {
    // fall through to the default
  }
  return 3;
}

// The H-1B rows for these exact normalized names (visaKey): human-reviewed company_intel rows and
// the DOL/USCIS employer_h1b_stats corpus. Exact matches only; a name with no row reads as no data.
export async function visaRows(db: SupabaseClient, keys: string[]):
  Promise<{ intel: Map<string, IntelRow>; stats: Map<string, StatsRow> }> {
  const unique = [...new Set(keys.filter(Boolean))];
  const intel = new Map<string, IntelRow>();
  const stats = new Map<string, StatsRow>();
  for (let i = 0; i < unique.length; i += 200) {
    const chunk = unique.slice(i, i + 200);
    const [a, b] = await Promise.all([
      db.from("company_intel").select("normalized_name,sponsors_h1b,match_status,h1b_recent_count").in("normalized_name", chunk),
      db.from("employer_h1b_stats").select("normalized_name,lca_recent_2fy,latest_filing_fy").in("normalized_name", chunk),
    ]);
    if (a.error) throw a.error;
    if (b.error) throw b.error;
    for (const r of (a.data ?? []) as IntelRow[]) intel.set(r.normalized_name, r);
    for (const r of (b.data ?? []) as StatsRow[]) stats.set(r.normalized_name, r);
  }
  return { intel, stats };
}
