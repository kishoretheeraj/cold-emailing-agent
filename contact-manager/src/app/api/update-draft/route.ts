export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";
import { getGmailClient } from "@/lib/gmail-server";
import { STAGE_TRANSITIONS } from "@/lib/cadence";
import type { gmail_v1 } from "googleapis";

function getSupabase() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

function toBase64Url(buf: Buffer): string {
  return buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// RFC 2047 MIME-word encoding for non-ASCII header values.
// Raw UTF-8 bytes in RFC 822 headers are invalid; without this, em dashes and
// other non-ASCII characters get double-encoded by receiving mail servers.
function mimeEncodeHeader(value: string): string {
  if (/^[\x00-\x7F]*$/.test(value)) return value;
  return `=?utf-8?b?${Buffer.from(value, "utf8").toString("base64")}?=`;
}

export async function POST(req: Request) {
  let contactId: string, subject: string, body: string;
  try {
    const parsed = (await req.json()) as {
      contact_id?: unknown;
      subject?: unknown;
      body?: unknown;
    };
    const rawId = parsed.contact_id;
    const coercedId =
      typeof rawId === "string" ? rawId :
      typeof rawId === "number" ? String(rawId) : null;
    if (
      !coercedId ||
      typeof parsed.subject !== "string" ||
      typeof parsed.body !== "string"
    ) {
      return Response.json(
        { error: "contact_id, subject, and body are required" },
        { status: 400 }
      );
    }
    contactId = coercedId;
    subject = parsed.subject;
    body = parsed.body;
  } catch {
    return Response.json(
      { error: "contact_id, subject, and body are required" },
      { status: 400 }
    );
  }

  const supabase = getSupabase();

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
  if (!(stage in STAGE_TRANSITIONS)) {
    return Response.json(
      { error: "contact not in a drafted state" },
      { status: 409 }
    );
  }

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

  let gmail;
  try {
    gmail = getGmailClient();
  } catch {
    return Response.json({ error: "Gmail auth not configured" }, { status: 401 });
  }

  // Read existing draft headers to preserve threading (In-Reply-To, References).
  let inReplyTo: string | null = null;
  let references: string | null = null;
  let fromEmail: string | null = null;
  try {
    const getParams: gmail_v1.Params$Resource$Users$Drafts$Get = {
      userId: "me",
      id: draftRow.gmail_draft_id,
      format: "metadata",
    };
    const existing = await gmail.users.drafts.get(getParams);
    const headers = existing.data.message?.payload?.headers ?? [];
    for (const h of headers) {
      const name = h.name?.toLowerCase();
      if (name === "in-reply-to") inReplyTo = h.value ?? null;
      else if (name === "references") references = h.value ?? null;
      else if (name === "from") fromEmail = h.value ?? null;
    }
  } catch {
    // Non-fatal — proceed without threading headers
  }

  // For follow-ups, look up the parent message's threadId so Gmail keeps the reply
  // in the same conversation. The draft may lack a threadId if the Python agent
  // fell back to IMAP APPEND (which strips threadId); this lookup repairs it.
  let parentThreadId: string | null = null;
  if (inReplyTo) {
    try {
      const clean = inReplyTo.trim().replace(/^<|>$/g, "");
      const searchRes = await gmail.users.messages.list({
        userId: "me",
        q: `rfc822msgid:${clean}`,
        maxResults: 1,
      });
      const parentMsg = searchRes.data.messages?.[0];
      if (parentMsg?.threadId) {
        parentThreadId = parentMsg.threadId;
      }
    } catch {
      // Non-fatal — proceed without threadId if lookup fails
    }
  }

  // Build RFC822 message with provided subject/body and preserved headers.
  const lines: string[] = [
    `From: ${fromEmail ?? contact.email}`,
    `To: ${contact.email}`,
    `Subject: ${mimeEncodeHeader(subject)}`,
    "Content-Type: text/plain; charset=utf-8",
  ];
  if (inReplyTo) lines.push(`In-Reply-To: ${inReplyTo}`);
  if (references) lines.push(`References: ${references}`);
  lines.push("", body);
  const rawMsg = toBase64Url(Buffer.from(lines.join("\r\n"), "utf8"));

  try {
    const updateParams: gmail_v1.Params$Resource$Users$Drafts$Update = {
      userId: "me",
      id: draftRow.gmail_draft_id,
      requestBody: {
        message: {
          raw: rawMsg,
          ...(parentThreadId ? { threadId: parentThreadId } : {}),
        },
      },
    };
    await gmail.users.drafts.update(updateParams);
  } catch {
    return Response.json({ error: "Gmail API error" }, { status: 502 });
  }

  // Persist edits so /queue shows the updated content on next load.
  await supabase
    .from("draft_history")
    .update({ subject, body })
    .eq("id", draftRow.id);

  return Response.json({ ok: true }, { status: 200 });
}
