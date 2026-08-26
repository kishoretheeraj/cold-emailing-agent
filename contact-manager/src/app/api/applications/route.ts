export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const stage = searchParams.get("stage");
  try {
    const supabase = getClient();
    let query = supabase.from("job_applications").select("*");
    if (stage) query = query.eq("stage", stage);
    const { data, error } = await query.order("created_at", { ascending: false });
    if (error) throw error;
    return Response.json({ applications: data ?? [] });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}

export async function POST(req: Request) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return Response.json({ error: "Invalid JSON" }, { status: 400 });
  }

  if (typeof body !== "object" || body === null) {
    return Response.json({ error: "company and role are required" }, { status: 400 });
  }
  const b = body as Record<string, unknown>;
  const company = typeof b.company === "string" ? b.company.trim() : "";
  const role = typeof b.role === "string" ? b.role.trim() : "";
  if (!company || !role) {
    return Response.json({ error: "company and role are required" }, { status: 400 });
  }

  try {
    const supabase = getClient();
    const { data, error } = await supabase
      .from("job_applications")
      .insert({
        company,
        role,
        job_url: typeof b.job_url === "string" ? b.job_url : null,
        source: typeof b.source === "string" ? b.source : "manual",
        contact_id: typeof b.contact_id === "string" ? Number(b.contact_id) : null,
        applied_date: typeof b.applied_date === "string" ? b.applied_date : null,
        notes: typeof b.notes === "string" ? b.notes : null,
        stage: "saved",
      })
      .select()
      .single();
    if (error) throw error;
    return Response.json({ application: data }, { status: 201 });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}
