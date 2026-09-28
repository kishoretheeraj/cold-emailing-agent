# Beelink 24/7 Automation — Design

Status: approved for planning (2026-09-17). Supersedes nothing structurally, but amends three
governance statements from `docs/superpowers/specs/2026-08-30-phase2.5-auto-apply-design.md` —
see "Governance amendments" below.

## Vision

Move job-agent execution off ephemeral GitHub Actions runners onto a dedicated always-on Linux
box (a Beelink mini PC, 16GB RAM, SSD, purchased specifically for this), and add LinkedIn as a
new discovery source — driven by Anthropic's Computer Use API against the user's real,
persistently-logged-in browser session, not a headless scraping bot. The end state: the only
manual step anywhere in the pipeline is one review-and-approve tap in the existing
`/applications` UI, after which submission is fully automated.

## Non-goals (explicit, not silently dropped)

- **Not building a stealth/anti-detection browser.** No CDP-flag stripping, no patched
  `navigator.webdriver`, no fingerprint spoofing. The safety strategy is *pacing*, not evasion —
  see Governance amendment 1.
- **Not adding LinkedIn as an apply target.** `linkedin.com/jobs` and other aggregator links stay
  permanently excluded from `apply_agent.py`'s fill/submit path, unchanged from Phase 2.5. This
  phase adds LinkedIn only as a discovery source feeding `job_applications`.
- **Not doing multi-user/multi-tenant hardening.** Single user, single box, trusted operator.
  Security decisions below (RLS+RPC, signed-tag deploys) are about protecting against a stolen
  API key or a compromised CI step, not about untrusted third parties.
- **Not migrating `monitor.py` (reply detection) off GitHub Actions.** It runs ~50x/day, is
  network-only (no browser), and cloud availability beats a home box that can lose power. Stays
  on GHA indefinitely.
- **Not building Tailscale.** The user will set this up on the Beelink themselves, later, as
  their own task. Nothing in this design requires it or assumes its absence — VNC binds to the
  LAN interface either way; Tailscale is a transparent additional interface when added.
- **Not unifying LinkedIn browsing and ATS form-filling under one mechanism.** Investigated
  seriously and rejected: their safety requirements are opposite (see Architecture, "Two
  mechanisms, not one").
- **Not fetching or importing browser-use, Skyvern, or Steel.** Skyvern and Steel were already
  rejected in the Phase 2.5 spec for shipping CAPTCHA-solving; that rejection is unchanged.
  `browser-use` is being removed, not fixed (see "Why browser-use is being removed", not patched).

## Governance amendments (dated 2026-09-17 — explicit, user-confirmed, not silent reinterpretation)

1. **CAPTCHA / bot-detection rule, amended.** The Phase 2.5 rule "never bypass CAPTCHA or
   bot-detection, in any form, including stealth browser patching" continues to mean exactly
   that — no stealth patching, and if a CAPTCHA appears, stop and flag a human (now with a better
   answer than before: VNC in and solve it directly). What's added: a real, unpatched Chrome
   profile with a real logged-in session, driven by real X11 input events, is not "stealth" — it
   patches nothing and falsifies nothing. The safety property this design relies on is **pacing**
   (rate caps, randomized delay, human session-length norms — see "LinkedIn pacing parameters"),
   not technical evasion. This sentence exists so a future reader sees a clarification, not a
   reversal.
2. **LinkedIn discovery-only, reaffirmed.** Adding LinkedIn as a `job_applications` source does
   not change its status as a permanently-excluded apply target. Both facts hold simultaneously.
3. **hiQ v. LinkedIn does not cover this use case.** The Ninth Circuit's favorable hiQ holding
   applies to public, logged-out pages. Authenticated browsing with the user's own account,
   accepting LinkedIn's User Agreement, is a different and less-settled fact pattern — the 2022
   remand found hiQ breached that agreement and violated the CFAA specifically via
   password-protected-page access. Practical risk for one individual's low-volume personal
   browsing is account restriction, not litigation (hiQ was commercial-scale resale with fake
   profiles) — but this is a knowing risk acceptance, not a legal clearance. The user has
   confirmed understanding of this distinction.

## Current state (as of 2026-09-17) — what this design builds on

- `job_applications` (Supabase/Postgres) is the shared pipeline table. Sources today:
  `ats_scan` (`job_discovery.py`, reads company ATS boards via `ats.py`), `jobright`
  (`jobright.py`, unofficial session-cookie scraper), and `manual`. Stage pipeline:
  `saved → applied → phone_screen → onsite → offer/rejected/withdrawn/accepted`, plus
  `ready_to_submit` for the apply-agent's preview stage.
- `job_pick.py` scores every new `saved` row: cheap keyword filter → local sentence-transformers
  embedding similarity → Claude judge. A `strong` verdict zero-tap triggers
  `resume_agent.py --propose` then `--build`.
- `apply_agent.py` fills real forms: hand-mapped Playwright fillers (`ats_fillers.py`) for
  Greenhouse/Ashby/Lever; anything else routes to `_fill_generic_via_browser_use`, which is
  **confirmed broken** — verified by reading the installed `browser-use==0.1.40` package against
  the call site: `browser_use.llm` doesn't exist in this version, `Agent()` has no `page` kwarg,
  and `run_sync()` doesn't exist. All three raise, get swallowed by a bare `except Exception`, and
  log a warning — generic-ATS fill has been a guaranteed no-op since it shipped. Two further bugs
  found in `_answer_screening_questions`: the field locator (`page.get_by_text("?").all()`)
  matches any element containing a question mark, not screening fields specifically; and
  `profile_summary=job.get("role", "")` feeds Claude the job title instead of any real candidate
  information, directly undermining the "grounded only in real facts" instruction.
- The two-pass submit gate — `apply_agent.py --preview` (daily GHA cron) fills and stops before
  Submit, setting `stage='ready_to_submit'`; a human taps Approve in `/applications`; that
  dispatches `apply_agent_submit.yml`, the **only** place `APPLY_AGENT_ARMED=1` is ever set; and
  `submit()` independently checks `stage=='ready_to_submit' and apply_preview` before touching a
  browser — is the load-bearing safety design of the whole system and is being carried forward,
  strengthened, not weakened (see "The ARMED gate on a persistent host").
- `contact-manager/src/components/ApplicationsPage.tsx` is the current review UI: a table with
  Company, Role, Stage, Applied date, Pick badge, Blocked reason, and an Approve & Submit button
  that shows only a *count* of screening answers. **Nothing about the job (description, URL) and
  nothing about the resume/cover letter is rendered anywhere today** — the two artifacts the user
  most needs to review before approving are invisible. Also confirmed: `ready_to_submit` is listed
  in the frontend's stage enum (`types.ts:243`) but the PATCH route's own hardcoded stage array
  omits it, so selecting it 400s and silently reverts.
- Everything currently scheduled runs on GitHub Actions: `daily_agent.yml`, `monitor.yml`,
  `visa_intel_ingest.yml`, `jobright_pull.yml`, `apply_agent_preview.yml`,
  `apply_agent_submit.yml`, plus `build-continue.yml` (hourly, unattended, auto-commits to `main`
  with no human review — relevant to "Deploy safety" below).
- `job_applications` and every other table currently have **no row-level security** — the
  `resumes` storage bucket migration explicitly documents this as deliberate ("no service-role
  credential anywhere in the stack"). This is the fact that makes the approval-signal security
  decision below necessary rather than optional.

## Architecture

### Why claude-in-chrome is disqualified for unattended use

Four independent, documented blockers: (1) Chrome integration requires interactive `/login`;
`claude setup-token` (the documented unattended-auth path) keeps it off by design. (2) Pausing for
a human on CAPTCHA/login is a designed feature, not a bug to work around. (3) The extension's
service worker goes idle on long sessions and recovery is an interactive slash command. (4) A
confirmed, unresolved upstream issue describes exactly this box-plus-laptop scenario causing
scheduled tasks to randomly execute on the wrong device.

### The mechanism: Anthropic's Computer Use API, driven directly

`computer_toolset_20260801` (current tool type, no beta header, supported on
`claude-opus-5`/`claude-sonnet-5`). The application (not Claude, not an extension) owns the loop:
screenshot → send to Claude → receive a `tool_use` action → execute it (via `xdotool`/`scrot`
against a real X11 display) → send the result back → repeat until `stop_reason != "tool_use"`.
No account-plan requirement, no permission-prompt surface, no extension. Anthropic's own
`computer-use-demo` reference (Xvfb + x11vnc + noVNC + mutter + tint2 + xdotool + scrot on Ubuntu)
is the topology and package-list recipe for the Beelink's display slots. Cost facts that drive
budgeting below: 1,000–1,800 tokens per screenshot; prune history to the last 3 screenshots.

### The "real browser" property, precisely

The one flag that actually matters: **never pass `--headless` or `--remote-debugging-port`** on
the LinkedIn Chrome profile. That's what sets Chrome's automation-detectable state; a CDP
*attachment* alone doesn't, but the launch configuration does. This means the LinkedIn profile
cannot use Playwright, browser-use, or any CDP-based tool — input must come from real X11 events
and screenshots from real X11 capture, which is exactly what the Computer Use API's execution
loop provides.

### Two mechanisms, not one — investigated and deliberately kept separate

| | LinkedIn ingestion | ATS form-filling |
|---|---|---|
| Requires | No automation flags → no CDP | Deterministic containment on the submit action |
| Implies | `xdotool`/`scrot` pixel-level driving | Playwright (CDP) with a constrained tool schema |
| Blast radius if wrong | Reads a public-to-you page | Submits a real application |

A single Chrome process cannot both omit `--remote-debugging-port` (required for LinkedIn) and
use it (required for the ATS fill's deterministic control). More importantly, pixel-level
computer-use is *strictly weaker* containment for a submit action than a constrained tool schema —
see next section. Unifying them would trade a real safety property for implementation
convenience. The two mechanisms share infrastructure (display slots, systemd scaffolding, the
Supabase poller, VNC) but stay operationally distinct.

### Fixing the ATS path: a constrained tool schema, not a fixed `browser-use` pin

`browser-use` is removed from `requirements-apply.txt` entirely, not re-pinned — its own issue
tracker documents CDP timeouts in Xvfb specifically and existing-profile timeouts, both exactly
this deployment shape. Replacement: a new module, `ats_agent.py`, running a Claude tool-use loop
over Playwright with a schema limited to `list_fields()`, `fill_field(label, value)`,
`select_option(label, value)`, `upload_file(label, ref)`. **There is no click or submit tool in
the schema.** The model cannot emit a submit action because the action doesn't exist for it to
emit — this is containment at the dispatch layer, not a prompt instruction, and it closes the hole
the Phase 2.5 spec explicitly conceded ("restrained only by the task-string instruction not to
click Submit, not a hard guarantee"). The actual submit click remains exactly where it is today:
deterministic code in `submit()`, after both the `stage`/`apply_preview` gate and the
`APPLY_AGENT_ARMED` check.

### Chosen architecture: full runtime migration, staged M1→M5

All scheduled work moves to systemd on the Beelink; GitHub Actions retains `monitor.py` and CI
(the test suite) only. The approval tap on Vercel changes from push (GitHub `workflow_dispatch`)
to a database write the Beelink polls for.

```
┌─ VERCEL (unchanged surface, new transport) ─────────────────────────────┐
│  /applications → POST /api/applications/[id]/submit                     │
│  writes approved_at via a security-definer RPC (see "Approval security")│
└────────────────────────────┬─────────────────────────────────────────────┘
                             │  Supabase (cloud, unchanged)
┌────────────────────────────▼── BEELINK (LAN only, no inbound) ──────────┐
│  systemd timers (OnCalendar + RandomizedDelaySec)                        │
│    job-linkedin-ingest   a few times/day, short paced sessions [slot 0] │
│    job-pick              oneshot, torch — never co-resident with Chrome │
│    job-apply-preview     daily                          [slots 1-2]     │
│  systemd service (long-running, UNARMED)                                │
│    job-approval-dispatcher  polls Supabase (~60s)                       │
│      └─ sudo systemctl start job-apply-submit@<id>.service  (ARMED)     │
│  display slots (templated units, N=3, hard cap 4)                       │
│    xvfb@N → chrome-profile@N → x11vnc@N → novnc@N   (VNC on LAN only)   │
└───────────────────────────────────────────────────────────────────────────┘
```

### Resource budget on 16GB — 3 concurrent slots, not 13

Naive per-slot arithmetic (~0.8-1.0GB per headful Chrome+VNC slot) suggests ~13 concurrent
sessions fit. Don't do that. Two reasons: (1) a Beelink in this class is CPU-bound (4 E-cores,
no hyperthreading) well before RAM runs out — screenshot PNG encoding plus rendering saturates
cores first; (2) `job_pick.py` imports `sentence_transformers`/torch at module level and must
**never** be co-resident with a long-lived browser-agent process — it stays a short-lived
`oneshot` systemd unit. **Recommendation: 3 concurrent display slots steady-state (1 LinkedIn + 2
ATS), hard cap 4.** The LinkedIn slot is exactly 1, always — concurrency buys nothing there since
the pacing cap is per-account, not per-process, and two simultaneous LinkedIn sessions is itself a
detection signal.

### systemd topology

```
xvfb@.service            Restart=always  — Xvfb :%i -screen 0 1280x800x24
chrome-profile@.service  After=xvfb@%i, headful, --user-data-dir=/var/lib/job-agent/profiles/%i
x11vnc@.service          -display :%i -rfbport 59%i -localhost -rfbauth ...
novnc@.service           websockify --web /usr/share/novnc 60%i localhost:59%i

job-linkedin-ingest.timer/.service   OnCalendar + RandomizedDelaySec=1800   [slot 0]
job-pick.timer/.service              Type=oneshot (torch)
job-apply-preview.timer/.service     [slots 1-2]
job-approval-dispatcher.service      Restart=always, UNARMED
job-apply-submit@.service            Type=oneshot, ARMED, instance = application id
notify-failure@.service              OnFailure target for all of the above
```

Every unit: `RuntimeMaxSec=` (a wedged Chrome must not run forever — replaces GHA's
`timeout-minutes`), `NoNewPrivileges=yes`, `PrivateTmp=yes`, `ProtectSystem=strict`,
`ProtectHome=yes`, explicit `ReadWritePaths=`. Dispatcher additionally gets
`Restart=on-failure`, `RestartSec=`, `StartLimitBurst=` so a crash loop can't hammer Supabase.

### The ARMED gate on a persistent host — rebuilt stronger, not weaker

- `/etc/job-agent/base.env` (`root:root 0600`) — all secrets except the arm flag.
- `/etc/job-agent/armed.env` (`root:root 0600`) — **only** `APPLY_AGENT_ARMED=1`, loaded via
  `EnvironmentFile=` by **exactly one unit**: `job-apply-submit@.service`. Every other unit,
  including the preview unit, explicitly sets `Environment=APPLY_AGENT_ARMED=` to make the
  absence testable, not just implicit.
- The repo's own `.env` at project root is **never** deployed to the Beelink — `apply_agent.py`'s
  `__main__` gains a startup assertion that `config.load_dotenv()` did not just source
  `APPLY_AGENT_ARMED` from an unexpected file.
- The dispatcher is unarmed and structurally cannot arm itself: it invokes the armed unit through
  a `sudoers` entry scoped to exactly `/usr/bin/systemctl start job-apply-submit@*.service`
  (NOPASSWD, single command, no wildcard elsewhere).
- The in-code gate gains a **third** condition, not fewer:
  `stage == 'ready_to_submit' AND apply_preview IS NOT NULL AND approved_at IS NOT NULL AND applied_date IS NULL`.
  Three independently-enforced, independently-mutation-tested gates instead of two.
- LinkedIn credentials are never written to any env file — the persistent Chrome profile's session
  cookie is the credential; the user logs in once by hand over VNC.

### Approval-signal security — RLS + RPC (user-chosen)

`job_applications` gets row-level security enabled (this project's first table to have it — a
deliberate, scoped exception to the "no service-role credential anywhere" convention, not a
reversal of it: no service-role key is introduced). A policy denies anonymous `UPDATE` on
`approved_at` and `stage` directly; a `security definer` Postgres RPC
(`approve_application(id, ...)`) is the only path that can set `approved_at`, and only the
server-side `/api/applications/[id]/submit` route calls it (still using the existing anon-scoped
client — the RPC's `security definer` is what grants the elevated write, not a new key in Vercel's
env). This keeps the "no service-role credential" property intact while closing the forgeable-
approval hole a naive direct-write would have opened.

### Deploy safety — signed tags (user-chosen)

`build-continue.yml` pushes AI-generated commits straight to `main`, hourly, unattended, with no
human review — that's fine on its own, but if the Beelink auto-pulled `main` on a timer, unattended
code and the ARMED submit credential would end up co-located on one box, defeating the isolation
`APPLY_AGENT_ARMED` exists to guarantee. Fix: the Beelink's deploy script pulls only signed git
tags (`git tag -s`), created by the user by hand when they've reviewed a commit and want it live.
`build-continue.yml`'s hourly commits accumulate on `main` but never reach the box until tagged.

### LinkedIn pacing parameters

Rate caps (grounded in observed 2026 practitioner data, not guessed): profile views capped well
under LinkedIn's own 500/day free-tier limit — target **≤100/day**, randomized, never linear
timing (perfectly-regular intervals are the single most commonly cited detection trigger).
Easy-Apply-equivalent actions (none planned for phase 1, LinkedIn is discovery-only, but the cap
exists for future reference) ≤50/24h per LinkedIn's own stated limit. Session shape: a few short
sessions per day (`RandomizedDelaySec=1800` on the systemd timer handles both the "avoid
predictable timing" goal and the pacing goal at once), each session capped to a modest number of
postings viewed, with per-action jitter inside the loop (starting values: ~25ms keystroke delay,
multi-step cursor interpolation rather than instant jumps — mirrors values used in comparable
open-source computer-use implementations). If a CAPTCHA or login challenge appears, the loop stops
and flags the human via the UI's attention badge (see UI gap U14) rather than attempting to solve
or bypass it.

### Host baseline

Never sleep (`systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target`,
BIOS "restore on AC power loss = on" for actual power outages). `unattended-upgrades` with
`Automatic-Reboot "false"` — security patches apply, reboots are scheduled by hand, never mid-task.
`systemd-timesyncd` enabled. `/dev/shm` mounted ≥2GB or `--disable-dev-shm-usage` passed (default
64MB causes Chrome tab crashes). VNC (`x11vnc`) binds `-localhost` with noVNC exposed on the LAN
interface only, password-protected, no port forwarding, no public exposure — transparent to add
Tailscale on top later. Bare metal, not Docker, for the browser stack — the entire point of a
persistent profile is a genuinely long-lived Chrome install; containers add shm/volume friction
for no benefit on a single-tenant box. Both new agents (`cu_linkedin.py`, `ats_agent.py`) log to
`api_usage_log` via the existing `usage_tracking.log_usage()` from their first commit
(`module='cu_linkedin'` / `module='ats_agent'`) so the real per-action cost of computer-use is
visible from day one.

## UI changes needed (`contact-manager/src/components/ApplicationsPage.tsx` and friends)

Blocking for the stated goal ("review job + resume, then click Approve"):

| # | Gap | Fix |
|---|---|---|
| U1 | No resume/cover letter rendered anywhere, despite `resume_file_ref`/`cover_letter_file_ref` existing on the type | New `ApplicationDetailSheet.tsx` + `GET /api/applications/[id]/files` (signed URLs from `resumes` bucket), inline PDF viewer |
| U2 | No job details rendered (`job_url`, `posting_snapshot` description/responsibilities/qualifications/benefits all exist, never shown) | Same detail sheet |
| U3 | `apply_preview` reduced to a bare count in the table | Render `platform`/`field_values`/`eligibility_answers`/`screening_answers` as labeled sections in the detail sheet |
| U4 | No confirmation step on Approve — one tap submits a real application | Wire up the already-unused `ConfirmModal` component |
| U5 | Approve is fire-and-forget with no status feedback; under the new polling model this can take up to ~60s | Add `approved_at`-aware status (`pending → submitting → applied/failed`) with row refresh/polling |

Promised in the Phase 2.5 spec but not built:

| # | Gap |
|---|---|
| U6 | `pick_score`/`pick_reasoning` not inspectable on hover/expand, only the bare verdict badge |
| U7 | No filter control at all, despite the API already supporting `?stage=` |
| U8 | `source_channel` never displayed (and never written — pairs with known follow-up #4) |
| U9 | Pick verdict badge has no color/variant mapping (strong/maybe/no render identically) |

Bugs and new needs:

| # | Gap |
|---|---|
| U10 | `ready_to_submit` is in the frontend's stage enum but missing from the PATCH route's own array → 400 → silent revert. Fix: import the shared constant instead of a second hardcoded array. |
| U11 | No edit-before-approve for a wrong screening answer — pairs with known follow-up #2 (submit regenerates answers instead of reusing approved ones); fixing U11 makes that follow-up user-visible and worth fixing together |
| U12 | New: `source` filter/column so `linkedin`-discovered rows are distinguishable from `ats_scan`/`jobright`/`manual` |
| U13 | New: a health strip (last successful run per systemd service, from `agent_runs`) — replaces the GHA red-X signal that disappears once GHA is retired |
| U14 | New: attention badge when a display slot is blocked on a human (CAPTCHA/2FA), with a direct noVNC link to that slot |
| U15 | Cost columns exist on the row and are never shown — cheap win |

## Data model changes

- `job_applications.approved_at TIMESTAMPTZ NULL` — new column, the third submit-gate condition.
- RLS enabled on `job_applications` (see "Approval-signal security"); `approve_application(id, ...)`
  security-definer RPC.
- `agent_runs.source` (already exists per `db.record_run(..., source=)`) becomes load-bearing for
  U13's health strip — no new column, new consumer.
- No new table for LinkedIn — it writes into `job_applications` via the existing
  `db.create_job_application(..., source='linkedin')` path, unmodified.

## Milestones (M1 → M5)

- **M1** — Beelink base provisioning (host baseline, systemd display-slot units) +
  `cu_linkedin.py` writing into `job_applications`. Nothing else moves yet. Validate real memory
  with `smem`/`ps_mem` (not `ps`, which over-counts Chrome's shared mappings) before adding load.
- **M2** — UI gaps (U1-U15), done *before* migrating the executor so the review workflow is
  validated against the still-working GHA path.
- **M3** — `ats_agent.py` (constrained tool schema) replaces `_fill_generic_via_browser_use`;
  `browser-use` dropped from `requirements-apply.txt`. Still runs on GHA at this stage.
- **M4** — Move `job-apply-preview`/`job-apply-submit`/`job-pick` to systemd; rebuild the ARMED
  gate per "The ARMED gate on a persistent host"; retire `apply_agent_preview.yml` and
  `apply_agent_submit.yml`.
- **M5** — Move `jobright_pull.yml` to systemd. `monitor.yml` and CI remain on GHA permanently.

## Testing strategy

Follows this repo's existing convention: every code change ships with tests, every outbound call
mocked. `cu_linkedin.py` and `ats_agent.py` each get their Anthropic Messages API calls mocked at
the client boundary, and their action-execution layer (`xdotool`/`scrot` subprocess calls for
`cu_linkedin.py`; Playwright page calls for `ats_agent.py`) mocked separately, so the sampling
loop itself is unit-testable without a real X11 display. systemd units and shell scripts aren't
unit-testable the normal way; each milestone gets a manual smoke-test pass on the real Beelink
before its systemd timers go live — mirroring the "live-verify before scheduling" pattern already
used for the Stage-1 visa-intel and Form D ingestion features (both found real bugs on first live
run despite a green test suite).

## Open risks / accepted trade-offs

- **Real cost increase per action.** Screenshots cost 1,000-1,800 tokens each; a single
  browse/fill session can run 15-40 turns. Mitigated by history pruning (last 3 screenshots) and
  full `api_usage_log` visibility from commit 1 — not eliminated.
- **Beelink uptime/power/disk is now the user's operational responsibility.** GHA's free
  infrastructure, timeout enforcement, and 30-day log-artifact retention all disappear and must be
  rebuilt (systemd `RuntimeMaxSec=`, logrotate, a heartbeat/watchdog — see U13).
- **LinkedIn account-restriction risk is real and not eliminated, only mitigated.** Pacing caps
  reduce but do not remove the chance of a temporary feature restriction on the user's real
  account. Accepted knowingly per Governance amendment 3.
