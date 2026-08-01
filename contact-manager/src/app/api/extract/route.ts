import Anthropic from "@anthropic-ai/sdk";
import type { ExtractedContact } from "@/lib/types";

export const runtime = "nodejs";
export const maxDuration = 30;

const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY! });

// LOCKED — not migrated to the prompts table.
// This prompt is bound to the ExtractedContact JSON schema validated by normalizeContact().
// Changing the output shape requires updating ExtractedContact in types.ts in sync.
// Editing via /prompts would decouple the prompt from its type contract without a code deploy.
const PROMPT = `Analyze the pasted text. If it contains ONE contact, return a single JSON object. If it contains MULTIPLE contacts (multiple distinct names with their own emails or contact blocks), return a JSON array.

For each contact extract these fields:
- name: string. First and last name only. Strip T'XX, D'XX, parenthetical nicknames, and honorifics (Mr/Ms/Dr).
- email: string or null. Primary email address. Null if absent.
- company: string or null. Null if absent.
- role: string or null. From Title or equivalent field.
- detail: string. One line personalization hook. Be SPECIFIC only if the input provides context (recent news, mutual interest, role history). If the input is minimal (just title and company), return a short factual hook such as 'President at Acme Corp'. Do NOT invent specifics.
- tier: integer 1, 2, or 3. Default 2.
- mode: always 'outreach' for bulk paste. Never output 'applied' from a paste, because applied mode requires a job description that is not present in bulk pasted text.
- dartmouth: boolean. True ONLY if T'XX, D'XX, Tuck, Thayer, Dartmouth, or Irving appears in the SAME contact block (not a page header or footer that applies to every contact). When in doubt, false.
- notes: string or null. If dartmouth is true and a T'YY pattern appears, set notes to 'Tuck Class of YYYY' using this rule:
    Let CY = the last 2 digits of the current year (2026 gives 26).
    If YY is less than or equal to CY + 5: year is 20YY.
      Examples: T'24 maps to 2024, T'29 maps to 2029.
    If YY is greater than CY + 5: year is 19YY.
      Examples: T'64 maps to 1964, T'96 maps to 1996.
  Otherwise null.
- resume_url: string or null. Any Google Drive or resume link.
- state: string or null. Two-letter US state code if a clear US location is mentioned (e.g. "Austin, TX" -> "TX", "NYC" -> "NY", "Bay Area" -> "CA", "based in Boston" -> "MA"). Null if ambiguous, non-US, or not mentioned. Do NOT infer from company name alone — if the paste says "Sarah, Stripe" with no location, return null.

Ignore: addresses, phone numbers, fax numbers, Function field, Industry field.

Return ONLY valid JSON. No explanation, no markdown, no preamble, no trailing text. Single contact returns a JSON object. Multiple contacts return a JSON array.

Example single-contact output:
{"name":"Jane Doe","email":"jane@acme.com","company":"Acme","role":"CEO","detail":"CEO at Acme since 2019","tier":2,"mode":"outreach","dartmouth":false,"notes":null,"resume_url":null,"state":"NY"}`;

function normalizeContact(c: Record<string, unknown>): ExtractedContact {
  const rawName = typeof c.name === "string" ? c.name.trim() : null;
  const rawCompany = typeof c.company === "string" ? c.company.trim() : null;

  const requiredMissingFields: string[] = [];
  if (!rawName) requiredMissingFields.push("name");
  if (!rawCompany) requiredMissingFields.push("company");
  const missingRequired = requiredMissingFields.length > 0;

  const rawEmail = typeof c.email === "string" ? c.email.trim() : null;
  const missingEmail = !rawEmail || !rawEmail.includes("@");

  let mode: "outreach" | "applied" = "outreach";
  if (c.mode === "applied") mode = "applied";

  let tier = 2;
  if (c.tier === 1 || c.tier === 2 || c.tier === 3) tier = c.tier;

  const dartmouth = typeof c.dartmouth === "boolean" ? c.dartmouth : false;

  const rawState = typeof c.state === "string" ? c.state.trim().toUpperCase() : null;

  return {
    name: rawName ?? "",
    email: missingEmail ? null : rawEmail,
    company: rawCompany ?? null,
    role: typeof c.role === "string" ? c.role : null,
    detail: typeof c.detail === "string" ? c.detail : null,
    tier,
    mode,
    dartmouth,
    job_title: null,
    job_description: null,
    applied_date: null,
    // Bulk-paste extraction never infers a networking connection hook or mode —
    // a genuine hook can't be reliably read off pasted directory/conference text,
    // and guessing one risks fabricating it. Always null/outreach out of extraction;
    // networking contacts are created deliberately via the Structured Form.
    connection_context: null,
    notes: typeof c.notes === "string" ? c.notes : null,
    resume_url: typeof c.resume_url === "string" ? c.resume_url : null,
    state: rawState && rawState.length === 2 ? rawState : null,
    missing_email: missingEmail,
    ...(missingRequired
      ? { missing_required: true, required_missing_fields: requiredMissingFields }
      : {}),
  };
}

export async function POST(req: Request) {
  let text: string;
  try {
    const body = (await req.json()) as { text?: string };
    text = body.text ?? "";
  } catch {
    return Response.json({ error: "text is required" }, { status: 400 });
  }

  if (!text || !text.trim()) {
    return Response.json({ error: "text is required" }, { status: 400 });
  }
  if (text.length > 20000) {
    return Response.json(
      { error: "input too large; paste no more than 20000 characters" },
      { status: 400 }
    );
  }
  if ((text.match(/@/g) ?? []).length > 50) {
    return Response.json(
      { error: "too many contacts; max 50 per paste" },
      { status: 400 }
    );
  }

  try {
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
      cleaned = cleaned.replace(/```[\s\S]*?```/g, "").trim();
      try {
        parsed = JSON.parse(cleaned);
      } catch {
        return Response.json({ error: "upstream parse failure" }, { status: 502 });
      }
    }

    const rawList = Array.isArray(parsed) ? parsed : [parsed];
    const contacts: ExtractedContact[] = [];

    for (const c of rawList) {
      if (typeof c !== "object" || c === null) continue;
      contacts.push(normalizeContact(c as Record<string, unknown>));
    }

    if (contacts.length === 0) {
      return Response.json({ error: "no contacts extracted" }, { status: 502 });
    }

    return Response.json({
      contacts,
      count: contacts.length,
      is_bulk: contacts.length > 1,
    });
  } catch {
    return Response.json(
      { error: "extraction service unavailable" },
      { status: 500 }
    );
  }
}
