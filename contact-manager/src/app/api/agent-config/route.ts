export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";

type PauseScope = "none" | "agent" | "all";
const VALID_SCOPES: PauseScope[] = ["none", "agent", "all"];

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

export async function GET() {
  try {
    const supabase = getClient();
    const { data, error } = await supabase
      .from("system_config")
      .select("value")
      .eq("key", "pause_scope")
      .single();

    if (error) throw error;
    const scope = (data?.value ?? "none") as PauseScope;
    return Response.json({ scope });
  } catch {
    // Table may not exist yet on fresh deploys — default to not paused.
    return Response.json({ scope: "none" });
  }
}

export async function POST(req: Request) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return Response.json({ error: "Invalid JSON" }, { status: 400 });
  }

  if (
    typeof body !== "object" ||
    body === null ||
    !("scope" in body) ||
    !VALID_SCOPES.includes((body as { scope: unknown }).scope as PauseScope)
  ) {
    return Response.json(
      { error: `scope must be one of: ${VALID_SCOPES.join(", ")}` },
      { status: 400 }
    );
  }

  const scope = (body as { scope: PauseScope }).scope;

  try {
    const supabase = getClient();
    const { error } = await supabase
      .from("system_config")
      .upsert({ key: "pause_scope", value: scope, updated_at: new Date().toISOString() })
      .eq("key", "pause_scope");

    if (error) throw error;
    return Response.json({ ok: true });
  } catch (err) {
    return Response.json(
      { error: String(err) },
      { status: 500 }
    );
  }
}
