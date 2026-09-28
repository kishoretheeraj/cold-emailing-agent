export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";
import type { SystemHealthRow } from "@/lib/types";

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

export async function GET() {
  try {
    const supabase = getClient();
    // agent_runs_latest_by_source is a DISTINCT ON (source) view (see migration
    // 20260925000001) -- it already returns exactly one row per source, the most recent by
    // ran_at, so no client-side windowing or reduction is needed here.
    const { data, error } = await supabase
      .from("agent_runs_latest_by_source")
      .select("source, status, ran_at, failure_reason")
      .order("source", { ascending: true });
    if (error) throw error;
    return Response.json({ health: (data ?? []) as SystemHealthRow[] });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}
