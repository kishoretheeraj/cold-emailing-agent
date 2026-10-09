export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";
import {
  JOB_APPLICATION_STAGES,
  type JobApplicationStage,
  type JobApplicationApplyPreview,
} from "@/lib/types";
import { newYorkDate } from "@/lib/nyDay";

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
  if (!/^\d+$/.test(id)) {
    return Response.json({ error: "Invalid application id" }, { status: 400 });
  }

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

  const editingApplyPreview = "apply_preview" in updates;

  try {
    const supabase = getClient();

    // Merge review 2026-09-28, finding 1: once approved_at is set, apply_agent.py's submit()
    // can read the row (and start filling a real form) at any point during the GitHub Actions
    // workflow's several-minute dependency install -- a plain unconditional UPDATE here let an
    // edit made during that window change the answers sent under an approval that was granted
    // for a different set of answers. Reject any apply_preview write once the row is approved,
    // enforced atomically at the database level (not check-then-act, which would leave a race
    // window between reading approved_at and writing the update) via a conditional WHERE clause.
    // stage-only edits (e.g. manually marking an approved row withdrawn) are not gated by this --
    // only a payload that touches apply_preview is.
    // Automation-status lifecycle: a preview/submit worker overwrites stage/apply_preview when it
    // releases its lease, so a human edit of either must also be conditional on no live lease
    // (worker_lease_id IS NULL), enforced atomically in the WHERE clause. notes-only stays
    // unconditional -- no worker writes it.
    if (editingApplyPreview || "stage" in updates) {
      let query = supabase
        .from("job_applications")
        .update(updates)
        .eq("id", Number(id))
        .is("worker_lease_id", null);
      if (editingApplyPreview) query = query.is("approved_at", null);
      const { data, error } = await query.select().single();
      if (error) {
        if (error.code === "PGRST116") {
          // Zero rows matched -- disambiguate missing / leased / approved so each gets the
          // right status instead of a misleading 409 or 404.
          const { data: existing } = await supabase
            .from("job_applications")
            .select("id, approved_at, worker_lease_id")
            .eq("id", Number(id))
            .maybeSingle();
          if (!existing) {
            return Response.json({ error: "Application not found" }, { status: 404 });
          }
          if (existing.worker_lease_id) {
            return Response.json(
              { error: "A worker is currently processing this application -- try again shortly" },
              { status: 409 }
            );
          }
          if (editingApplyPreview && existing.approved_at) {
            return Response.json(
              { error: "Cannot edit apply_preview: this application has already been approved" },
              { status: 409 }
            );
          }
          return Response.json(
            { error: "Application changed while saving -- try again" },
            { status: 409 }
          );
        }
        throw error;
      }
      // Marking a row applied by hand records the date if nothing did: the cold-email agent's
      // applied-mode mail to linked people says when the application went in (warm paths).
      if (updates.stage === "applied" && data && !data.applied_date) {
        const { data: dated } = await supabase
          .from("job_applications")
          .update({ applied_date: newYorkDate() })
          .eq("id", Number(id))
          .is("applied_date", null)
          .select()
          .maybeSingle();
        if (dated) return Response.json({ application: dated });
      }
      return Response.json({ application: data });
    }

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
