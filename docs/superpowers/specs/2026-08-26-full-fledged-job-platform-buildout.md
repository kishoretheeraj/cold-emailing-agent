# Full-Fledged Job Platform Buildout — Spec

**Status:** Phase 1 in detail, Phases 2-5 stubbed (design them when their turn comes — Phase 1's
schema decisions will change what Phase 2+ needs).

**Origin:** OSS landscape scan (2026-08-26) of ~50 cold-email/AI-SDR and job-search/tracking repos,
mapped against this repo's current capabilities. Full findings: the "Pipeline Gaps" artifact from
that session (not checked into the repo — this spec is the durable record).

## Vision

cold-email-agent today is the *outreach* half of a job search: find a contact, personalize an
email, detect replies. It has no concept of a job *application* — applying to a specific posting,
tracking it through interview stages, scoring fit against a resume. This spec closes that gap
without touching the outreach machinery that already works.

## Non-goals (explicit, not silently dropped)

- **Multi-channel outreach** (LinkedIn/WhatsApp/Telegram). Common in the OSS SDR-template space
  (growchief, DeskcommCRM) but adds ToS risk and complexity with no benefit for one person's job
  search.
- **Multi-user CRM** (Twenty, EspoCRM-style team seats, custom objects). This system is single-user
  by design; a pipeline entity is not the same ask as a team CRM.
- **Email warmup/deliverability tooling** (warmbly, kindling). Only relevant if send volume grows
  enough to strain sender reputation — not a problem today. Revisit if volume changes.

## Governance invariant (same posture as every other signal in this repo)

Every new signal here — job fit score, application stage, JobRight-sourced posting — degrades to
"unknown"/absent, never to a false negative. Same rule as the H-1B gate, Form D funding signal, and
decision-context tagging: absence of data is not-observed, not "no."

**Human-gated everything.** Multiple OSS projects independently converged on "agent drafts,
human submits" for job applications (JobCtrl's "approval-gated applications," AutoApply,
dear-hiring-manager) — the same rule this repo already enforces for email
(`Don't add an SMTP/send path` in the root CLAUDE.md). Any future auto-apply work inherits that
rule without exception: draft the application, never submit it.

---

## Phase 1 — Job application tracking (detailed)

**Plan:** `docs/superpowers/plans/2026-08-26-job-application-tracking.md`

New `job_applications` table with its own stage enum (`saved → applied → phone_screen → onsite →
offer/rejected/withdrawn/accepted`), independent of `contacts.stage` (which tracks the outreach
relationship only — see the root CLAUDE.md's four-mirrored-stage-set warning; this table
deliberately never touches that web). `contact_id` is nullable (an application can exist with no
known contact) and `INTEGER` to match `contacts.id`'s actual Postgres type. Backend: `db.py`
accessors + tests. Frontend: a new `/applications` page in contact-manager with a table view and
inline stage control, following existing page/API-route/test conventions exactly.

## Phase 2 — Job & company discovery (stub)

`ats.py` today only answers "is this company hiring in this function" as a boolean; it never stores
individual postings. Extend it (or add a sibling module) to persist postings into `job_applications`
at `stage='saved'` — either via deeper Greenhouse/Ashby/Lever parsing (structured data already
available from those APIs) or a JobSpy-style aggregator pull (LinkedIn/Indeed/Glassdoor). Design this
phase's schema needs against whatever Phase 1 actually shipped, not against this stub.

**JobRight puller (see below) feeds this phase** as one additional, manual-only source.

## Phase 3 — Resume intelligence (stub)

Fit-score a resume against a specific `job_applications` row (approach: Resume-Matcher-style
embedding/keyword hybrid) and optionally draft a tailored cover letter, gated by the same
human-review step as email drafts. Requires deciding where a resume lives in the schema — a new
table vs. a `prompts`-style row, structured fields vs. a blob — as part of *this* phase's design,
not preemptively now.

## Phase 4 — Interview & offer tracking (stub)

Extend `job_applications` past `applied` with interview-prep notes and offer/negotiation fields.
Only meaningful once Phase 1's pipeline actually has applications flowing through it.

## Phase 5 — Email verification pre-flight (opportunistic, low priority)

A bounce-risk check (email-sleuth-style) before the first-touch draft, additive to `preflight.py`'s
existing checks or a new pre-draft gate. Small, can be done any time after Phase 1.

---

## JobRight puller (manual only — not in cron)

**Decision record:** JobRight.ai has no public API (confirmed via SaaSWorthy: "Does Jobright provide
API? No") and its `robots.txt` disallows `/api/*`. The only working integration path is an
unofficial session-cookie scraper that logs in with the user's own credentials and hits JobRight's
internal `/swan/...` endpoints. The user was shown this risk explicitly (account-flag risk, ToS
violation, fragility on any JobRight frontend change) and chose to proceed anyway with their own
paid account — an informed call about their own account, not one this spec re-litigates.

**What keeps this from becoming a production incident:**

- **Manual only, never in cron.** Same shape as `extract_voice.py` ("manual, not in cron"). No
  workflow file references it, no GitHub Actions secret is added for it. It is a script the user
  runs locally when they want a fresh pull.
- **Credentials never touch disk in code.** `JOBRIGHT_EMAIL` / `JOBRIGHT_PASSWORD` read from
  `os.environ` only, exactly like `GMAIL_APP_PASSWORD`. Never written to a file, never logged,
  never passed to `db.py` or any table.
- **Best-effort, never raises.** Same posture as `ats.py`: a JobRight failure (login failure, schema
  drift, rate limit) returns `[]` and logs a warning. It must never block or fail anything else.
  Nothing in the daily agent or monitor path calls it.
- **Respect the reference implementation's etiquette.** Keep inter-page delay and retry backoff
  (the third-party `jobright.py` scraper this pattern is drawn from already implements both) —
  that's what separates "unsupported personal use of my own account" from hammering their API.
- **Output feeds Phase 2's `job_applications` ingestion**, tagged `source='jobright'` in
  `posting_snapshot`, so a future review can always tell which postings came from an unofficial
  path versus a public board scrape.

Design and build this as part of Phase 2, not Phase 1 — Phase 1 has no postings table yet for it to
write into.

---

## Auto-continue infrastructure (cross-cutting, build alongside Phase 1)

**Problem:** the user's interactive Claude session is rate-limited on a rolling 5-hour window. A
multi-phase build will outlast single sessions repeatedly.

**Rejected approach:** a scheduled Claude Code cloud routine (`schedule` skill / `RemoteTrigger`).
Checked via the `schedule` skill — it does not establish that cloud routines draw from separate
quota than the interactive subscription window. If they share quota, an hourly routine burns the
same budget it's waiting to recover, making the stall worse, not better.

**Chosen approach:** a GitHub Actions workflow, `build-continue.yml`, on an hourly cron
(`0 * * * *` UTC — GHA's minimum useful granularity for this). Billed via `ANTHROPIC_API_KEY`
(already a repo secret, used identically by `daily_agent.yml` / `monitor.yml` /
`visa_intel_ingest.yml`), which is provably independent of the interactive session's subscription
window — same pattern this repo already trusts three times over.

**What it does each run:**

1. Checks out `main`.
2. Reads the current phase's plan file under `docs/superpowers/plans/` for the first unchecked
   `- [ ]` task.
3. If every task in the current phase's plan is checked, and the spec above lists a next phase:
   invokes Claude Code headless to *write* that phase's detailed plan (using the `writing-plans`
   skill's structure) before starting it. This is why Phases 2-5 are stubs now, not full plans —
   the stub is exactly the brief a cold session needs to write the real plan when its turn comes.
4. Otherwise, invokes Claude Code headless (`claude -p "..."`, non-interactive) with a prompt that
   points at the next unchecked task and instructs it to follow
   `superpowers:executing-plans` conventions: implement the task, run its tests, and only commit
   when green — the repo's Definition of Done (tests pass, CLAUDE.md updated, memory updated)
   applies to every commit this workflow makes, exactly as it would to a commit made interactively.
5. Commits with `Co-Authored-By: Claude <noreply@anthropic.com>` and pushes to `main` directly (no
   PR review loop — this is the same trust level the user already grants interactive sessions per
   their global CLAUDE.md: "run tests, commit, and push without being asked").
6. If a run finds all five phases' plans fully checked off, it disables itself
   (`gh workflow disable`) and stops firing.

**The plan files are the progress doc.** No separate tracking file — each plan's `- [ ]` /
`- [x]` checkboxes are the machine-readable state a cold session reads to know exactly what's
next, and this spec's phase list is the map of which plan file is current. This reuses the
`writing-plans` skill's existing design instead of inventing a parallel state file.

**Failure mode:** same as the three existing workflows — uploads its log as an artifact, runs
`notify_failure.py` on `if: failure()`. A failed run does not advance the plan; the next hourly
fire retries the same unchecked task.
