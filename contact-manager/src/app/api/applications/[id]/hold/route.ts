export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";
import { holdDays } from "@/lib/warmPaths";
import { loadPreferences } from "@/lib/warmPathsData";

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

function validId(id: string) {
  return /^\d+$/.test(id);
}

// "Ask for a referral first": hold_for_referral clamps the days to 1..14 and only acts on an
// unapproved preview (it raises otherwise, which becomes a 409). The hold never approves or
// blocks anything; Submit stays available on the card.
export async function POST(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!validId(id)) return Response.json({ error: "Invalid application id" }, { status: 400 });
  try {
    const db = getClient();
    const days = holdDays(await loadPreferences(db));
    const { data, error } = await db.rpc("hold_for_referral", { p_id: Number(id), p_days: days });
    if (error) return Response.json({ error: error.message }, { status: 409 });
    return Response.json({ referral_hold_until: data, days });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}

export async function DELETE(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!validId(id)) return Response.json({ error: "Invalid application id" }, { status: 400 });
  try {
    const { error } = await getClient().rpc("release_referral_hold", { p_id: Number(id) });
    if (error) return Response.json({ error: error.message }, { status: 409 });
    return Response.json({ ok: true });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}
