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

  const body = await req.json().catch(() => null);
  if (!body || typeof body.submitted !== "boolean") {
    return Response.json({ error: "submitted (boolean) is required" }, { status: 400 });
  }

  // resolve_confirmation only acts on a needs_confirmation row -- the human's answer to "did
  // the Submit click land?" is the only thing that may move it out, never an automatic retry.
  // supabase-js RPC calls don't throw on a Postgres exception; the error is on the result.
  const supabase = getClient();
  const { error } = await supabase.rpc("resolve_confirmation", {
    p_id: Number(id),
    p_submitted: body.submitted,
  });
  if (error) {
    return Response.json({ error: error.message }, { status: 409 });
  }

  return Response.json({ ok: true });
}
