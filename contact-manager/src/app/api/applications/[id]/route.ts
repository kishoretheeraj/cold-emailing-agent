export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";
import {
  JOB_APPLICATION_STAGES,
  type JobApplicationStage,
  type JobApplicationApplyPreview,
} from "@/lib/types";

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

function isValidApplyPreview(v: unknown): v is JobApplicationApplyPreview {
  if (typeof v !== "object" || v === null) return false;
  const p = v as Record<string, unknown>;
  return (
    typeof p.platform === "string" &&
    typeof p.field_values === "object" && p.field_values !== null &&
    typeof p.eligibility_answers === "object" && p.eligibility_answers !== null &&
    typeof p.screening_answers === "object" && p.screening_answers !== null
  );
}

export async function GET(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) {
    return Response.json({ error: "Invalid application id" }, { status: 400 });
  }
  try {
    const supabase = getClient();
    const { data, error } = await supabase
      .from("job_applications")
      .select("*")
      .eq("id", Number(id))
      .single();
    if (error) throw error;
    return Response.json({ application: data });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
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
    if (!JOB_APPLICATION_STAGES.includes(b.stage as JobApplicationStage)) {
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
  if ("apply_preview" in b) {
    if (!isValidApplyPreview(b.apply_preview)) {
      return Response.json(
        {
          error:
            "apply_preview must be an object with platform, field_values, eligibility_answers, screening_answers",
        },
        { status: 400 }
      );
    }
    updates.apply_preview = b.apply_preview;
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
