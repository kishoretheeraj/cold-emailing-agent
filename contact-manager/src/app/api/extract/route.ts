import Anthropic from "@anthropic-ai/sdk";
import type { ExtractedContact } from "@/lib/types";

export const runtime = "nodejs";

const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY! });

const SYSTEM_PROMPT = `Extract contact information from this text for a job search cold email tool. Return ONLY a JSON object with these fields: name (string), email (string), company (string), role (string, their job title), detail (string, one specific interesting thing about them to reference in an email), tier (integer 1-3 where 1=dream company, 2=strong fit, 3=worth a shot), mode (string: 'outreach' if this is a new person to reach out to, 'applied' if the user already applied to a job here), dartmouth (boolean, true if any Dartmouth/Tuck/Thayer/Irving connection is mentioned), job_title (string, the role applied for — only for applied mode), job_description (string, full JD text if present), applied_date (string YYYY-MM-DD format or null), notes (string, anything else relevant). If a field cannot be determined, use null.`;

export async function POST(req: Request) {
  try {
    const { text } = (await req.json()) as { text?: string };

    if (!text || !text.trim()) {
      return Response.json({ error: "text is required" }, { status: 400 });
    }

    const response = await anthropic.messages.create({
      model: "claude-sonnet-4-6",
      max_tokens: 2000,
      messages: [
        {
          role: "user",
          content: `${SYSTEM_PROMPT}\n\nText to parse:\n${text}`,
        },
      ],
    });

    const block = response.content.find((b) => b.type === "text");
    const raw = block && "text" in block ? block.text : "";

    // Strip code fences if present
    const cleaned = raw
      .trim()
      .replace(/^```(?:json)?\s*/i, "")
      .replace(/\s*```$/i, "")
      .trim();

    let parsed: ExtractedContact;
    try {
      parsed = JSON.parse(cleaned);
    } catch {
      return Response.json(
        { error: "Claude did not return valid JSON", raw },
        { status: 502 }
      );
    }

    // Validate required fields — return a clear per-field error rather than
    // inserting malformed data into Supabase.
    const missing: string[] = [];
    if (!parsed.name || typeof parsed.name !== "string" || !parsed.name.trim()) {
      missing.push("name");
    }
    if (
      !parsed.email ||
      typeof parsed.email !== "string" ||
      !parsed.email.includes("@")
    ) {
      missing.push("email");
    }
    if (
      !parsed.company ||
      typeof parsed.company !== "string" ||
      !parsed.company.trim()
    ) {
      missing.push("company");
    }
    if (missing.length > 0) {
      const errors = missing
        .map((f) => `Could not extract ${f} — please add it manually`)
        .join("; ");
      return Response.json({ error: errors, raw }, { status: 422 });
    }

    // Auto-correct optional fields to safe defaults rather than storing garbage.
    if (!["outreach", "applied"].includes(parsed.mode as string)) {
      parsed = { ...parsed, mode: "outreach" };
    }
    if (![1, 2, 3].includes(parsed.tier as number)) {
      parsed = { ...parsed, tier: 2 };
    }
    if (typeof parsed.dartmouth !== "boolean") {
      parsed = { ...parsed, dartmouth: false };
    }

    return Response.json({ data: parsed });
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : "unknown error";
    return Response.json({ error: message }, { status: 500 });
  }
}
