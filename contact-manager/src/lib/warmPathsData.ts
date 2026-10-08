// Supabase reads and writes for warm paths. Kept apart from the routes so route tests can mock
// one module, and so warmPathsData.stress.test.ts can run these exact queries against the local
// Postgres + PostgREST stack (scripts/stress/up.sh).
import type { SupabaseClient } from "@supabase/supabase-js";
import { companyKey, companySearchWord, type ApplicationForPeople, type ContactBrief } from "@/lib/warmPaths";

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
