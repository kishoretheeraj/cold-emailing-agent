import Anthropic from "@anthropic-ai/sdk";
import type { ExtractedContact } from "@/lib/types";

export const runtime = "nodejs";

const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY! });

const PROMPT = `Analyze the pasted text. If it contains ONE contact, return a single JSON object. If it contains MULTIPLE contacts (indicated by multiple names, emails, or contact blocks), return a JSON array of contact objects.

For each contact extract:
- name (string) — first + last name only, strip T'XX or D'XX suffix
- email (string) — primary email address, null if not found
- company (string)
- role (string) — from Title field
- detail (string) — generate a one-line personalization hook based on their role, company, and industry. Make it specific and useful for a cold email opener. Do not use filler like 'experienced leader'.
- tier (integer 1-3) — default 2
- mode (string) — default 'outreach'
- dartmouth (boolean) — true if T'XX, D'XX, Tuck, Thayer, Dartmouth, or Irving appears anywhere near their name or in the text
- notes (string) — if dartmouth is true, set to 'Tuck Class of [year]' where year is derived from T'XX (T'06 = 2006, T'96 = 1996, T'64 = 1964). Otherwise null.
- resume_url (string or null) — any Google Drive or resume link found

Ignore: addresses, phone numbers, fax numbers, function field, industry field.

Return ONLY valid JSON. No explanation, no markdown fences, no preamble. Single contact = object. Multiple contacts = array.`;

type RawContact = ExtractedContact & { missing_email?: boolean };

function normalizeContact(c: Record<string, unknown>): RawContact {
  const contact = { ...(c as RawContact) };
  if (!["outreach", "applied"].includes(contact.mode as string)) {
    contact.mode = "outreach";
  }
  if (![1, 2, 3].includes(contact.tier as number)) {
    contact.tier = 2;
  }
  if (typeof contact.dartmouth !== "boolean") {
    contact.dartmouth = false;
  }
  return contact;
}

export async function POST(req: Request) {
  try {
    const { text } = (await req.json()) as { text?: string };

    if (!text || !text.trim()) {
      return Response.json({ error: "text is required" }, { status: 400 });
    }

    const response = await anthropic.messages.create({
      model: "claude-sonnet-4-6",
      max_tokens: 4000,
      messages: [{ role: "user", content: `${PROMPT}\n\nText to parse:\n${text}` }],
    });

    const block = response.content.find((b) => b.type === "text");
    const raw = block && "text" in block ? block.text : "";

    let cleaned = raw.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/i, "").trim();

    let parsed: Record<string, unknown> | Record<string, unknown>[];
    try {
      parsed = JSON.parse(cleaned);
    } catch {
      // Retry: strip any remaining fences more aggressively
      cleaned = cleaned.replace(/```[\s\S]*?```/g, "").trim();
      try {
        parsed = JSON.parse(cleaned);
      } catch {
        return Response.json({ error: "Could not parse contacts", raw }, { status: 502 });
      }
    }

    const rawList = Array.isArray(parsed) ? parsed : [parsed];
    const contacts: RawContact[] = [];

    for (const c of rawList) {
      if (typeof c !== "object" || c === null) continue;
      const contact = normalizeContact(c as Record<string, unknown>);
      // name and company are required — skip the contact entirely if missing
      if (!contact.name?.toString().trim()) continue;
      if (!contact.company?.toString().trim()) continue;
      // email is required — mark as missing rather than skipping
      if (!contact.email?.toString().includes("@")) {
        contacts.push({ ...contact, email: null, missing_email: true });
      } else {
        contacts.push({ ...contact, missing_email: false });
      }
    }

    if (contacts.length === 0) {
      return Response.json(
        { error: "No valid contacts found — name and company are required for each" },
        { status: 422 }
      );
    }

    return Response.json({
      contacts,
      count: contacts.length,
      is_bulk: contacts.length > 1,
    });
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : "unknown error";
    return Response.json({ error: message }, { status: 500 });
  }
}
