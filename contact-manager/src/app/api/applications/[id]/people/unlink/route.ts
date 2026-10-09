export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";
import { unlinkContact } from "@/lib/warmPathsData";

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

// Unlinking only clears the link; the contact keeps its mode, stage and thread.
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
    const done = await unlinkContact(getClient(), Number(contactId), Number(id));
    if (!done) return Response.json({ error: "That contact is not linked to this application" }, { status: 409 });
    return Response.json({ ok: true });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}
