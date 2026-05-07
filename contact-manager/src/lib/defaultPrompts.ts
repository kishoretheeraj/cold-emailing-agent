// Fallback defaults — match config.py exactly.
// Used by the Reset to Default button in the prompts editor.

export const DEFAULT_PROMPTS: Record<string, string> = {
  sender_profile: `Name: Kishore
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
Sign-off: Kishore / MEM '26 | Dartmouth`,

  outreach_prompt: `You are writing a cold outreach email for a job seeker.
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
- Sound human, not AI-generated`,

  applied_intro_prompt: `You are writing an email from a job applicant to a
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
- Sound like a confident person, not a cover letter`,

  applied_followup_prompt: `You are writing a brief follow-up email from a job
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
- Do NOT include subject line or name sign-off`,

  subject_prompt: `Generate a short email subject line.

To: {name} at {company}
Mode: {mode}
Role (if applied): {job_title}
Email body:
{body}

RULES:
- Max 8 words
- Mode "outreach": casual, personal feel
- Mode "applied": can reference the role (e.g. "Re: Senior PM application")
- Lowercase preferred
- No clickbait
- Return ONLY the subject line, nothing else`,
};

// ── Prompt metadata — labels, variable chips, row heights ─────────────────────

export type PromptMeta = {
  key: string;
  label: string;
  description: string;
  variables: string[] | null; // null = no variable chips (profile section)
  rows: number;
};

export const PROMPT_META: PromptMeta[] = [
  {
    key: "sender_profile",
    label: "Sender Profile",
    description: "Injected as {profile} into every email template.",
    variables: null,
    rows: 14,
  },
  {
    key: "outreach_prompt",
    label: "Outreach Email",
    description: "Used for cold intro, follow-up 1 & 2, and breakup emails.",
    variables: [
      "{profile}", "{name}", "{company}", "{role}", "{detail}",
      "{tier}", "{tier_instruction}", "{template}", "{template_instruction}",
      "{dartmouth_instruction}",
    ],
    rows: 24,
  },
  {
    key: "applied_intro_prompt",
    label: "Applied Intro",
    description: "Sent after submitting an ATS application.",
    variables: [
      "{profile}", "{name}", "{role}", "{company}", "{job_title}",
      "{job_description}", "{applied_date}", "{dartmouth_instruction}",
    ],
    rows: 36,
  },
  {
    key: "applied_followup_prompt",
    label: "Applied Follow-up",
    description: "Brief follow-up sent 5 days after the intro.",
    variables: [
      "{profile}", "{name}", "{role}", "{company}", "{job_title}",
      "{dartmouth_instruction}",
    ],
    rows: 20,
  },
  {
    key: "subject_prompt",
    label: "Subject Line",
    description: "Called once per first-touch email to generate the subject.",
    variables: ["{name}", "{company}", "{mode}", "{job_title}", "{body}"],
    rows: 18,
  },
];
