export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";
import { getGmailClient } from "@/lib/gmail-server";
import { CADENCE, STAGE_TRANSITIONS } from "@/lib/cadence";
import type { gmail_v1 } from "googleapis";

// All *_sent stage values — used for idempotency check.
const SENT_STAGES = new Set(
  Object.values(STAGE_TRANSITIONS).map((t) => t.next)
);

function todayUTC(): string {
  return new Date().toISOString().slice(0, 10);
}

function computeFollowupDate(cadenceKey: keyof typeof CADENCE | null): string | null {
  if (!cadenceKey) return null;
  const days = CADENCE[cadenceKey];
  const d = new Date();
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function getSupabase() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL!;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;
  return createClient(url, key);
}

export async function POST(req: Request) {
  const startedAt = new Date().toISOString();
  let contactId: string | undefined;

  try {
    const body = (await req.json()) as { contact_id?: unknown };
    const raw = body.contact_id;
    const coerced =
      typeof raw === "string" ? raw :
      typeof raw === "number" ? String(raw) : null;
    if (!coerced) {
      return Response.json({ error: "contact_id is required" }, { status: 400 });
    }
    contactId = coerced;
  } catch {
    return Response.json({ error: "contact_id is required" }, { status: 400 });
  }

  const supabase = getSupabase();

  // Step 2: fetch contact
  const { data: contact, error: contactErr } = await supabase
    .from("contacts")
    .select("*")
    .eq("id", contactId)
    .is("deleted_at", null)
    .single();

  if (contactErr || !contact) {
    return Response.json({ error: "contact not found" }, { status: 404 });
  }

  const stage: string = contact.stage ?? "";

  // Step 3: check stage
  if (SENT_STAGES.has(stage)) {
    return Response.json({ ok: true, already_sent: true }, { status: 200 });
  }
  if (!(stage in STAGE_TRANSITIONS)) {
    return Response.json(
      { error: "contact not in a drafted state" },
      { status: 409 }
    );
  }

  // Step 4: fetch latest unsent draft_history row
  const { data: draftRow, error: draftErr } = await supabase
    .from("draft_history")
    .select("*")
    .eq("contact_id", contactId)
    .is("sent_body", null)
    .order("drafted_at", { ascending: false })
    .limit(1)
    .single();

  if (draftErr || !draftRow || !draftRow.gmail_draft_id) {
    return Response.json(
      {
        error:
          "no Gmail draft ID stored; contact needs to be re-drafted by the agent",
      },
      { status: 410 }
    );
  }

  const gmailDraftId: string = draftRow.gmail_draft_id;

  // Step 5–6: call Gmail API
  let gmail;
  try {
    gmail = getGmailClient();
  } catch {
    return Response.json({ error: "Gmail auth not configured" }, { status: 401 });
  }

  let sentMsg: { id?: string | null; threadId?: string | null } | null = null;
  try {
    const sendParams: gmail_v1.Params$Resource$Users$Drafts$Send = {
      userId: "me",
      requestBody: { id: gmailDraftId },
    };
    const sendRes = await gmail.users.drafts.send(sendParams);
    sentMsg = sendRes.data;
  } catch (err: unknown) {
    const status = (err as { code?: number }).code;
    await supabase.from("agent_events").insert({
      event_type: "send_draft",
      status: "failed",
      contact_id: Number(contactId),
      started_at: startedAt,
      completed_at: new Date().toISOString(),
      error_message: String(err),
    });
    if (status === 404) {
      return Response.json(
        { error: "draft no longer exists in Gmail" },
        { status: 410 }
      );
    }
    if (status === 401) {
      return Response.json(
        { error: "Gmail auth failed; refresh token may need rotation" },
        { status: 401 }
      );
    }
    return Response.json({ error: "Gmail API error" }, { status: 502 });
  }

  const sentMessageId = sentMsg?.id ?? null;
  const sentThreadId = sentMsg?.threadId ?? null;

  // Step 7: try to fetch sent body
  let sentBody: string | null = draftRow.body;
  let sentSubject: string | null = draftRow.subject;
  if (sentMessageId) {
    try {
      const getMsgParams: gmail_v1.Params$Resource$Users$Messages$Get = {
        userId: "me",
        id: sentMessageId,
        format: "full",
      };
      const msgRes = await gmail.users.messages.get(getMsgParams);
      const headers = msgRes.data.payload?.headers ?? [];
      const subjectHeader = headers.find(
        (h) => h.name?.toLowerCase() === "subject"
      );
      if (subjectHeader?.value) sentSubject = subjectHeader.value;
      const bodyData = msgRes.data.payload?.body?.data;
      if (bodyData) {
        sentBody = Buffer.from(bodyData, "base64url").toString("utf8");
      }
    } catch {
      // Non-fatal: fall back to draft body
    }
  }

  // Step 8–9: compute transition
  const transition = STAGE_TRANSITIONS[stage];
  const nextStage = transition.next;
  const followupDate = computeFollowupDate(transition.cadenceKey);
  const today = todayUTC();

  // Step 10: update contact
  await supabase
    .from("contacts")
    .update({
      stage: nextStage,
      last_emailed: today,
      followup_date: followupDate,
      ...(sentMessageId ? { message_id: sentMessageId } : {}),
    })
    .eq("id", contactId);

  // Step 11: update draft_history
  const editDetected = sentBody !== draftRow.body;
  await supabase
    .from("draft_history")
    .update({
      sent_subject: sentSubject,
      sent_body: sentBody,
      sent_at: new Date().toISOString(),
      edit_detected: editDetected,
    })
    .eq("id", draftRow.id);

  // Step 12: insert email_messages (ignore duplicate message_id)
  if (sentMessageId) {
    await supabase.from("email_messages").upsert(
      {
        contact_id: Number(contactId),
        direction: "outgoing",
        message_id: sentMessageId,
        subject: sentSubject,
        body: sentBody,
        sent_at: new Date().toISOString(),
        ...(sentThreadId ? { thread_id: sentThreadId } : {}),
      },
      { onConflict: "message_id", ignoreDuplicates: true }
    );
  }

  // Step 13: log success event
  await supabase.from("agent_events").insert({
    event_type: "send_draft",
    status: "success",
    contact_id: Number(contactId),
    started_at: startedAt,
    completed_at: new Date().toISOString(),
  });

  // Step 14: return
  return Response.json(
    { ok: true, message_id: sentMessageId, stage: nextStage },
    { status: 200 }
  );
}
