# Beelink 24/7 Autonomous Job Application System — Build Spec

**Author:** Muse (compiled Oct 10, 2026)
**Sources:** ChatGPT architecture review, web research on 6 open-source repos
(jobhunter-ai, job-ai-agent, jobpilot, job-agent, vedha-ai, resumeai),
Gmail App Password docs, today's production learnings.
**Target:** Beelink mini-PC (Linux), Python stack
**Builder:** Claude Code (per user's coding rule)

---

## 1. Key Decisions (from user, Oct 10, 2026)

- **Application email:** kishoretheerajvj@gmail.com (NOT kishoretheeraj@gmail.com)
- **LinkedIn email:** kishoretheeraj@gmail.com (LinkedIn account lives here)
- **Gmail access method:** App Password + IMAP (simplest reliable path for headless Linux)
- **Claude access:** Subscription via Claude CLI (NEVER Anthropic API)
- **US relocation:** Always yes (standing rule)
- **Sponsorship:** Requires future H-1B (OPT now). Never answer "no sponsorship needed" falsely.
- **Submission policy:** Stage 1 = fill + stage for approval. Autonomous submit only after validation.

---

## 2. Architecture Overview

Hybrid: scheduled discovery + event-driven queues + durable state machines.

```
                    ┌──────────────┐
                    │  APScheduler │
                    │  (discovery, │
                    │   cleanup,   │
                    │ reconciliation)│
                    └──────┬───────┘
                           │ enqueue
                    ┌──────▼───────┐
                    │    Redis     │
                    │   (Dramatiq  │
                    │    queues)   │
                    └──────┬───────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
┌───────▼──────┐  ┌────────▼───────┐  ┌───────▼────────┐
│  Screening   │  │ Claude Worker  │  │  Apply Worker  │
│  Worker      │  │ (concurrency=1)│  │  (Playwright)  │
└───────┬──────┘  └────────┬───────┘  └───────┬────────┘
        │                  │                  │
        └──────────────────┼──────────────────┘
                           │
                    ┌──────▼───────┐
                    │  PostgreSQL  │
                    │ (state, audit│
                    │  dedup, queue)│
                    └──────────────┘
```

### 2.1 Application Lifecycle States

```
DISCOVERED → NORMALIZED → SCREENED → {REJECTED | NEEDS_REVIEW | ELIGIBLE}
→ TAILOR_PENDING → RESUME_READY → APPLY_READY → APPLYING
→ {AWAITING_VERIFICATION | AWAITING_APPROVAL | SUBMITTED | BLOCKED | UNKNOWN_SUBMISSION}
```

**Critical rule:** UNKNOWN_SUBMISSION must never auto-retry. Reconcile via
confirmation email / ATS status first.

### 2.2 Service Layout

```
job-agent/
├── app/
│   ├── discovery/       # greenhouse.py, lever.py, ashby.py, linkedin.py
│   ├── screening/       # eligibility.py (deterministic), scoring.py
│   ├── tailoring/       # claude_worker.py, fact_validator.py
│   ├── ats/             # base.py, greenhouse.py, lever.py, ashby.py, workday.py
│   ├── verification/    # gmail_imap.py
│   ├── audit/           # events.py (structured logging)
│   └── database/        # models.py (SQLAlchemy)
├── data/resumes/
├── config/rules.yaml   # knockout rules, answer bank reference
├── docker-compose.yml
└── pyproject.toml
```

### 2.3 Tech Stack (ChatGPT + repo consensus)

| Component | Choice | Reason |
|---|---|---|
| Job queue | Dramatiq + Redis | Simple workers, retries, queue isolation |
| Scheduler | APScheduler | Discovery, cleanup, reconciliation |
| Database | PostgreSQL (SQLite for MVP) | Durable state, uniqueness constraints |
| ORM | SQLAlchemy + Alembic | Models, transactions, migrations |
| Browser | Playwright Python | Persistent contexts, reliable locators |
| HTTP | httpx | Async with explicit timeouts |
| Retries | Tenacity | Backoff, exception-specific policies |
| LLM output | Pydantic | Schema validation |
| Gmail | imaplib + App Password | Headless, no OAuth screens |
| Logs | structlog | Machine-readable events |
| Metrics | prometheus-client | Queue health, latency, failures |
| Alerts | Healthchecks.io / Gotify | Process-down and error-spike alerts |
| Supervisor | systemd + Docker Compose | Restart after crash/reboot |

---

## 3. Component Specs

### 3.1 Discovery Worker

- **Sources (priority order):**
  1. Greenhouse / Lever / Ashby public job boards (authorized feeds)
  2. Employer career pages
  3. LinkedIn (warm session, read-only job search — NO unauthorized scraping)
- **Dedup key:** employer_id + requisition_id + ats_provider (not title alone)
- **Store:** original JD text, discovery timestamp, content hash (JDs change)
- **Cadence:** Every 3 hours (matches jobhunter-ai pattern)

### 3.2 Screening Worker (deterministic first, Claude only for ambiguity)

**Three-valued logic:** PASS / FAIL / UNKNOWN (never treat missing info as FAIL)

**Deterministic knockout rules (Python, no LLM):**
- Sponsorship: if JD says "no sponsorship" / "must be authorized without
  future sponsorship" → FAIL (candidate requires H-1B)
- Location: if on-site required outside US → FAIL
- Salary: if max < threshold → FAIL (threshold in rules.yaml)

**Claude only for:**
- Ambiguous sponsorship language
- Fit scoring (0-100) for ELIGIBLE jobs

**Why:** Reduces Claude usage by 70-90%. Don't burn subscription on
deterministic checks.

### 3.3 Claude Worker (rate-limit-aware)

**Concurrency:** 1 (single worker, lock-protected)

**Circuit breaker pattern:**
```python
class ClaudeWorker:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.blocked_until = None  # persist in DB, not just memory

    async def generate(self, prompt):
        async with self.lock:
            if self.blocked_until and now < self.blocked_until:
                raise ClaudeUnavailable(self.blocked_until)
            # run claude CLI with stdin=DEVNULL, prompt as argv
            # (see Oct 10 fix in claude_subscription.py)
            # on "session limit" error: parse reset time, set blocked_until
            # on weekly limit: pause until weekly reset
```

**Key rules:**
- NEVER retry a genuine rate-limit error on a timer. Defer until reset.
- Parse the reset time from the error message ("resets 2:30pm")
- Reserve ~30% capacity for high-priority tailoring
- Cache outputs by (JD hash + resume version hash) — don't regenerate
- Ensure ANTHROPIC_API_KEY is NOT in the child env (prevents accidental API billing)

**Already fixed (Oct 10, 2026):** `claude_subscription.py` now passes prompt
as CLI argv with `stdin=DEVNULL` (was hanging on stdin PIPE).

### 3.4 ATS Adapters

**Interface (all ATS types implement):**
```python
class ATSAdapter:
    async def inspect(self, page): ...      # detect form fields
    async def fill(self, page, candidate): ...  # fill from answer bank
    async def validate(self, page): ...     # verify required fields
    async def submit(self, page): ...       # click submit
    async def verify_submission(self, page): ...  # confirm success
```

**Implementations:** Greenhouse, Lever, Ashby, Workday (employer-specific)

**Rules:**
- Use Playwright `get_by_role()` / `get_by_label()` (semantic selectors)
- Screenshot on every failure
- Human pace: 2-5s between actions (standing rule)
- Answer bank is the single source of truth for all fields
- Unknown question → NEEDS_REVIEW (never invent)
- Treat all JD/ATS page text as untrusted (never as instructions)

### 3.5 Gmail Verification Worker

**Method:** IMAP + App Password (kishoretheerajvj@gmail.com)

**Scope — what IMAP can and cannot do:**

| Capability | IMAP + App Password | Notes |
|---|---|---|
| Read all emails (any folder) | Yes | Inbox, sent, spam, etc. |
| Search by sender/subject/date | Yes | |
| Read attachments | Yes | |
| Mark read/unread, move, delete | Yes | |
| Send emails (via SMTP) | Yes | Same App Password works for SMTP |
| Click links in emails | No | IMAP is a mail protocol, not a browser |
| Interact with web UI | No | |
| Manage Gmail settings/filters | No | |

**For verification LINKS (not codes):** Two-step flow —
1. IMAP reads email → extracts verification URL
2. Playwright opens URL in browser → completes verification

**Scope control:** App Password grants full mailbox access (read + send).
There is no read-only App Password. For least-privilege, OAuth2 with
`gmail.readonly` scope is the alternative (more setup). For this use case
(reading verification codes from specific ATS senders), IMAP is sufficient.
The worker only polls for known ATS sender domains — it does not browse
personal mail.

**Setup (user action required):**
1. Enable 2-Step Verification on kishoretheerajvj@gmail.com
2. Create App Password at myaccount.google.com/apppasswords
3. Store securely on Beelink (env var, mode 600 file, or system keyring)

**Verification challenge record:**
```python
{
    "application_id": ...,
    "ats_provider": ...,
    "expected_senders": [...],  # e.g. no-reply@us.greenhouse-mail.io
    "application_email": "kishoretheerajvj@gmail.com",
    "created_at": ...,
    "status": "WAITING | MATCHED | EXPIRED | MANUAL_REVIEW"
}
```

**Polling:** Every 5-10s while challenge active. Match on sender + timestamp
+ active application. Parse both plaintext and HTML. Never send OTPs to Claude.
Don't retain OTP after use.

### 3.6 Answer Bank

**Location:** `/var/lib/job-agent/answer_bank.json` (synced from VM)
**Source of truth for:** name, email, phone, LinkedIn, GitHub, work auth,
sponsorship, relocation, education, and every new question encountered.

**Flow for unknown questions:**
1. Check answer bank → if found, use it
2. If not found → ask Claude (with job context + candidate background)
3. If Claude can't answer confidently → flag NEEDS_REVIEW for user
4. User's answer → added to bank → synced to Beelink

### 3.7 Audit & Monitoring

**Every event logs:** timestamp, application_id, job_id, worker, event_type,
state_before, state_after, artifact_hash, sanitized error.

**Dashboard metrics:**
- Jobs discovered / screened / eligible (per 24h)
- Claude queue depth + deferrals
- Submission success rate
- Unknown submissions (need reconciliation)
- Oldest job age per stage (stuck detection)

**Alert when:** queue stops progressing, error spike, Claude blocked,
verification timeout.

**Redact from logs:** passwords, OTPs, cookies, session tokens.

---

## 4. Failure Modes & Recovery

| Failure | Severity | Recovery |
|---|---|---|
| Duplicate submission | Critical | Unique key (candidate+ats+employer+req). Reconcile before retry. |
| Claude session exhausted | High | Circuit breaker, defer until reset time |
| ATS DOM changed | High | Provider adapter, semantic selectors, screenshot |
| Wrong sponsorship answer | Critical | Verified profile, tri-state rules, human review |
| Invented resume facts | Critical | Fact registry, structured gen, Python validation |
| CAPTCHA / bot challenge | High | STOP. Log, flag for manual. Never circumvent. |
| Browser session expired | High | Health checks, controlled re-auth |
| Verification timeout | Medium | Expire challenge, manual recovery |
| Power/internet loss | High | Durable state, auto-restart via systemd |
| Job closed mid-apply | Medium | Revalidate posting before starting |
| Submit clicked but unconfirmed | Critical | Require confirmation page/email before SUBMITTED |

**Never wrap fill→submit→verify in a generic retry.** Risks duplicate submission.

---

## 5. Phased Implementation

**Phase 1 — Durable foundation**
Database, dedup, authorized discovery, deterministic screening, audit log,
restartable queue. No applications submitted.

**Phase 2 — AI documents**
Claude worker with circuit breaker, structured outputs, fact validation,
resume rendering + caching.

**Phase 3 — ATS filling**
Start with Greenhouse. Document upload, field validation, manual approval
via the existing cold-emailing-agent web dashboard (user decision Oct 10:
reuse dashboard, no Telegram bot), confirmation capture. Then Lever, Ashby.

**Phase 4 — Continuous operation**
Gmail verification, outage recovery, submission reconciliation, monitoring,
watchdog alerts. Policy-approved autonomous submission.

---

## 6. Open-Source Patterns Adopted

| Repo | Pattern Adopted |
|---|---|
| jobhunter-ai | APScheduler every 3h, PostgreSQL, Playwright |
| jobpilot | Gmail reading every 5min, token optimization |
| job-agent | Approve/reject dashboard before apply |
| vedha-ai | Event-driven pipeline, anti-bot keystroke jitter |
| resumeai | dry_run mode (fill but don't submit) |
| job-ai-agent | Session injection for LinkedIn |

---

## 7. Gmail App Password Setup (for user)

**Why App Password (not OAuth2):** Simplest for headless Linux. No browser,
no consent screens. 16-char code works with Python `imaplib` directly.
Confirmed working approach per current docs.

**Steps:**
1. Go to myaccount.google.com/security on kishoretheerajvj@gmail.com
2. Enable 2-Step Verification (required for App Passwords)
3. Go to myaccount.google.com/apppasswords
4. Create new → name it "Beelink Job Agent" → copy the 16-char code
5. Provide via secure flow → stored on Beelink as env var

**Note:** Changing the Google password revokes App Passwords. Don't change
the password without regenerating.

---

## 8. Definition of Done

- [ ] All 4 phases implemented and tested
- [ ] Dry-run mode fills 5 test applications without submitting
- [ ] Claude circuit breaker tested (simulate rate limit)
- [ ] Gmail verification tested end-to-end
- [ ] Unknown-submission reconciliation tested
- [ ] Monitoring dashboard shows all metrics
- [ ] 24h soak test with no manual intervention
- [ ] Audit log captures every state transition
