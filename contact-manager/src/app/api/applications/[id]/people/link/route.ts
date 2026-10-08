export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";
import { linkContact } from "@/lib/warmPathsData";

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

// Link a contact the user already has. Only an unlinked, live contact matches the update; the
// contacts_link_guard trigger refuses mid-thread, outreach-mode, closed-application and
// fourth-person links, and its message comes back as a 409.
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
  const contactId = (body as Record<string, unknown> | null)?.contact_id;
  if (!(typeof contactId === "number" || typeof contactId === "string") || !/^\d+$/.test(String(contactId))) {
    return Response.json({ error: "contact_id is required" }, { status: 400 });
  }
  try {
    const result = await linkContact(getClient(), Number(contactId), Number(id));
    if (result === null) {
      return Response.json({ error: "That contact is gone or already linked" }, { status: 409 });
    }
    if (!result.ok) {
      return Response.json({ error: result.error }, { status: result.conflict ? 409 : 500 });
    }
    return Response.json({ contact: result.contact });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}
