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
  const { error } = await supabase.rpc("reset_approval", { p_id: Number(id) });
  if (error) {
    return Response.json({ error: error.message }, { status: 409 });
  }
  return Response.json({ ok: true });
}
