export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) {
    return Response.json({ error: "Invalid application id" }, { status: 400 });
  }
  const supabase = getClient();
  // reset_approval (migration 20260928000000) clears approved_at and apply_blocked_reason in
  // one guarded UPDATE -- see merge review 2026-09-28 finding 6. An earlier version of this
  // route did that second clear as a separate anon-permitted UPDATE here; if it failed, the
  // row stayed visibly blocked with no working retry (a second tap failed at the RPC's own
  // approved_at IS NOT NULL guard, which no longer held after the first call's partial
  // success). Doing both in the RPC removes the partial-failure window entirely.
  const { error } = await supabase.rpc("reset_approval", { p_id: Number(id) });
  if (error) {
    return Response.json({ error: error.message }, { status: 409 });
  }

  return Response.json({ ok: true });
}
