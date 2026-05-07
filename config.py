import os
from dotenv import load_dotenv

load_dotenv()

# ── Secrets ────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
GMAIL_ADDRESS      = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
SUPABASE_URL       = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY  = os.environ["SUPABASE_ANON_KEY"]

# ── Models ─────────────────────────────────────────────────────────────────────
EMAIL_MODEL = "claude-sonnet-4-6"

# These are fallback defaults. Live prompts (including sender_profile) are stored
# in the Supabase prompts table and override these at runtime.

# ── Sender profile ─────────────────────────────────────────────────────────────
SENDER_PROFILE = """
Name: Kishore
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
Sign-off: Kishore / MEM '26 | Dartmouth
""".strip()

# ── Dartmouth keywords ─────────────────────────────────────────────────────────
DARTMOUTH_KEYWORDS = ["dartmouth", "tuck", "thayer", "irving", "big green"]

# ── Follow-up delays (days) ────────────────────────────────────────────────────
FOLLOWUP_DAYS = {
    "send_first_touch":       5,
    "send_followup1":         4,
    "send_followup2":         3,
    "send_breakup":           None,
    "send_applied_intro":     5,
    "send_applied_followup":  None,
}

# ── Tier instructions ──────────────────────────────────────────────────────────
TIER_INSTRUCTIONS = {
    1: "DREAM company. Deep personalization. Reference specific company news, "
       "products, or the contact's background. Show you've done real research.",
    2: "STRONG FIT. Moderate personalization. Reference the company and why "
       "you're interested, but don't overdo it.",
    3: "WORTH A SHOT. Keep it efficient. Brief, professional, to the point.",
}

# ── Template instructions (Mode A) ────────────────────────────────────────────
TEMPLATE_INSTRUCTIONS = {
    "cold_intro":   "First-touch cold email. End with a soft ask for a 15-min chat.",
    "follow_up_1":  "Gentle follow-up (day 5). Reference something new — a recent "
                    "company announcement, article, or product launch. Not pushy.",
    "follow_up_2":  "Second follow-up (day 10). Shorter, mild urgency. Mention "
                    "you'll stop reaching out if now isn't the right time.",
    "breakup":      "Final email. 2-3 sentences max. Respectful close. Leave door open.",
}

# ── Dartmouth injection ────────────────────────────────────────────────────────
DARTMOUTH_INSTRUCTION = (
    "ALUMNI CONNECTION DETECTED: Treat this as warm alumni outreach, not cold. "
    "Reference the shared Dartmouth/Tuck/Thayer connection naturally and early. "
    "Use a peer-to-peer tone, not a stranger-to-stranger tone. Soften the ask."
)

# ── Prompt templates (fallbacks — Supabase values take precedence) ────────────

# ── Mode A prompt ──────────────────────────────────────────────────────────────
OUTREACH_PROMPT = """You are writing a cold outreach email for a job seeker.
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
- Sound human, not AI-generated"""

# ── Mode B prompt — applied_intro ─────────────────────────────────────────────
APPLIED_INTRO_PROMPT = """You are writing an email from a job applicant to a
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
- Sound like a confident person, not a cover letter"""

# ── Mode B prompt — applied_followup ─────────────────────────────────────────
APPLIED_FOLLOWUP_PROMPT = """You are writing a brief follow-up email from a job
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
- Do NOT include subject line or name sign-off"""

# ── Subject line prompt ────────────────────────────────────────────────────────
SUBJECT_PROMPT = """Generate a short email subject line.

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
- Return ONLY the subject line, nothing else"""
