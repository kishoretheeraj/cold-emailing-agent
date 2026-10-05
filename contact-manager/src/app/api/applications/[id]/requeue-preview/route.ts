export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

export async function POST(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) {
    return Response.json({ error: "Invalid application id" }, { status: 400 });
  }

  // requeue_preview only acts on a needs_input row with no lease; it clears approval and the
  // stored form signature so the next preview pass re-reads the (changed) form from scratch.
  // supabase-js RPC calls don't throw on a Postgres exception; the error is on the result.
  const supabase = getClient();
  const { error } = await supabase.rpc("requeue_preview", { p_id: Number(id) });
  if (error) {
    return Response.json({ error: error.message }, { status: 409 });
  }

  return Response.json({ ok: true });
}
