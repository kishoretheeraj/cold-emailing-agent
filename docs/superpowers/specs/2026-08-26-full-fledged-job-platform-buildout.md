# Full-Fledged Job Platform Buildout — Spec

**Status:** Phases 1-2 in detail (Phase 2 designed 2026-08-27, against what Phase 1 actually
shipped), Phases 2.5-5 stubbed (design them when their turn comes).

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

**Human-gated by default, with an explicit auto-apply override (recorded 2026-08-27).** Multiple
OSS projects independently converged on "agent drafts, human submits" for job applications
(JobCtrl's "approval-gated applications," AutoApply, dear-hiring-manager) — the same rule this
repo already enforces for email (`Don't add an SMTP/send path` in the root CLAUDE.md). This spec
originally inherited that rule without exception for job applications too.

The user has since explicitly overridden it for the future auto-apply agent (Phase 2.5, stubbed
below): a default **review queue** (generated resume/application shown to the user, editable,
approve-to-submit) with a separate **toggle** to submit without review, per-application or
globally. This is an informed, deliberate call about the user's own accounts and own applications
— not one this spec re-litigates — but it does NOT change the email send-path rule (`Don't add an
SMTP/send path` in the root CLAUDE.md), which stays absolute and untouched. Job-application
submission and email sending are governed separately from here on.

Phase 2 itself (this document's next section) stays entirely read-only/discovery — it writes
`job_applications` rows at `stage='saved'` and never submits anything. The auto-apply override
only becomes live code in Phase 2.5.

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

## Phase 2 — Job & company discovery (detailed)

**Plans:** `docs/superpowers/plans/2026-08-27-job-discovery.md` (ATS scan — this section, shipped
2026-08-27) and `docs/superpowers/plans/2026-08-27-jobright-puller.md` (JobRight puller — not yet
written; needs interactive reconnaissance of JobRight's actual endpoints before a TDD plan can be
written against real request/response shapes instead of guessed ones).

Read-only/discovery only — writes `job_applications` rows at `stage='saved'` and never submits
anything. Two new manual (not-in-cron by default; see JobRight scheduling below) scripts, following
`extract_voice.py`'s "manual, not in cron" posture as the default and only deviating from it where
explicitly decided:

- **`job_discovery.py`** — builds the company universe as the union of distinct `contacts.company`
  values and every `company_intel.raw_company_names` entry (both scanned; `company_intel` is
  currently 38 rows, small enough that no bounded-selection rule is needed — revisit if that corpus
  grows by an order of magnitude). Uses `raw_company_names` (the original, unnormalized display
  strings), not `normalized_name` — `ats._slug_candidates` needs a real company name to derive a
  URL slug from, not the alias-canonicalized form used for visa-gate matching. For each company,
  calls `ats.fetch_jobs(company, max_jobs=...)` — see the `ats.py` change below — reusing the
  existing Greenhouse/Ashby/Lever cascade instead of writing a new scraper. Filters results against
  the new `target_roles` prompts key, dedupes, and persists via
  `db.create_job_application(..., source='ats_scan')`.
- **`jobright.py`** — session-cookie login using `JOBRIGHT_EMAIL`/`JOBRIGHT_PASSWORD`, hits
  JobRight's internal `/swan/...` endpoints (see the JobRight puller section below for the full
  decision record), normalizes to the same posting shape, feeds the same persistence path tagged
  `source='jobright'`.

**`ats.py` change**: `fetch_jobs(company, role=None)` currently hardcodes its cap to
`config.ATS_MAX_JOBS` (3) inside `_rank_jobs`, sized for "the single best match for one known
contact's role" in the research pipeline — wrong for discovery, which wants everything currently
open. Add an optional `max_jobs` parameter to both `fetch_jobs` and `_rank_jobs` (falling back to
`config.ATS_MAX_JOBS` when omitted, so `research.py`'s existing call site is byte-identical in
behavior); `job_discovery.py` passes a new `config.ATS_DISCOVERY_MAX_JOBS` constant instead.

**Target role**: every `role` in `config.py`/`prompts` today means the *contact's* role, not the
user's. Add a `target_roles` row to the `prompts` table (newline-delimited, same convention as
`guardrail_company_list`/`forbidden_phrases`), editable live via the contact-manager's Prompts page.
`job_discovery.py` reads it via `db.load_prompts()` and filters postings whose title tokens overlap
any target role — this is a filter, not `ats.py`'s existing single-best-match ranking, since
discovery wants every reasonably-matching posting, not the top one.

**Dedup**: add a partial unique index on `job_applications.job_url` (`WHERE job_url IS NOT NULL`),
plus an accessor-level check-before-insert in `db.py` (same shape as `create_draft`'s idempotency
check) so repeated scans don't create duplicate `'saved'` rows.

**JobRight puller (see below) feeds this phase** as an additional source, tagged `source='jobright'`.

## JobRight scheduling (decided 2026-08-27 — overrides the original manual-only rule)

The JobRight puller section below originally specified "manual only, never in cron" as a hard rule
to bound ToS/account-flag risk. The user explicitly chose to override this and run it unattended:
a new scheduled workflow (`jobright_pull.yml`, proposed daily cadence — postings don't change
fast enough to need more, and a lower frequency reduces account-flag risk relative to hourly/etc.)
with `JOBRIGHT_EMAIL`/`JOBRIGHT_PASSWORD` added as GitHub Actions secrets, scoped to that workflow
only. `jobright.py` itself keeps working as a manual/local script too — scheduling is additive, not
a replacement. Same trust level as the auto-apply override above: an informed call about the user's
own account, not re-litigated here. The reference-implementation etiquette (inter-page delay, retry
backoff — see below) matters more, not less, once this runs unattended.

**Correction (2026-08-27, live reconnaissance):** the `JOBRIGHT_EMAIL`/`JOBRIGHT_PASSWORD` design
above assumed a JobRight-native password login. Live network capture during reconnaissance
(logging out and back in while reading network traffic, with the user's participation) showed the
user's account actually authenticates via **Google Sign-In**: the browser POSTs a Google-minted
OIDC `idToken` (a JWT, not persisted anywhere in this repo or its docs) to
`POST /swan/auth/login/sso` as `{"email": ..., "idToken": "<google-oidc-jwt>", "from": "homepage"}`.
There is no password in this flow. An unattended scheduled job cannot mint a fresh Google `idToken`
without automating Google Sign-In itself — a materially different and larger risk than the
JobRight-only risk shown when the scheduling override above was made, since it would put the
user's Google account (not just JobRight) in the blast radius. **The scheduling override above is
therefore not currently buildable as designed.** Two open questions determine whether a workable
path exists (native-password fallback, or a long-lived-cookie fallback) — see
`docs/superpowers/plans/2026-08-27-jobright-puller.md` once written, or the session that resolves
this note, for the outcome. Confirmed real (from the same capture): the job-listing endpoint
`GET /swan/recommend/list/jobs?refresh=<bool>&sortCondition=<int>&position=<int>&count=<int>&syncRerank=<bool>`
returns `{success, errorCode, errorMsg, result: {jobList: [{jobResult: {jobId, jobTitle,
jobLocation, workModel, originalUrl, applyLink, isCompanySiteLink, source, salaryDesc, minSalary,
maxSalary, ...}, companyResult: {companyName, companyURL, h1bAnnualJobCount, ...}}]}}`, and the
session-check endpoint `GET /swan/auth/newinfo` returns `{result: {logined: true/false, ...}}` —
both cookie-authenticated (no bearer token in `localStorage`, consistent with an httpOnly session
cookie).

## Phase 2.5 — Auto-apply agent (future, stub — NOT part of Phase 2's plan)

Deliberately excluded from Phase 2's plan file: this needs its own spec once Phase 2 has produced
real `job_applications` inventory at `stage='saved'` to apply to, and because it introduces a
genuinely higher-consequence action (irreversible real-world submissions) that a design pass this
short doesn't fully specify. Recorded here so a cold session has the intended shape:

- **Resume generation**: given a `job_applications` row, generate a tailored resume (ties into
  Phase 3's resume intelligence — likely needs Phase 3 designed first, or folded into this phase).
- **Review queue (default)**: generated resume + application shown to the user in the
  contact-manager, editable, with explicit approve-to-submit. Matches the JobCtrl/AutoApply/
  dear-hiring-manager pattern already cited above.
- **Auto-apply toggle**: a separate, explicit opt-in (per-application or global) to skip the review
  queue and submit without a human click. This is the part that overrides the original
  human-gated-everything rule; the review queue remains the default.
- **CI-safety constraint for whichever agent builds this**: `build-continue.yml` runs unattended
  hourly, pushes straight to `main` with no PR review, and its prompt tells it to implement whatever
  task it finds next. The actual submit call must be gated behind a local-only env var (e.g.
  `JOBRIGHT_AUTOAPPLY_ARMED`) that is **never** a GitHub Actions secret — same pattern as
  `JOBRIGHT_EMAIL`/`JOBRIGHT_PASSWORD` being local-only today. This lets `build-continue.yml` build
  and test the full dry-run path autonomously while making it structurally impossible for that
  unattended agent to ever fire a real submission, even if its tests exercise the code path. Decide
  the exact mechanism as part of that phase's own design pass, not by inheriting this note verbatim.

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

## JobRight puller (manual + scheduled — see "JobRight scheduling" above)

**Decision record:** JobRight.ai has no public API (confirmed via SaaSWorthy: "Does Jobright provide
API? No") and its `robots.txt` disallows `/api/*`. The only working integration path is an
unofficial session-cookie scraper that logs in with the user's own credentials and hits JobRight's
internal `/swan/...` endpoints. The user was shown this risk explicitly (account-flag risk, ToS
violation, fragility on any JobRight frontend change) and chose to proceed anyway with their own
paid account — an informed call about their own account, not one this spec re-litigates.

**What keeps this from becoming a production incident:**

- **Manual by default, also scheduled (decided 2026-08-27 — see "JobRight scheduling" above).**
  Originally "manual only, never in cron" like `extract_voice.py`; the user explicitly chose to
  also run it via a dedicated `jobright_pull.yml` workflow at a conservative (daily) cadence,
  scoped secrets, with the manual/local path still working unchanged.
- **Credentials never touch disk in code.** `JOBRIGHT_EMAIL` / `JOBRIGHT_PASSWORD` read from
  `os.environ` only, exactly like `GMAIL_APP_PASSWORD`. Never written to a file, never logged,
  never passed to `db.py` or any table. When scheduled, they're GitHub Actions secrets scoped to
  `jobright_pull.yml` only — not exposed to `build-continue.yml` or any other workflow.
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
3. **If every task in the current phase's plan is checked and the next phase's plan doesn't exist
   yet, it does NOT author that plan itself.** It commits a one-line note to this spec and stops.
   Reasoning (corrected after the initial design — see the advisor review that caught this before
   the workflow ever ran): the `writing-plans` skill this design originally assumed the workflow
   would invoke is a `superpowers` **plugin** skill, not available in a plain
   `anthropics/claude-code-action` environment without `plugin_marketplaces`/`plugins` inputs this
   workflow doesn't set — and even if it were installed, `--allowedTools` here omits `Skill`.
   Phase plans are written by a human in an interactive session where the skill actually loads;
   this workflow only ever *executes* an already-written plan.
4. Otherwise, invokes Claude Code headless (`claude -p "..."`, non-interactive) with a
   self-contained prompt (no skill references) that points at the next unchecked task, implements
   it, runs its tests, and only commits when green — the repo's Definition of Done (tests pass,
   CLAUDE.md updated) applies to every commit this workflow makes. The prompt explicitly skips any
   plan step that asks it to write a memory file under `~/.claude/projects/` — that path doesn't
   exist in the CI environment.
5. Commits with `Co-Authored-By: Claude <noreply@anthropic.com>` and pushes to `main` directly (no
   PR review loop — this is the same trust level the user already grants interactive sessions per
   their global CLAUDE.md: "run tests, commit, and push without being asked").
6. If a run finds every phase listed in this spec (currently: 1, 2, 2.5, 3, 4, 5) fully checked
   off, it disables itself (`gh workflow disable`) and stops firing.

**The plan files are the progress doc.** No separate tracking file — each plan's `- [ ]` /
`- [x]` checkboxes are the machine-readable state a cold session reads to know exactly what's
next, and this spec's phase list is the map of which plan file is current. This reuses the
`writing-plans` skill's existing design instead of inventing a parallel state file.

**Failure mode:** same as the three existing workflows — uploads its log as an artifact, runs
`notify_failure.py` on `if: failure()`. A failed run does not advance the plan; the next hourly
fire retries the same unchecked task.

**2026-08-26 (build-continue.yml):** Phase 1 complete; Phase 2's plan needs to be written in an interactive session.

**2026-08-28 (build-continue.yml):** Checked again — Phase 2 is now fully designed above (as of
2026-08-27), but its plan file (`docs/superpowers/plans/2026-08-27-job-discovery.md`) still doesn't
exist. Still waiting on a human interactive session to author it via `writing-plans`; this workflow
took no other action this run.
