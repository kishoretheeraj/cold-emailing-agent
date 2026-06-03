import Anthropic from "@anthropic-ai/sdk";
import { createClient } from "@supabase/supabase-js";
import {
  assembleUserMessage,
  assembleCriticMessage,
  deriveAction,
} from "@/lib/assembleUserMessage";
import type { Contact, Prompt } from "@/lib/types";

export const runtime = "nodejs";
export const maxDuration = 30;

// ── Rate limiting ──────────────────────────────────────────────────────────────

const RATE_LIMIT = 10;
const WINDOW_MS = 60_000;

const _rateLimitMap = new Map<string, { count: number; windowStart: number }>();

// Exposed for tests only — clears the in-memory window so rate-limit state
// doesn't bleed between test cases running in the same module instance.
export function _resetRateLimitForTesting() {
  _rateLimitMap.clear();
}

function checkRateLimit(ip: string): boolean {
  const now = Date.now();
  const entry = _rateLimitMap.get(ip) ?? { count: 0, windowStart: now };
  if (now - entry.windowStart > WINDOW_MS) {
    _rateLimitMap.set(ip, { count: 1, windowStart: now });
    return true;
  }
  if (entry.count >= RATE_LIMIT) return false;
  _rateLimitMap.set(ip, { count: entry.count + 1, windowStart: entry.windowStart });
  return true;
}

// ── Supabase + Anthropic clients ───────────────────────────────────────────────

function getSupabase() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY! });

// ── Fence stripping ────────────────────────────────────────────────────────────

function stripFences(raw: string): string {
  return raw.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/i, "").trim();
}

// ── POST handler ───────────────────────────────────────────────────────────────

export async function POST(req: Request) {
  // Rate limit by IP
  const ip =
    req.headers.get("x-forwarded-for")?.split(",")[0].trim() ?? "unknown";
  if (!checkRateLimit(ip)) {
    return Response.json({ error: "too many requests" }, { status: 429 });
  }

  // Parse and validate body
  let body: Record<string, unknown>;
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    return Response.json({ error: "invalid JSON body" }, { status: 400 });
  }

  const contact_id =
    typeof body.contact_id === "string"
      ? body.contact_id
      : typeof body.contact_id === "number"
      ? String(body.contact_id)
      : null;
  const active_prompt_key =
    typeof body.active_prompt_key === "string" ? body.active_prompt_key : null;
  const sandbox_value =
    typeof body.sandbox_value === "string" ? body.sandbox_value : null;
  const mode =
    body.mode === "writer" || body.mode === "critic" ? body.mode : null;

  if (!contact_id || !active_prompt_key || sandbox_value === null || !mode) {
    return Response.json(
      { error: "contact_id, active_prompt_key, sandbox_value, and mode are required" },
      { status: 400 }
    );
  }

  const critic_draft_body =
    typeof body.critic_draft_body === "string" ? body.critic_draft_body : "";
  const critic_draft_subject =
    typeof body.critic_draft_subject === "string" ? body.critic_draft_subject : "";

  const sb = getSupabase();

  // Fetch full contact record
  const { data: contactData, error: contactErr } = await sb
    .from("contacts")
    .select("*")
    .eq("id", contact_id)
    .single();

  if (contactErr || !contactData) {
    return Response.json({ error: "contact not found" }, { status: 404 });
  }
  const contact = contactData as Contact;

  // Fetch all prompts
  const { data: promptRows } = await sb
    .from("prompts")
    .select("key,value");

  const prompts: Record<string, string> = {};
  for (const row of (promptRows ?? []) as Prompt[]) {
    prompts[row.key] = row.value;
  }

  // Merge sandbox value: replace saved value with what's in the editor
  prompts[active_prompt_key] = sandbox_value;

  try {
    if (mode === "critic") {
      // ── Critic mode ────────────────────────────────────────────────────────
      const { userMessage, systemMessage } = assembleCriticMessage(
        contact,
        prompts,
        critic_draft_subject,
        critic_draft_body
      );

      const response = await anthropic.messages.create({
        model: "claude-sonnet-4-6",
        max_tokens: 1000,
        system: systemMessage,
        messages: [{ role: "user", content: userMessage }],
      });

      const block = response.content.find((b) => b.type === "text");
      const raw = block && "text" in block ? block.text : "";
      const cleaned = stripFences(raw);

      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(cleaned) as Record<string, unknown>;
      } catch {
        return Response.json({ error: "upstream parse failure" }, { status: 502 });
      }

      return Response.json({
        kind: "critic",
        score: typeof parsed.score === "number" ? parsed.score : 0,
        verdict: typeof parsed.verdict === "string" ? parsed.verdict : "UNKNOWN",
        feedback: typeof parsed.feedback === "string" ? parsed.feedback : "",
        killed_by: Array.isArray(parsed.killed_by) ? parsed.killed_by : [],
        failed_soft_criteria: Array.isArray(parsed.failed_soft_criteria)
          ? parsed.failed_soft_criteria
          : [],
        rewrite_required:
          typeof parsed.rewrite_required === "boolean"
            ? parsed.rewrite_required
            : false,
      });
    }

    // ── Writer mode ───────────────────────────────────────────────────────────
    const action = deriveAction(contact);
    const { userMessage, systemMessage } = assembleUserMessage(
      contact,
      action,
      prompts
    );

    const response = await anthropic.messages.create({
      model: "claude-sonnet-4-6",
      max_tokens: 1500,
      system: systemMessage,
      messages: [{ role: "user", content: userMessage }],
    });

    const block = response.content.find((b) => b.type === "text");
    const body_text = block && "text" in block ? block.text : "";

    // Generate subject only for first-touch actions
    const isFirstTouch =
      action === "send_first_touch" || action === "send_applied_intro";

    let subject: string | undefined;
    if (isFirstTouch) {
      const subjectTpl =
        prompts["subject_prompt"] ??
        `Generate a short email subject line.\n\nTo: {name} at {company}\nMode: {mode}\nRole (if applied): {job_title}\nEmail body:\n{body}\n\nRULES:\n- Max 8 words\n- Return ONLY the subject line, nothing else`;
      const subjectPrompt = subjectTpl
        .replace(/\{name\}/g, contact.name ?? "")
        .replace(/\{company\}/g, contact.company ?? "")
        .replace(/\{mode\}/g, contact.mode ?? "outreach")
        .replace(/\{job_title\}/g, contact.job_title ?? "")
        .replace(/\{body\}/g, body_text.slice(0, 500));

      const subjectResp = await anthropic.messages.create({
        model: "claude-sonnet-4-6",
        max_tokens: 50,
        system: prompts["sender_profile"] ?? "",
        messages: [{ role: "user", content: subjectPrompt }],
      });
      const sBlock = subjectResp.content.find((b) => b.type === "text");
      subject =
        sBlock && "text" in sBlock
          ? sBlock.text.trim().replace(/^["']|["']$/g, "")
          : undefined;
    }

    return Response.json({
      kind: "writer",
      body: body_text,
      ...(subject !== undefined ? { subject } : {}),
    });
  } catch {
    return Response.json(
      { error: "preview service unavailable" },
      { status: 500 }
    );
  }
}
