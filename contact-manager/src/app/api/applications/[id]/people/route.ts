export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";
import {
  companyCap,
  contactFromApplication,
  isClosed,
  linkBlocker,
  MAX_PEOPLE_PER_APPLICATION,
  parsePerson,
  postingEmails,
  searchLinks,
} from "@/lib/warmPaths";
import {
  findByEmail,
  insertContact,
  loadApplication,
  loadCompanyContacts,
  loadLinked,
  loadPreferences,
} from "@/lib/warmPathsData";

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

const DAY_MS = 86_400_000;

function recentAtCompany(contacts: { relationship?: string | null; created_at?: string | null }[]): number {
  const since = Date.now() - 30 * DAY_MS;
  return contacts.filter((c) => c.relationship && c.created_at && Date.parse(c.created_at) >= since).length;
}

// The People panel's data: who is linked, who the user already knows at the company, search
// links, emails named in the posting, and how many more people this application can take.
export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) {
    return Response.json({ error: "Invalid application id" }, { status: 400 });
  }
  try {
    const db = getClient();
    const app = await loadApplication(db, Number(id));
    if (!app) return Response.json({ error: "Application not found" }, { status: 404 });
    const [linked, atCompany, prefs] = await Promise.all([
      loadLinked(db, Number(id)), loadCompanyContacts(db, app.company), loadPreferences(db),
    ]);
    const cap = companyCap(prefs);
    const known = atCompany
      .filter((c) => Number(c.job_application_id) !== Number(id))
      .map((c) => ({ ...c, blocker: linkBlocker(c) }));
    return Response.json({
      application: { id: app.id, company: app.company, role: app.role, stage: app.stage,
        automation_status: app.automation_status, referral_hold_until: app.referral_hold_until },
      linked,
      known,
      known_emails: atCompany.map((c) => ({ name: c.name, email: c.email })),
      search_links: searchLinks(app),
      posting_emails: postingEmails(app.posting_snapshot),
      remaining: Math.max(0, MAX_PEOPLE_PER_APPLICATION - linked.length),
      company_recent: recentAtCompany(atCompany),
      company_cap: cap,
      closed: isClosed(app),
    });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}

// Add a person the user picked. The contacts_link_guard trigger is the backstop for every rule
// checked here; these checks only make the refusal readable.
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) {
    return Response.json({ error: "Invalid application id" }, { status: 400 });
  }
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return Response.json({ error: "Invalid JSON" }, { status: 400 });
  }
  const parsed = parsePerson(body);
  if (!parsed.ok) return Response.json({ error: parsed.error }, { status: 400 });
  const person = parsed.person;

  try {
    const db = getClient();
    const app = await loadApplication(db, Number(id));
    if (!app) return Response.json({ error: "Application not found" }, { status: 404 });
    if (isClosed(app)) {
      return Response.json({ error: "This application is closed; nobody new is linked to it" }, { status: 409 });
    }
    const [linked, atCompany, prefs, existing] = await Promise.all([
      loadLinked(db, Number(id)), loadCompanyContacts(db, app.company), loadPreferences(db), findByEmail(db, person.email),
    ]);
    if (existing?.deleted_at) {
      const who = existing.name ?? "A contact";
      return Response.json({
        error: `${who} with this email was previously deleted. Restore them in the Supabase dashboard to re-add.`,
      }, { status: 409 });
    }
    if (existing) {
      return Response.json({
        error: `${existing.name ?? "Someone"} is already in your contacts`,
        existing: { id: existing.id, name: existing.name, blocker: linkBlocker(existing) },
      }, { status: 409 });
    }
    if (linked.length >= MAX_PEOPLE_PER_APPLICATION) {
      return Response.json({ error: `This application already has ${MAX_PEOPLE_PER_APPLICATION} people` }, { status: 409 });
    }
    const cap = companyCap(prefs);
    if (recentAtCompany(atCompany) >= cap) {
      return Response.json({ error: `You have added ${cap} people at ${app.company} in the last 30 days` }, { status: 409 });
    }
    const result = await insertContact(db, contactFromApplication(app, person));
    if (!result.ok) {
      return Response.json({ error: result.error }, { status: result.conflict ? 409 : 500 });
    }
    return Response.json({ contact: result.contact }, { status: 201 });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}
