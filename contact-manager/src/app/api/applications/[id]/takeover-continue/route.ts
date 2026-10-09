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

  // takeover_continue only marks an open request of the row's current lease as answered; the
  // waiting worker re-checks the page itself. It changes nothing else on the row.
  const supabase = getClient();
  const { data, error } = await supabase.rpc("takeover_continue", { p_id: Number(id) });
  if (error) {
    return Response.json({ error: error.message }, { status: 500 });
  }
  if (!data) {
    return Response.json({ error: "Nothing is waiting on this application any more" }, { status: 409 });
  }
  return Response.json({ ok: true });
}
