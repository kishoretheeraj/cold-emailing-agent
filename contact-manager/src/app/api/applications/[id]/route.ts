export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";

const JOB_APPLICATION_STAGES = [
  "saved", "applied", "phone_screen", "onsite", "offer", "rejected", "withdrawn", "accepted",
] as const;

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

export async function PATCH(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return Response.json({ error: "Invalid JSON" }, { status: 400 });
  }

  if (typeof body !== "object" || body === null) {
    return Response.json({ error: "no valid fields to update" }, { status: 400 });
  }
  const b = body as Record<string, unknown>;
  const updates: Record<string, unknown> = {};

  if ("stage" in b) {
    if (!JOB_APPLICATION_STAGES.includes(b.stage as (typeof JOB_APPLICATION_STAGES)[number])) {
      return Response.json(
        { error: `stage must be one of: ${JOB_APPLICATION_STAGES.join(", ")}` },
        { status: 400 }
      );
    }
    updates.stage = b.stage;
  }
  if ("notes" in b && typeof b.notes === "string") {
    updates.notes = b.notes;
  }

  if (Object.keys(updates).length === 0) {
    return Response.json({ error: "no valid fields to update" }, { status: 400 });
  }
  updates.updated_at = new Date().toISOString();

  try {
    const supabase = getClient();
    const { data, error } = await supabase
      .from("job_applications")
      .update(updates)
      .eq("id", Number(id))
      .select()
      .single();
    if (error) throw error;
    return Response.json({ application: data });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}
