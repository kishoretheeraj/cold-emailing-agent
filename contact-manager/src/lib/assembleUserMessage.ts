import type { Contact } from "@/lib/types";

// ── Types ──────────────────────────────────────────────────────────────────────

export type AgentAction =
  | "send_first_touch"
  | "send_followup1"
  | "send_followup2"
  | "send_breakup"
  | "send_applied_intro"
  | "send_applied_followup"
  | "send_networking_first_touch"
  | "send_networking_followup";

export type AssembleResult = {
  userMessage: string;
  systemMessage: string;
};

// ── Fallback constants (mirrors config.py — Supabase values take precedence) ──

const SENDER_PROFILE_FALLBACK = `Name: Kishore
Program: MEM, Dartmouth (Thayer-Tuck), graduating November 2026
Background: 3 years PM/UX at Protium Finance (Series B fintech, India).
Built compliance dashboards, co-lending platforms, white-label B2B SaaS,
and insurance integration systems. Promoted four times. CS undergrad.
Differentiators:
- Hands-on builder: shipped full-stack AI products (Claude API, Next.js, Supabase)
- Mentored 150+ students in UX and design thinking across India
- Dartmouth Irving Institute Energy Ventures program participant
- Tuck MBA coursework alongside engineering management degree
- Built deepfake audio detection tool for Hiya (telephony company)
Tone: Warm, confident, conversational. Simple English. No em dashes. No filler.
Sign-off: Kishore / MEM '26 | Dartmouth`;

const TIER_INSTRUCTION_FALLBACK: Record<number, string> = {
  1: "DREAM company. Deep personalization. Reference specific company news, products, or the contact's background. Show you've done real research.",
  2: "STRONG FIT. Moderate personalization. Reference the company and why you're interested, but don't overdo it.",
  3: "WORTH A SHOT. Keep it efficient. Brief, professional, to the point.",
};

const TEMPLATE_INSTRUCTION_FALLBACK: Record<string, string> = {
  cold_intro: "First-touch cold email. End with a soft ask for a 15-min chat.",
  follow_up_1:
    "Gentle follow-up (day 5). Reference something new — a recent " +
    "company announcement, article, or product launch. Not pushy.",
  follow_up_2:
    "Second follow-up (day 10). Shorter, mild urgency. Mention " +
    "you'll stop reaching out if now isn't the right time.",
  breakup: "Final email. 2-3 sentences max. Respectful close. Leave door open.",
};

const DARTMOUTH_INSTRUCTION_FALLBACK =
  "ALUMNI CONNECTION DETECTED: Treat this as warm alumni outreach, not cold. " +
  "Reference the shared Dartmouth/Tuck/Thayer connection naturally and early. " +
  "Use a peer-to-peer tone, not a stranger-to-stranger tone. Soften the ask.";

const OUTREACH_PROMPT_FALLBACK = `You are writing a cold outreach email for a job seeker.
Generate ONLY the email body. No subject line, no sign-off name, no metadata.

SENDER PROFILE:
{profile}

RECIPIENT:
- Name: {name}
- Company: {company}
- Role: {role}
- Detail to reference: {detail}

TIER: {tier} — {tier_instruction}
TEMPLATE: {template} — {template_instruction}
{dartmouth_instruction}

RULES:
- Max 100 words
- No filler phrases ("I hope this finds you well", etc.)
- No em dashes
- Simple, conversational English
- Reference ONE specific thing about the person or company
- One clear call-to-action
- Sound human, not AI-generated`;

const APPLIED_INTRO_PROMPT_FALLBACK = `You are writing an email from a job applicant to a
hiring manager. The applicant already submitted their application through the
company's ATS. This email is NOT asking if a role exists — it is to get noticed
and land directly in the hiring manager's hands, bypassing the recruiter queue.
Tone is confident but not aggressive.

SENDER PROFILE:
{profile}

HIRING MANAGER:
- Name: {name}
- Title: {role}
- Company: {company}

ROLE APPLIED FOR: {job_title}

JOB DESCRIPTION:
{job_description}

APPLICATION DATE: {applied_date}
{dartmouth_instruction}

TASK:
1. Read the job description carefully
2. Identify the 3 requirements that best match the sender's background
3. Write exactly 3 bullet points — one line each, under 15 words,
   with a specific metric or concrete outcome where possible
4. Write the full email body in this structure:
   Line 1: "I applied for [job_title] at [company] [X days ago / yesterday]
            and wanted to reach out directly."
   Line 2: "I think there is a strong fit here."
   Blank line
   "A few things I would bring:"
   • Bullet 1
   • Bullet 2
   • Bullet 3
   Blank line
   Closing: one sentence, soft, not pushy (e.g. "Happy to share more context
   if helpful — my application is already in the system.")

RULES:
- Max 130 words total
- No filler phrases
- No em dashes
- Bullets must be specific — no vague claims like "strong communication skills"
- Do NOT include subject line, name sign-off, or any metadata
- Sound like a confident person, not a cover letter`;

const APPLIED_FOLLOWUP_PROMPT_FALLBACK = `You are writing a brief follow-up email from a job
applicant to a hiring manager. An intro email with 3 bullet points was sent 5
days ago. This follow-up is short and adds one new piece of value — do NOT
repeat the bullets from the first email.

SENDER PROFILE:
{profile}

HIRING MANAGER:
- Name: {name}
- Title: {role}
- Company: {company}

ROLE APPLIED FOR: {job_title}

RULES:
- Max 60 words
- Briefly reference that a previous email was sent
- Add ONE new observation about the company, role, or industry
- End with a soft close ("Happy to connect if timing works")
- No bullets, no em dashes, no filler
- Do NOT include subject line or name sign-off`;

const CRITIC_PROMPT_FALLBACK = `You are a strict editor reviewing
cold email drafts before they go out. Your job is to catch
drafts that sound generic, AI-written, or off-voice for the
sender.

The sender is:
{sender_profile}

The contact context is:
{contact_context}

The draft to review:
Subject: {subject}
Body:
{body}

Evaluate against these criteria. Each is binary pass/fail:

1. Personalization: the body references something specific from
the contact context (their role, company, the detail hook,
a Tuck connection). Not just their name and company.
2. Voice match: warm, confident, conversational. NOT corporate.
NOT overly polite. NOT 'I hope this finds you well' style.
3. No filler: zero unnecessary intro phrases. The first sentence
does work, not throat-clearing.
4. No AI tells: no em dashes, no 'I wanted to reach out', no
'I hope this email finds you well', no 'Just wanted to ping',
no 'I trust this message finds you', no excessive politeness.
5. Specific ask: ends with a clear, specific next step. Not
'let me know if you have time' or 'would love to chat'.
6. Length: under 120 words for the body. Tight.
7. Format: ends with the sender sign-off exactly as defined
in the sender profile.

Return a single JSON object, nothing else. No markdown, no
code fences, no preamble:

{{"score": <integer 0 to 7, count of criteria passed>,
  "failed_criteria": [<list of criterion numbers that failed>],
  "feedback": "<one or two sentences telling the writer
               specifically what to fix, written as a direct
               instruction>"}}`;

const NETWORKING_PROMPT_FALLBACK = `You are writing a warm networking email for a job seeker.
This is NOT a job pitch. Do not mention roles, openings, hiring, or applying.
The only goal is a genuine, low-pressure ask for a short conversation.
Generate ONLY the email body. No subject line, no sign-off name, no metadata.

SENDER PROFILE:
{profile}

RECIPIENT:
- Name: {name}
- Company: {company}

CONNECTION:
{connection_context_instruction}
{dartmouth_instruction}

RULES:
- Max 120 words
- No filler phrases ("I hope this finds you well", etc.)
- No em dashes
- Never mention a role, opening, application, or hiring
- No attachment references
- One clear, low-commitment ask: a 15-20 minute chat, with a couple of
  flexible time windows offered so it's easy to say yes
- Simple, conversational, personal tone — not a pitch
- Sound human, not AI-generated`;

const NETWORKING_FOLLOWUP_PROMPT_FALLBACK = `You are writing a brief, gentle follow-up to a
networking email sent about a week ago. This is the only follow-up that will
be sent — do not imply there will be another. Keep it low-pressure; the
recipient owes nothing.

SENDER PROFILE:
{profile}

RECIPIENT:
- Name: {name}
- Company: {company}
{dartmouth_instruction}

RULES:
- Max 60 words
- Briefly reference that a previous note was sent
- Restate the same low-commitment ask (15-20 minute chat), do not escalate it
- No role, opening, application, or hiring language
- No em dashes, no filler
- End with a soft, easy-out close (e.g. "No worries at all if timing's not right")
- Do NOT include subject line or name sign-off`;

// ── Internal maps (mirrors emailer.py) ────────────────────────────────────────

const ACTION_TO_TEMPLATE: Record<string, string> = {
  send_first_touch: "cold_intro",
  send_followup1: "follow_up_1",
  send_followup2: "follow_up_2",
  send_breakup: "breakup",
};

const TEMPLATE_TO_PROMPT_KEY: Record<string, string> = {
  cold_intro: "outreach_first_touch_instruction",
  follow_up_1: "outreach_followup1_instruction",
  follow_up_2: "outreach_followup2_instruction",
  breakup: "outreach_breakup_instruction",
};

// ── Helpers ────────────────────────────────────────────────────────────────────

// Mirrors Python's str.format(**kwargs): replaces {key} tokens.
// Extra kwargs in Python are silently ignored; missing keys leave the token as-is.
function pythonFormat(template: string, kwargs: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (match, key) => kwargs[key] ?? match);
}

// Mirrors emailer._is_dartmouth
export function isDartmouth(contact: Contact): boolean {
  if (contact.dartmouth) return true;
  const detail = (contact.detail ?? "").toLowerCase();
  return ["dartmouth", "tuck", "thayer", "irving", "big green"].some((kw) =>
    detail.includes(kw)
  );
}

function getTierInstruction(prompts: Record<string, string>, tier: number): string {
  const key = `tier_${tier}_instruction`;
  return prompts[key] ?? TIER_INSTRUCTION_FALLBACK[tier] ?? TIER_INSTRUCTION_FALLBACK[2];
}

function getTemplateInstruction(prompts: Record<string, string>, template: string): string {
  const key = TEMPLATE_TO_PROMPT_KEY[template] ?? null;
  if (key && prompts[key] != null) return prompts[key];
  return TEMPLATE_INSTRUCTION_FALLBACK[template] ?? "";
}

function getDartmouthInstruction(prompts: Record<string, string>, dart: boolean): string {
  if (!dart) return "";
  return prompts["dartmouth_instruction"] ?? DARTMOUTH_INSTRUCTION_FALLBACK;
}

// Mirrors emailer._connection_context_instruction
function getConnectionContextInstruction(contact: Contact): string {
  const value = (contact.connection_context ?? "").trim();
  if (value) return `Lead with this specific hook: ${value}`;
  return (
    "No connection hook provided — do not invent one. Open with a brief, " +
    "honest, low-pressure reason for reaching out instead."
  );
}

// ── deriveAction ───────────────────────────────────────────────────────────────

// Mirrors _decide_outreach / _decide_applied from agent.py.
// For *_drafted stages (where the agent would skip), we return the action
// that was used to create that draft so the Lab always shows something useful.
export function deriveAction(contact: Contact): AgentAction {
  const mode = contact.mode ?? "outreach";
  const stage = contact.stage ?? "new";

  if (mode === "applied") {
    if (
      stage === "applied_followup_drafted" ||
      stage === "applied_followup_sent"
    ) {
      return "send_applied_followup";
    }
    return "send_applied_intro";
  }

  if (mode === "networking") {
    if (
      stage === "networking_followup_drafted" ||
      stage === "networking_followup_sent"
    ) {
      return "send_networking_followup";
    }
    return "send_networking_first_touch";
  }

  // outreach (and all other modes fall through to outreach)
  if (stage === "followup1_drafted" || stage === "followup1_sent") return "send_followup1";
  if (stage === "followup2_drafted" || stage === "followup2_sent") return "send_followup2";
  if (stage === "breakup_drafted" || stage === "breakup_sent") return "send_breakup";
  return "send_first_touch"; // covers: new, first_touch_drafted, first_touch_sent, and all others
}

// ── Voice DNA ──────────────────────────────────────────────────────────────────

// Mirrors emailer.VOICE_INJECTION_DEFAULT. Keep byte-identical to the Python
// copy or this preview diverges from what the agent actually sends.
const VOICE_INJECTION_FALLBACK = `

VOICE MATCH
Write in the sender's own voice, described below. Match the rhythm and habits.
Do not imitate any specific sentence. All other formatting and content rules
above still apply and take precedence over this section.

{voice_dna}
`;

// Mirrors emailer._FIRST_TOUCH_ACTIONS.
const FIRST_TOUCH_ACTIONS = new Set<AgentAction>([
  "send_first_touch",
  "send_applied_intro",
  "send_networking_first_touch",
]);

function buildVoiceBlock(action: AgentAction, prompts: Record<string, string>): string {
  if (!FIRST_TOUCH_ACTIONS.has(action)) return "";
  const voiceDna = (prompts["voice_dna"] ?? "").trim();
  if (!voiceDna) return "";
  return pythonFormat(VOICE_INJECTION_FALLBACK, { voice_dna: voiceDna });
}

// ── assembleUserMessage ────────────────────────────────────────────────────────

// Mirrors emailer._build_outreach_prompt, _build_applied_intro_prompt,
// _build_applied_followup_prompt from the Python agent.
// Research injection is intentionally omitted (v1 Lab scope).
export function assembleUserMessage(
  contact: Contact,
  action: AgentAction,
  prompts: Record<string, string>
): AssembleResult {
  const profile = prompts["sender_profile"] ?? SENDER_PROFILE_FALLBACK;
  const dart = isDartmouth(contact);
  const dartmouth_instruction = getDartmouthInstruction(prompts, dart);
  const systemMessage = profile;
  const voiceBlock = buildVoiceBlock(action, prompts);

  if (action === "send_applied_followup") {
    const tpl = prompts["applied_followup_prompt"] ?? APPLIED_FOLLOWUP_PROMPT_FALLBACK;
    const userMessage = pythonFormat(tpl, {
      profile,
      name: contact.name ?? "",
      role: contact.role ?? "",
      company: contact.company ?? "",
      job_title: contact.job_title ?? "the role",
      dartmouth_instruction,
    });
    return { userMessage, systemMessage };
  }

  if (action === "send_networking_followup") {
    const tpl = prompts["networking_followup_prompt"] ?? NETWORKING_FOLLOWUP_PROMPT_FALLBACK;
    const userMessage = pythonFormat(tpl, {
      profile,
      name: contact.name ?? "",
      company: contact.company ?? "",
      dartmouth_instruction,
    });
    return { userMessage, systemMessage };
  }

  if (action === "send_networking_first_touch") {
    const tpl = prompts["networking_prompt"] ?? NETWORKING_PROMPT_FALLBACK;
    const userMessage = pythonFormat(tpl, {
      profile,
      name: contact.name ?? "",
      company: contact.company ?? "",
      connection_context_instruction: getConnectionContextInstruction(contact),
      dartmouth_instruction,
    });
    return { userMessage: userMessage + voiceBlock, systemMessage };
  }

  if (action === "send_applied_intro") {
    const tpl = prompts["applied_intro_prompt"] ?? APPLIED_INTRO_PROMPT_FALLBACK;
    const today = new Date().toISOString().slice(0, 10);
    const userMessage = pythonFormat(tpl, {
      profile,
      name: contact.name ?? "",
      role: contact.role ?? "",
      company: contact.company ?? "",
      job_title: contact.job_title ?? "the role",
      job_description: contact.job_description ?? "",
      applied_date: contact.applied_date ?? today,
      dartmouth_instruction,
    });
    return { userMessage: userMessage + voiceBlock, systemMessage };
  }

  // Outreach actions: send_first_touch, send_followup1, send_followup2, send_breakup
  const tier = contact.tier ?? 2;
  const template = ACTION_TO_TEMPLATE[action];
  const tpl = prompts["outreach_prompt"] ?? OUTREACH_PROMPT_FALLBACK;
  const userMessage = pythonFormat(tpl, {
    profile,
    name: contact.name ?? "",
    company: contact.company ?? "",
    role: contact.role ?? "",
    detail: contact.detail ?? "",
    tier: String(tier),
    tier_instruction: getTierInstruction(prompts, tier),
    template,
    template_instruction: getTemplateInstruction(prompts, template),
    dartmouth_instruction,
  });
  return { userMessage: userMessage + voiceBlock, systemMessage };
}

// ── assembleCriticMessage ─────────────────────────────────────────────────────

// Mirrors emailer._run_critic from the Python agent.
export function assembleCriticMessage(
  contact: Contact,
  prompts: Record<string, string>,
  draftSubject: string,
  draftBody: string
): AssembleResult {
  const profile = prompts["sender_profile"] ?? SENDER_PROFILE_FALLBACK;

  const parts: string[] = [];
  if (contact.name) parts.push(`Name: ${contact.name}`);
  if (contact.company) parts.push(`Company: ${contact.company}`);
  if (contact.role) parts.push(`Role: ${contact.role}`);
  if (contact.detail) parts.push(`Detail: ${contact.detail}`);
  if (contact.tier) parts.push(`Tier: ${contact.tier}`);
  if (contact.dartmouth) parts.push(`Dartmouth: yes`);
  const contact_context = parts.join("\n");

  const tpl = prompts["critic_prompt"] ?? CRITIC_PROMPT_FALLBACK;
  const userMessage = pythonFormat(tpl, {
    sender_profile: profile,
    contact_context,
    subject: draftSubject,
    body: draftBody,
  });
  return { userMessage, systemMessage: profile };
}
