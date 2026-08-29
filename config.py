import os
from dotenv import load_dotenv

load_dotenv()

# ── Secrets ────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
GMAIL_ADDRESS      = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
SUPABASE_URL       = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY  = os.environ["SUPABASE_ANON_KEY"]

# Optional — needed for Gmail API send path (/api/send-draft) and
# draft ID lookup in gmail.py. Absent in CI until secrets are added.
GOOGLE_OAUTH_CLIENT_ID     = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
GOOGLE_OAUTH_REFRESH_TOKEN = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN")

# ── Models ─────────────────────────────────────────────────────────────────────
EMAIL_MODEL = "claude-sonnet-4-6"
REPLY_CLASSIFICATION_MODEL = "claude-haiku-4-5-20251001"
REPLY_RESPONSE_MODEL = "claude-sonnet-4-6"

# ── Research (Tavily) ──────────────────────────────────────────────────────────
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
# No raise if missing. Research is an enhancement. If the key is absent,
# the agent logs a warning and generates emails without a brief.

RESEARCH_CACHE_TTL_DAYS = 7
RESEARCH_MAX_QUERIES = 5
RESEARCH_MAX_QUERY_LEN = 80
RESEARCH_TAVILY_RESULTS_PER_QUERY = 5
RESEARCH_HARDCODED_FALLBACK_QUERY = "{company} news 2026"
RESEARCH_TIERS = {1, 2}

RESEARCH_QUERY_MODEL = "claude-haiku-4-5-20251001"
RESEARCH_CURATE_MODEL = "claude-sonnet-4-6"
RESEARCH_CURATE_MAX_TOKENS = 600

# ── ATS career-page enrichment ─────────────────────────────────────────────────

ATS_ENABLED = True
ATS_MAX_JOBS = 3
ATS_MAX_DESCRIPTION_CHARS = 1500
# Short on purpose. This runs inside a per-contact loop in a cron job, so a hung
# careers API must never stall a run.
ATS_TIMEOUT_SECONDS = 8
ATS_MAX_SLUG_CANDIDATES = 2
# Discovery (job_discovery.py) wants every currently-open posting for a company, not
# just the single best match for one contact's role — this cap is deliberately higher
# than ATS_MAX_JOBS (which sizes a research-brief snippet, not a discovery scan).
ATS_DISCOVERY_MAX_JOBS = 25

# ── JobRight puller (Phase 2, full-fledged buildout) ────────────────────────────

# Optional. Absent means jobright.py no-ops (fetch_recommended_jobs returns []) -- never a
# hard-required config.py import-time lookup like the five core secrets above, since every
# other script (agent.py, monitor.py, the whole test suite) must keep working without these set.
JOBRIGHT_EMAIL = os.environ.get("JOBRIGHT_EMAIL")
JOBRIGHT_PASSWORD = os.environ.get("JOBRIGHT_PASSWORD")
JOBRIGHT_TIMEOUT_SECONDS = 15
JOBRIGHT_MAX_ATTEMPTS = 3
JOBRIGHT_RETRY_BACKOFF_SECONDS = 2
JOBRIGHT_PAGE_SIZE = 20
JOBRIGHT_MAX_JOBS = 60
JOBRIGHT_PAGE_DELAY_SECONDS = 2

# recommend/list/jobs sortCondition -- confirmed via live reconnaissance 2026-08-29:
# 0 = Recommended (JobRight's blended default, matches account's saved filter weighting),
# 1 = Most Recent (posting date), 2 = Top Matched (match score). Any other value silently
# falls back to 0 server-side. Recommended stays the default -- it's what the account's own
# UI defaults to and reflects the saved filter's full weighting, not just recency or score.
JOBRIGHT_SORT_CONDITION = 0

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
    "send_first_touch":          5,
    "send_followup1":            4,
    "send_followup2":            3,
    "send_breakup":              None,
    "send_applied_intro":        5,
    "send_applied_followup":     None,
    "send_networking_first_touch": 6,
    "send_networking_followup":    None,
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

# ── Mode C prompt — networking first touch ────────────────────────────────────
NETWORKING_PROMPT = """You are writing a warm networking email for a job seeker.
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
- Sound human, not AI-generated"""

# ── Mode C prompt — networking follow-up (single nudge) ──────────────────────
NETWORKING_FOLLOWUP_PROMPT = """You are writing a brief, gentle follow-up to a
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

# ── Networking subject line prompt ─────────────────────────────────────────────
NETWORKING_SUBJECT_PROMPT = """Generate a short email subject line for a warm
networking email (not a job pitch).

To: {name} at {company}
Email body:
{body}

RULES:
- Max 8 words
- Casual, personal feel — never mention role, application, or hiring
- Lowercase preferred
- No clickbait
- Return ONLY the subject line, nothing else"""

# ── Critic loop ────────────────────────────────────────────────────────────────
CRITIC_PROMPT_DEFAULT = """You are a strict editor reviewing
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
               instruction>"}}"""

# Drafts scoring >= this pass without retry.
# Drafts scoring < this trigger one regeneration with feedback.
CRITIC_PASS_THRESHOLD = 6

# Seconds to sleep between body generation and subject/critic calls within a single
# contact, to avoid exhausting the 30k input-tokens-per-minute rate limit.
INTER_CALL_SLEEP = 10
BATCH_POLL_INTERVAL = 30  # seconds between batch status polls

# ── Research prompt defaults ───────────────────────────────────────────────────
RESEARCH_QUERY_DEFAULT = """You generate web search queries to help a job seeker write a personalized cold outreach email.

THE SENDER (writing the email):
{sender_profile}

THE CONTACT (recipient):
- Name: {name}
- Company: {company}
- Role: {role}
- Detail (what the sender already knows): {detail}
- Notes: {notes}
- Dartmouth alumni: {dartmouth}
- Tier: {tier}

Your job: generate 1 to 5 web search queries that would surface recent, specific, professional context about this contact.

PRIORITY ORDER (most important to least):
1. About the PERSON specifically (recent talks, articles, interviews, podcast appearances, role changes, news quotes). The person is the priority. Use the contact name combined with the company name to disambiguate from people with similar names.
2. About the COMPANY where the person works (recent funding, product launches, hiring, strategic shifts, news coverage).
3. About a SHARED CONTEXT (only if dartmouth is true: look for the person's Dartmouth or Tuck connection).

ABOUT QUERIES:
- Every person-targeted query MUST include the company name to avoid matching unrelated people with the same name. Example: 'John Smith Palm Desert Networks podcast' NOT just 'John Smith podcast'.
- Each query should target a different angle. Do NOT generate 5 near-duplicate queries.
- Prefer recent context: include '2026' or '2025' in queries where freshness matters.
- Do NOT include 'LinkedIn', 'twitter', or 'X.com' in queries. Those platforms block search engines and rarely return useful recent activity. Instead, look for podcast appearances, panel talks, articles, interviews, press quotes, or company news that may mention the person.
- Do NOT search for personal life, family, hobbies, or opinions on non-professional topics. Professional context only.
- If the contact has thin info (only name and company, no detail or notes), 2 queries is enough. If detail/notes/dartmouth are rich, up to 5 queries are warranted.

ABOUT LENGTH: each query MUST be 80 characters or fewer.

Return ONLY a JSON array of strings, nothing else. No markdown, no fences, no preamble.

Example output for a contact with detail field 'Built fintech compliance dashboards for 12 banks':
["John Smith Palm Desert Networks interview 2026",
 "John Smith Palm Desert Networks fintech compliance",
 "Palm Desert Networks news 2026",
 "Palm Desert Networks compliance dashboards banks"]

If the contact has insufficient info to generate ANY useful queries, return an empty array: []"""

RESEARCH_CURATE_DEFAULT = """You synthesize web search results into a brief, disambiguated context note that a job seeker can use to personalize a cold email.

CRITICAL DISAMBIGUATION RULE:
Some search results may be about a DIFFERENT person who happens to share the contact's name. Before including ANY fact, verify it is clearly about THIS contact, identified by:
  - The contact's name AND
  - Their actual current company AND
  - A role consistent with what we know about them
If a result is ambiguous (could be about this person or someone else), DO NOT include it. Better to return an empty brief than to include a fact about the wrong person.

THE CONTACT (authoritative facts; results that contradict these are about a different person):
- Name: {name}
- Company: {company}
- Role: {role}
- What the sender already knows: {detail}

RAW SEARCH RESULTS:
{raw_results}

YOUR OUTPUT FORMAT:

Return a short markdown-formatted brief, structured as:

Person:
- <fact 1 about the contact, 1 sentence, with source domain in parens>
- <fact 2, if available>

Company:
- <fact 1 about the company, 1 sentence, with source domain in parens>
- <fact 2, if available>

Omit a section entirely if no facts of that type passed the disambiguation rule. Maximum 4 bullets total across both sections. Each bullet under 25 words.

Better generic than wrong. If results are ambiguous, off-topic, or about a different person, return exactly the string:
NO_RELIABLE_BRIEF

Do not include 'Note:' caveats. Do not editorialize. Just facts in bullets, or NO_RELIABLE_BRIEF."""

REPLY_CLASSIFICATION_DEFAULT = """Classify this email reply. Return a single JSON object with one key "classifier_status" and one of these values:
- positive_reply: genuine interest, wants to talk, asks questions, or is clearly engaged
- soft_yes: mild positive signal, open to connecting but non-committal
- hard_no: any negative reply — explicit rejection, disinterest, no opportunity available, can't help, doesn't have a role/internship/position, or politely declining
- auto_reply: out-of-office, vacation auto-reply, or bounce notification
- out_of_office: person is away but no explicit rejection
- unrelated: reply is clearly not about the original outreach at all (wrong thread, spam, phishing, etc.) — do NOT use this for negative replies

Reply to classify:
{reply_body}

Return ONLY the JSON object, nothing else. Example: {{"classifier_status": "positive_reply"}}"""

REPLY_RESPONSE_DEFAULT = """Write a brief, warm reply to this email. You are Kishore responding to someone who expressed interest in connecting.

SENDER PROFILE:
{profile}

CONTACT:
- Name: {name}
- Company: {company}
- Role: {role}

THEIR REPLY:
{reply_body}

RULES:
- Max 80 words
- Acknowledge what they said specifically
- Suggest a concrete next step (short call, specific time, calendly link placeholder)
- No em dashes
- No filler phrases
- Sound like a real person, not a template
- Do NOT include subject line or name sign-off"""

RESEARCH_INJECTION_DEFAULT = """

RECENT WEB CONTEXT for the contact (auxiliary; the contact record above is authoritative):

{brief_text}

RULES FOR USING THIS CONTEXT:
- Use a fact from the brief ONLY if it is specific, recent, and clearly relevant to your outreach purpose. Verifiable details like a named product, a specific event, a recent role change, a funding round, a quote.
- If the brief is generic or thin, IGNORE it. Use the contact's detail field instead.
- If the brief contradicts the contact record above (different role, different company, conflicting facts), DISCARD the brief entirely. Use the contact record.
- Do NOT invent facts. Do NOT embellish. If you would have to stretch to use the brief, do not use it.
- Better generic than wrong. A safe email with a soft hook beats a confident email with a fabricated detail.
"""

# ── Resume intelligence (Phase 3, full-fledged buildout) ────────────────────────

RESUME_STORAGE_BUCKET = "resumes"
RESUME_MODEL = EMAIL_MODEL
RESUME_SOFFICE_TIMEOUT_SECONDS = 30
RESUME_COVER_LETTER_MAX_WORDS = 300
# preflight.py's own pattern: one automatic regeneration on a lint failure, then give up loudly.
RESUME_MAX_BUILD_RETRIES = 1
# Claude Sonnet 4.6 pricing per platform.claude.com/docs/en/about-claude/pricing, checked 2026-08-29.
# Update these if RESUME_MODEL changes or Anthropic's pricing changes.
RESUME_MODEL_COST_PER_MTOK_INPUT = 3.0
RESUME_MODEL_COST_PER_MTOK_OUTPUT = 15.0

# ── Model pricing (system-wide cost tracking) ───────────────────────────────────
# Real per-million-token USD prices, verified against platform.claude.com/docs/en/about-claude/
# pricing 2026-08-29 -- not estimated. {model: (input_price_per_mtok, output_price_per_mtok)}.
# Covers every real model string used anywhere in this repo (EMAIL_MODEL, REPLY_CLASSIFICATION_
# MODEL, REPLY_RESPONSE_MODEL, RESEARCH_QUERY_MODEL, RESEARCH_CURATE_MODEL, RESUME_MODEL all
# resolve to one of these two strings). usage_tracking.calculate_cost raises KeyError for any
# other model -- add its verified price here rather than guessing before using a new model.
MODEL_PRICING = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}
# Font extracted from the user's own real resume corpus (77 .docx files, checked 2026-08-29):
# Calibri is the dominant choice (~76/77 files); Garamond appeared in exactly one recent
# consulting-track variant. Default to the proven majority pattern, single line to override.
RESUME_FONT_NAME = "Calibri"
# The strategy step (resume_agent.py --propose) may only choose from this exact set of section
# names -- an unconstrained LLM invented labels like "Selected Projects"/"Core Competencies" that
# resume_build.py's section_order lookup silently dropped (found on the first live --build run).
# "Summary" is deliberately excluded: master.json has no summary text to render, and the most
# recent real template omits it.
RESUME_ALLOWED_SECTIONS = ("Education", "Experience", "Projects", "Skills", "Leadership")
# Real historical resumes show 2-3 bullets per role/project, not every metric.json entry a role
# has bullet_ids for -- found live: rendering all 4-5 bullets per Protium role (18+ bullets total
# across 4 roles) never fits one page even at the tightest fitting-ladder rung. Caps to the first
# N bullet_ids per entry (already curated in master.json's own order) -- deterministic, no new LLM
# selection freedom, matching the real corpus's actual density.
RESUME_MAX_BULLETS_PER_ENTRY = 3
