# Fifty a day: sourcing, dedup, triage and throughput

**Status:** Draft. **Date:** 2026-10-08.
**Goal:** the pipeline finds, prepares and queues enough good-fit jobs for the operator to
release 50 applications a day. Every application still needs the operator's signed one-tap
approval. Nothing new spends API money: every model call runs on the Claude subscription
on the Beelink.
**Builds on:** [first ten applications](2026-10-08-first-ten-applications-design.md),
[Phase 2.5 auto-apply](2026-08-30-phase2.5-auto-apply-design.md),
[resume subscription transport](2026-10-04-resume-subscription-transport-design.md).

---

## 1. What breaks at 50 a day (reproduced 2026-10-08)

Each problem below was reproduced against the real `db.py`, running on a local Postgres 16
with every migration applied. PostgREST 12 sits in front with Supabase's 1000-row cap
(`scripts/stress/`), or the problem was shown with a direct call to the shipped function.

| # | Failure | Evidence |
|---|---|---|
| F1 | **The same job is saved (and later applied to) several times.** Dedup is exact `job_url` only. | Ten spellings of four real jobs (Ashby `/application?embed=true`, `utm_source`, Greenhouse `boards.` vs `job-boards.` vs a company page's `?gh_jid=`, Lever `/apply`, Workday `/en-US/`) produced 10 rows. |
| F2 | **Two sources saving one URL at the same moment raise errors.** The check-then-insert loses the race, and `_retry` repeats a unique-violation insert for about 6 s before raising. | 8 concurrent inserts: 1 inserted, 5 returned None, 2 raised `23505` after about 6 s. |
| F3 | **A ready row becomes invisible once 1000 newer `saved` rows exist.** `preview_candidates()` reads every saved row, newest first, with no filter. The 1000-row cap then cuts the oldest, which are the ready ones. | 1501 saved rows, 1 ready row (the oldest): 1000 returned, 0 candidates. |
| F4 | **Rows on unsupported sites starve the prepare queue.** `run_prepare` takes the first 5 candidates and *then* finds they can't be prepared on this host ("skipped"). The same 5 come back first on every run. | 5 Oracle rows plus 1 Lever row gave outcomes `skipped x5`, every run. The Lever row was never reached. |
| F5 | **A failing row is retried forever.** `failed_retryable` is always eligible, with no attempt count and no backoff. | Code: `APPLY_AGENT_PREVIEW_ELIGIBLE_STATUSES`, and `claim_application` accepts `failed_retryable` unconditionally. |
| F6 | **The platform classifier matches substrings anywhere in the URL.** | `clever.com/...` came back as `lever`, `?src=greenhouse.io` as `greenhouse`, and `careers.ashbyhq.com.evil.example` as `ashby`. A company page carrying `?gh_jid=` came back as `generic`. |
| F7 | **The title filter admits almost anything.** It does substring checks for `pm`, `manager` and `analyst`. | 14 of 15 sample titles passed, including "Software Development Engineer", "Store Manager", "VP of Product" and "Senior Director, Product Management". |
| F8 | **No location, seniority, posting-age, sponsorship or liveness filter runs before money and quota are spent.** Non-US, senior, closed and no-sponsorship roles get a resume built, and only then get knocked out at preview. | Code: `job_discovery.run`, `jobright.run`, `job_pick.score_job`, `resume_agent.drain`. |
| F9 | **Discovery keeps only each company's first 25 postings, before filtering by title.** A large employer's PM roles past position 25 are never seen. | `ats.fetch_jobs(company, max_jobs=25)`, then the title filter. |
| F10 | **The fit judge bills the API**: `emailer._call_claude` in GitHub Actions. A parse failure drops the job as "no" and it is never rescored. | `job_pick._llm_judge` and `jobright_pull.yml`. |
| F11 | **The uploaded resume is named `tmpXXXX.pdf`**, and recruiters see that name. The temp files are never deleted either. | `_attach_resume_and_cover_letter`, which uses `NamedTemporaryFile(delete=False)`. |
| F12 | **Submit could land in `needs_confirmation` without ever clicking.** `clicked = True` is set before the locator is resolved. A missing button, or two matching buttons, raises inside `.click()`. | `submit()`. |
| F13 | **Confirmation waits 2 s once.** A Greenhouse submit that uploads files, or that asks for the emailed security code, reads as unconfirmed. | `_submission_confirmed`. Greenhouse documents that it may ask for a code sent to the applicant's email before accepting the application. |
| F14 | **No daily cap and no per-company cap.** Eight roles at one company can all go out the same day. | No such code exists. |
| F15 | **The resume worker builds for rows that can never be prepared on this host** (Oracle, iCIMS, custom pages), in pure FIFO order. | `get_strong_applications_without_resume`. |

Safe under load: 20 concurrent `claim_application` calls on one row gave exactly one lease.

## 2. What we take from open source

- **[Career-Ops](https://github.com/santifer/career-ops)** (MIT, by santifer). We port ideas and
  rules to Python, with attribution in the module docstrings:
  - Zero-token public-ATS providers, including Workday's CXS `POST /wday/cxs/{tenant}/{site}/jobs`.
  - The location filter tiers: `block_hard` > `always_allow` > `block` > `allow`, plus a remote
    rescue in the title. US state expansion stops `block: Dublin` from dropping "Dublin, OH".
    The Workday location is recovered from the URL when the field says "5 Locations".
    Keywords match on word boundaries, so "india" does not drop "Indiana".
  - "Title identity" for duplicate roles: the same set of title words, never fuzzy similarity,
    because "PM - Berlin" and "PM - Munich" are two different openings.
  - A SimHash content fingerprint of the job description, to catch reposts.
  - The liveness ladder: an ATS API first, conservative on ambiguity. A Lever API 404 is not
    proof the posting is gone.
  - Bot-challenge pages are never classified as "closed".
  - Ranking annotates and never deletes.
- **[SimplifyJobs/New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions)**
  publishes `.github/scripts/listings.json`, which had 19,742 rows on 2026-10-08. 122 of them were
  active "Product" roles. Every row has the company, title, a direct ATS URL, locations,
  `date_posted`, a `sponsorship` value and `active`. The direct URLs also tell us which board
  each company uses (Greenhouse slug, Ashby org, Lever slug, Workday tenant/site). That turns
  the feed into a seed list for sweeping whole company boards.
- **JobSpy** (LinkedIn, Indeed and other boards) is not used. Those boards' terms forbid
  scraping. Their links go to aggregators we never apply through, and JobRight and LinkedIn
  discovery already cover that ground.
- Greenhouse documents invisible reCAPTCHA with an emailed-code fallback, and Ashby documents
  spam protection with tunable strictness. Our posture does not change: a real Chrome on a
  residential IP, human pacing, a human takeover for any visible challenge, and no stealth or
  solving. The emailed code goes to the operator's own receipt inbox, and the worker reads it
  just as it already does for Workday signup codes.

## 3. Design

### 3.1 Job identity: `job_identity.py` (pure, no I/O)

`identify(url)` returns `{platform, tenant, job_id, job_key, canonical_url, apply_url}`.

- **Classify by hostname**, never by substring:
  - Greenhouse: `boards.` / `job-boards[.eu].greenhouse.io/{board}/jobs/{id}`, plus any page
    carrying `gh_jid`. Greenhouse job ids are global, so the key is `greenhouse:{id}`.
  - Lever: `jobs[.eu].lever.co/{slug}/{uuid}[/apply]`.
  - Ashby: `jobs.ashbyhq.com/{org}/{uuid}[/application]`.
  - Workday: `{tenant}.wdN.myworkdayjobs.com/[locale/]{site}/job/.../{title}_{REQ}`, or
    `myworkdaysite.com/recruiting/{tenant}/{site}/...`. The key is `workday:{tenant}:{site}:{REQ}`.
  - SmartRecruiters, Workable and Oracle Cloud HCM: keyed by their job ids.
  - Aggregators: from the configured domain list.
  - Anything else: `generic`.
- **Generic canonical URL:** lowercase the host, use https, drop the fragment and the trailing
  slash, and drop tracking parameters (`utm_*`, `gh_src`, `source`, `src`, `ref`, `lever-*`,
  `trk`, `embed`). The key is `url:{canonical}`.
- `ats_platform.classify` becomes a thin wrapper around `identify`. Unknown keeps meaning
  `generic`.

**Dedup policy at insert** (`db.create_job_application`):

1. **Same `job_key`:** skip. This is enforced by a unique partial index, so a lost race
   returns None instead of raising, and a unique violation is never retried.
2. **Same company key and title identity**, with an existing row that is not
   withdrawn/rejected, created in the last 45 days: skip as a cross-source duplicate.
3. **Same company key, title identity and description fingerprint** as a row that reached
   `applied`/`submitted` in the last 180 days: skip as a repost.

New columns: `job_key`, `platform`, `company_key`, `title_key`, `location`, `posted_at` and
`jd_fingerprint`. A backfill derives `job_key` and `platform` for existing rows. Where the
backfill finds two rows with one key, every row but the oldest gets `job_key` set to NULL.
Those duplicates stay visible and are counted in the migration's NOTICE.

### 3.2 Search preferences: `job_filters.py` and the `job_search_preferences` prompt row

This is one JSON row the operator edits on the Prompts page. A missing or invalid row falls
back to the defaults below.

```json
{
  "titles": {"include": ["product manager", "associate product manager", "apm", "product analyst",
                          "product owner", "technical product manager", "product management",
                          "product operations", "program manager", "business analyst", "strategy"],
             "exclude": ["designer", "engineer", "developer", "sales", "marketing", "account executive",
                         "recruiter", "contract", "contractor", "part time"]},
  "seniority_exclude": ["senior", "sr", "staff", "principal", "lead", "director", "head", "vp",
                        "vice president", "chief", "group", "intern", "internship", "co-op"],
  "locations": {"always_allow": ["united states", "usa", "u.s.", "us remote", "remote us", "sf", "nyc",
                                 "bay area", "silicon valley"],
                "allow": ["remote", "anywhere"], "block": [],
                "block_hard": ["india", "united kingdom", "uk", "ireland", "canada", "germany", "...", "emea", "apac"]},
  "max_posting_age_days": 30,
  "per_company_cap_30d": 3,
  "daily_submit_cap": 50,
  "skip_no_sponsorship": true
}
```

Rules:

- Titles: no seniority keyword may match (reason `seniority`), an include keyword must match and no exclude keyword may match (reason `title`). Matching is on
  word boundaries, so "pm" never matches "development". `word:` and `stem:` prefixes
  work as in Career-Ops.
- Locations: the Career-Ops tier semantics above. An empty location passes.
- Posting age: postings older than `max_posting_age_days` are skipped. An unknown date
  passes.
- Sponsorship: a source label of "does not offer sponsorship" or "citizenship required", or
  any `application_quality.knockout_reasons` hit on the description, skips the job when
  `skip_no_sponsorship` is set and the operator's eligibility says sponsorship is needed.

Filtered-out postings are never inserted. A scan only counts them by reason
(`[SOURCING] | ... | skipped_location=12 ...`).

### 3.3 Sources: `job_sources.py`, plus the `job_boards` table

All sources are zero-token, public and need no auth. Every one of them returns the same
normalized shape: `{company, title, url, location, posted_at, description, sponsorship, source}`.

- `simplify`: the listings.json feed, keeping rows that are active, visible and in the
  configured categories (default `Product`).
- `greenhouse`, `ashby`, `lever`: whole public boards. Titles are filtered before any cap
  (fixes F9).
- `workday`: a CXS search per tenant/site with `searchText` set to each title include phrase
  (paginated and capped). The detail endpoint supplies the description for jobs that survive
  the filters.
- `jobright` and `linkedin` keep their own modules and go through the same filters and dedup.

`job_boards` (company, platform, board, enabled, last_scanned_at, last_count,
consecutive_failures, dead_at) holds the companies to sweep. Each Simplify run inserts every
board it sees. Contacts and `company_intel` names are probed once for Greenhouse, Ashby and
Lever. After 3 consecutive failures (404, or Workday's maintenance page), a board is marked
dead and skipped.

`job_sourcing.py` runs every source, then filters, then dedups, then inserts at
`stage='saved'`. Each run writes one `agent_runs` row (`source='job_sourcing'`) carrying the
counts. On the Beelink it runs every 2 hours from a timer. It needs only the anon key: no
browser and no model.

**Liveness** (`job_liveness.py`) runs just before the resume is built, so a closed posting
never costs subscription quota:

- Greenhouse: the per-job API. A 404 or 410 means closed.
- Ashby: the posting is no longer listed on the board.
- Lever: a 404 is not authoritative, so it gets the page check at preview.
- Workday: a CXS job 404, or Workday's "job posting is no longer available" error code.
- Anything ambiguous counts as live.

A closed row goes to `unsupported` with `apply_blocked_reason="Posting closed: ..."`.

### 3.4 Scoring on the subscription (`job_pick.py`)

- The structured filter becomes `job_filters`.
- The judge runs through `claude_subscription.complete` when `JOB_PICK_BACKEND=subscription`,
  10 jobs per call, returning a JSON list.
  - Rows from a call that fails to parse stay unscored and are retried on the next run.
  - After 3 failed attempts (`pick_attempts`), a row is set to `maybe`.
  - A `ClaudeUsageLimitError` stops the run.
- The embedding stage keeps the profile vector for the whole run instead of re-encoding it
  per job.
- Scoring moves to the Beelink: a new `job-pick.service` oneshot, run by
  `job-sourcing.service` after sourcing. `jobright_pull.yml` stops scoring. It only pulls, and
  stops installing torch.

### 3.5 Choosing what to build and prepare

`db.get_strong_applications_without_resume` orders by preparability on this host first, then
`pick_score` descending, then freshness. It skips:

- rows whose platform has no adapter here;
- companies already at `per_company_cap_30d`, counting submitted, approved and ready rows;
- rows past the posting-age limit.

Unsupported platforms keep their strong verdict. The UI lists them as "apply by hand" with the
posting link, and no resume is built automatically.

`preview_candidates()` becomes a server-side query: `stage='saved'`, both files built,
`automation_status` in (`idle`, `failed_retryable`), platform preparable here, and
`prepare_attempts < 3`. A `failed_retryable` row also waits a backoff:
`updated_at < now() - 30 min x 2^attempts`. The query orders by `pick_score` descending, then
`created_at`, and takes `limit`. `claim_application(..., 'preparing')` increments
`prepare_attempts`, and `requeue_preview` resets it to zero. `run_prepare` keeps going until
it has *claimed* `limit` rows, or the candidates run out (fixes F3 to F5).

### 3.6 Applying

- **Attachments** are uploaded as `Kishore_Theeraj_Vasudevan_Jaya_Resume.pdf` and
  `..._Cover_Letter.pdf`, from a per-run temporary directory that is removed afterwards (F11).
- **The Submit control is resolved before `clicked = True`**: exactly one visible, enabled
  button with the platform's label (Greenhouse "Submit application", Lever "Submit
  application", Ashby "Submit Application"). Zero or several matches is a pre-click failure,
  and the row goes to `failed_retryable` (F12).
- **Confirmation** is polled for up to 30 s. The confirmation text or a known confirmation URL
  (Greenhouse `/confirmation`, Lever `/thanks`) counts as confirmed; rejection text counts as
  failed (F13).
- **The emailed security code**: when the page asks for one after Submit, the worker reads it
  from the receipt inbox (sent by Greenhouse, after the click), enters it, and presses the
  form's own button. This is the same submission, which the site is holding until it has the
  code. If no code arrives, the worker requests a takeover with kind `email_verification`.
- **Caps** (F14):
  - **Daily cap:** the submit worker stops once `daily_submit_cap` rows were submitted in the
    current America/New_York day. The remaining approvals wait for the next day.
  - **Per-company cap:** no documents are built for a company that already has
    `per_company_cap_30d` applications in the last 30 days (`resume_agent._queue_gate`).

### 3.7 Capacity

The timers and batch sizes give these rated daily capacities (`scripts/stress/capacity.py`):

| Stage | Rate | Per day |
|---|---|---|
| Sourcing | every 2 h | thousands of postings, zero tokens |
| Scoring | 10 jobs per call | about 30 calls for 300 jobs |
| Resume build | 6 per 30 min | 288 |
| Prepare | about 3 min a row, serialized on display :1 | about 400 |
| Submit | 3 per minute, capped | 50 |

Subscription calls per application:
- 1 strategy call;
- 1 cover-letter call;
- 1 screening call, with all of a form's questions in a single call (§3.6 follow-up);
- 0.1 judge calls.

That is about 3 calls per application, or about 160 a day at 50 a day, plus retries.
A usage-limit error pauses any stage, and the stage resumes on its next run.

The real limits are outside the code:
1. how many good-fit jobs exist each day (the Simplify feed alone held 122 live PM new-grad
   roles);
2. required custom questions that send a row to `needs_input`;
3. CAPTCHA takeovers.

`apply_status` reports all three.

## 4. Non-goals

- No auto-submit. The signed approval stays per application.
- No CAPTCHA solving, no stealth plugins, no proxies.
- No scraping of LinkedIn or Indeed. LinkedIn discovery stays on the operator's own browser
  at its paced caps.
- No job is applied to through an aggregator.

## 5. Testing

- **Unit:** `job_identity` (every URL family, plus adversarial hosts), `job_filters` (ported
  location/title cases), `job_sources` parsers (recorded payloads), and SimHash.
- **Integration** (`tests/test_stress_local.py`, run only when `STRESS_SUPABASE_URL` is set): the
  F1 to F5 and F14 scenarios against the real `db.py`, Postgres and PostgREST, with the
  1000-row cap.
- **Browser load:** 50 previews through the real `apply_agent` and real Chromium against
  local Greenhouse/Lever/Ashby lookalike forms. It measures time per row and checks for leaked
  Chromium processes and temp files.
