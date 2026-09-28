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

  // C1 follow-up: reset_approval (migration 20260925000000) only clears approved_at -- it
  // never touches apply_blocked_reason. Without also clearing it here, ApplicationsPage.tsx's
  // Try again fallback (which now reads apply_blocked_reason straight from the row, not only
  // client-side polling state) would keep rendering the blocked state forever after a reset,
  // permanently hiding Approve & Submit again -- the exact dead-end C1 exists to close, just
  // moved one step later. Best-effort: a failure here must never turn a successful
  // approved_at reset into a client-visible error, since the row is still recoverable (a
  // second Try again click would retry this clear).
  const { error: clearError } = await supabase
    .from("job_applications")
    .update({ apply_blocked_reason: null, updated_at: new Date().toISOString() })
    .eq("id", Number(id));
  if (clearError) {
    console.error(`reset_approval: failed to clear apply_blocked_reason for ${id}: ${clearError.message}`);
  }

  return Response.json({ ok: true });
}
