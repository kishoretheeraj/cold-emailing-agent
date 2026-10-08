export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";
import { dailyCap } from "@/lib/dailyCap";
import { startOfNewYorkDay } from "@/lib/nyDay";

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

// Submissions attempted today (America/New_York) against the daily cap, for the queue's meter.
export async function GET() {
  try {
    const supabase = getClient();
    const since = startOfNewYorkDay().toISOString();
    const [count, prefs] = await Promise.all([
      supabase.from("job_applications").select("id", { count: "exact", head: true }).gte("submit_attempted_at", since),
      supabase.from("prompts").select("value").eq("key", "job_search_preferences").maybeSingle(),
    ]);
    if (count.error) throw count.error;
    return Response.json({ submitted: count.count ?? 0, cap: dailyCap(prefs.data?.value) });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}
