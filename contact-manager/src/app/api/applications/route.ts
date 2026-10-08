export const runtime = "nodejs";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { openTakeover } from "@/lib/takeover";
import { companyKey } from "@/lib/warmPaths";
import { knownPeopleByCompany, linkedCounts } from "@/lib/warmPathsData";
import type { JobApplication } from "@/lib/types";

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

const QUEUE_STATUSES = [
  "ready_for_review", "approved", "submitting", "needs_input", "needs_confirmation", "submitted",
];
// Submitted rows stay in the queue's "Submitted" list for two weeks; at fifty a day an unbounded
// list grows past what a phone can show and past the API's row cap.
const SUBMITTED_DAYS = 14;

// Each queue card shows who is linked and how many people the user knows at the company
// (warm paths). Best-effort: a failed count leaves the cards without it, never the queue empty.
async function withPeople(supabase: SupabaseClient, rows: JobApplication[]): Promise<JobApplication[]> {
  try {
    const [linked, known] = await Promise.all([
      linkedCounts(supabase, rows.map((r) => Number(r.id))), knownPeopleByCompany(supabase),
    ]);
    return rows.map((r) => ({
      ...r,
      people: { linked: linked.get(Number(r.id)) ?? 0, known: known.get(companyKey(r.company)) ?? 0 },
    }));
  } catch {
    return rows;
  }
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const stage = searchParams.get("stage");
  const source = searchParams.get("source");
  // ?takeover=open: only rows where a Beelink worker is waiting for a human (the banner polls it).
  const takeoverOpen = searchParams.get("takeover") === "open";
  // ?view=queue: what the approval queue shows (ready, in flight, needs you, and the last two weeks
  // of submitted rows); skipped (withdrawn) and rejected rows leave it.
  const queueView = searchParams.get("view") === "queue";
  try {
    const supabase = getClient();
    let query = supabase.from("job_applications").select("*");
    if (stage) query = query.eq("stage", stage);
    if (source) query = query.eq("source", source);
    if (takeoverOpen) query = query.not("takeover", "is", null);
    if (queueView) {
      const since = new Date(Date.now() - SUBMITTED_DAYS * 86_400_000).toISOString();
      query = query
        .not("stage", "in", "(withdrawn,rejected)")
        .in("automation_status", QUEUE_STATUSES)
        .or(`automation_status.neq.submitted,updated_at.gte.${since}`);
    }
    const { data, error } = await query.order("created_at", { ascending: false });
    if (error) throw error;
    const rows = (data ?? []) as JobApplication[];
    if (queueView) return Response.json({ applications: await withPeople(supabase, rows) });
    return Response.json({ applications: takeoverOpen ? rows.filter((r) => openTakeover(r)) : rows });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}

export async function POST(req: Request) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return Response.json({ error: "Invalid JSON" }, { status: 400 });
  }

  if (typeof body !== "object" || body === null) {
    return Response.json({ error: "company and role are required" }, { status: 400 });
  }
  const b = body as Record<string, unknown>;
  const company = typeof b.company === "string" ? b.company.trim() : "";
  const role = typeof b.role === "string" ? b.role.trim() : "";
  if (!company || !role) {
    return Response.json({ error: "company and role are required" }, { status: 400 });
  }

  try {
    const supabase = getClient();
    const { data, error } = await supabase
      .from("job_applications")
      .insert({
        company,
        role,
        job_url: typeof b.job_url === "string" ? b.job_url : null,
        source: typeof b.source === "string" ? b.source : "manual",
        contact_id: typeof b.contact_id === "string" ? Number(b.contact_id) : null,
        applied_date: typeof b.applied_date === "string" ? b.applied_date : null,
        notes: typeof b.notes === "string" ? b.notes : null,
        stage: "saved",
      })
      .select()
      .single();
    if (error) throw error;
    return Response.json({ application: data }, { status: 201 });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}
