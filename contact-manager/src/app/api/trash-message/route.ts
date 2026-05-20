export const runtime = "nodejs";

// Reserved for future "true undo" feature — not called from the UI in v1.
// Ships now so the API surface is complete when /queue undo wires up.
import { getGmailClient } from "@/lib/gmail-server";
import type { gmail_v1 } from "googleapis";

export async function POST(req: Request) {
  let messageId: string;
  try {
    const body = (await req.json()) as { message_id?: unknown };
    if (typeof body.message_id !== "string" || !body.message_id) {
      return Response.json({ error: "message_id is required" }, { status: 400 });
    }
    messageId = body.message_id;
  } catch {
    return Response.json({ error: "message_id is required" }, { status: 400 });
  }

  let gmail;
  try {
    gmail = getGmailClient();
  } catch {
    return Response.json({ error: "Gmail auth not configured" }, { status: 401 });
  }

  try {
    const trashParams: gmail_v1.Params$Resource$Users$Messages$Trash = {
      userId: "me",
      id: messageId,
    };
    await gmail.users.messages.trash(trashParams);
    return Response.json({ ok: true }, { status: 200 });
  } catch {
    return Response.json({ error: "Gmail API error" }, { status: 502 });
  }
}
