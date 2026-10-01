# Application Reliability Plan — Validation, Critique & Improvements

Date: 2026-09-30
Target: [2026-09-28-application-reliability-plan.md](../superpowers/plans/2026-09-28-application-reliability-plan.md)
Context: Hardware constraints (Beelink 16GB RAM, quad-core CPU, Linux/Xvfb), ATS form structures (Greenhouse, Lever, Ashby, Workday), and merge review findings ([2026-09-28-merge-review.md](2026-09-28-merge-review.md)).

---

## Executive Summary & Verdict

The proposed implementation plan is **fundamentally sound in its engineering discipline**:
1. **Fix correctness before adding automation**: Freezing approved revisions, fixing prompt grounding bugs, and eliminating false successes.
2. **Deterministic-first execution**: Keeping Playwright as the primary driver and treating AI agents strictly as fallbacks.
3. **Strict scope bounding**: Greenhouse, Lever, and Ashby first; isolating LinkedIn to discovery; keeping Workday deferred.
4. **Decoupled assistant layer**: Leaving Hermes and OpenClaw outside the core engine.

However, deep evaluation against hardware constraints, existing codebase capabilities, and real-world ATS form mechanics reveals **three architectural mismatches and two major missed opportunities**:

1. **Major Mismatch: Skyvern is the wrong tool for local Beelink execution.** Skyvern requires a full Docker Compose stack (FastAPI server, PostgreSQL instance, Web UI, and Chromium runtime). Running it on a 16GB quad-core mini-PC already running Next.js, Postgres pollers, Xvfb, VNC, and PyTorch will cause severe CPU/memory contention.
2. **Better Alternative: Stagehand or an In-Tree Constrained Schema (`ats_agent.py`).** ATS forms do not fail because they need autonomous browsing; they fail because of *custom React comboboxes, shadow DOMs, and dynamic dropdowns* (especially on Ashby). Stagehand runs in-process with Playwright, caches resolved selectors ($0 token replay), and uses DOM/accessibility trees rather than heavy vision models.
3. **Missed Automation Opportunity: Gmail OTP / Magic Link Interception.** The plan treats email verification as an interruption requiring manual takeover. The repo already has full Gmail API & IMAP clients (`gmail.py`, `emailer.py`). Verification codes (Ashby/Workday) can be intercepted and auto-filled in seconds without human intervention.
4. **Database State Collision:** Overloading the recruiting pipeline column `stage` with execution states (`preparing`, `ready_to_submit`, `submitting`) was the root cause of multiple bugs in merge review findings #4–6. Execution state must be completely decoupled from recruitment stage.

---

## Parameter-by-Parameter Analysis

### 1. Browser Automation Engine: Benchmarking Matrix

The plan proposes repairing Browser-Use and benchmarking Skyvern across 30 scenarios. Here is how the options actually compare on a 16GB mini-PC running local automation:

| Parameter | Pure Playwright (`ats_fillers.py`) | Browser-Use (Python) | Skyvern (Local) | Stagehand (Browserbase) | In-Tree Constrained Schema (`ats_agent.py`) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Runtime Footprint** | Extremely light (<100MB RAM) | Moderate (~400MB + Python) | **Heavy** (Docker + Postgres + API + UI: 2–4GB RAM) | Extremely light (in-process with Playwright) | Extremely light (in-process with Playwright) |
| **Beelink CPU Impact** | Negligible (<5% CPU) | High (screenshot encoding + DOM serialization) | **Very High** (container services + rendering) | Low (DOM-based, no constant screenshotting) | Low (CDP accessibility tree snapshot) |
| **Token Cost / App** | **$0.00** | $0.15 – $0.35 (vision + DOM per step) | $0.20 – $0.50 (full-page multimodal loops) | **$0.01 – $0.03** (first run); **$0.00** (cached replay) | **$0.01 – $0.02** (single prompt with form fields) |
| **Action Containment** | Absolute (code-defined) | Weak (instructed via prompt not to click submit) | Weak (instructed via task prompt) | High (isolated `act()` / `extract()` calls) | **Absolute** (schema has `fill`/`select` tools, NO `submit`) |
| **Ashby/React Widgets** | Poor (fails on custom comboboxes) | Moderate (clicks visually) | High (visual perception) | **Very High** (synthesizes clicks on custom comboboxes) | High (maps ARIA comboboxes and options) |
| **Selector Caching** | Manual / Static | None | None | **Native (stores working DOM selectors)** | Cacheable in DB via field label hash |

#### Recommendation:
- **Drop Skyvern from the local Beelink plan.** The operational overhead of Docker, a second PostgreSQL database, and a FastAPI server on a 16GB box is unjustified when 90% of jobs are guest forms on Greenhouse, Lever, or Ashby.
- **Pivot the evaluation to Stagehand vs. In-Tree Constrained Schema (`ats_agent.py`).**
  - Use Stagehand when Playwright hits custom React comboboxes or multi-select chips (e.g. Ashby’s custom dropdowns).
  - Use the in-tree `ats_agent.py` pattern (from the Sept 17 Beelink spec): pass the page's accessibility tree to Claude with tools `fill_field(label, value)` and `select_option(label, value)`. **No click tool, no submit tool.** This provides 100% containment without third-party library instability.

---

### 2. Authentication, Session Storage, and Email OTP

#### A. Guest Applications vs. Multi-Tenant Accounts
- **Greenhouse, Lever, and Ashby**: ~90% of applications are guest submissions. They require no user account, only candidate details and resume upload.
- **Workday, SmartRecruiters, Taleo**: Require accounts, but **each company is an isolated tenant** (e.g., `apple.wd5.myworkdayjobs.com` vs. `target.wd5.myworkdayjobs.com`). You do not have a single "Workday session"; you have dozens of tenant accounts.

#### B. Storage Mechanism: Domain-Scoped `storage_state.json` vs. Monolithic `--user-data-dir`
- If you run all ATS automation inside one shared Chrome `--user-data-dir`, you risk Chrome `SingletonLock` crashes when running concurrent workers or tests, as well as cookie contamination.
- **Better Alternative**: Use Playwright's native `context.storage_state(path=f"sessions/{tenant_domain}.json")`:
  - When opening an ATS job, check if `sessions/{tenant_domain}.json` exists.
  - If it exists, launch an isolated context with `storage_state=...`.
  - If the session has expired, trigger re-authentication or prompt the user.

#### C. Automated OTP / Magic Link Interception via Existing `gmail.py`
The plan currently treats email verification as an unexpected event that pauses for human takeover. However:
1. Ashby and Workday frequently send a 6-digit code or magic link to verify the applicant's email address.
2. The repository already has a functioning Gmail API and IMAP integration in `gmail.py`.
3. **Automated Interceptor Flow**:
   - When the worker encounters an "Enter verification code sent to your email" step, it enters an internal 60-second polling state.
   - It calls `gmail.search_threads(query='newer_than:2m (from:ashbyhq.com OR from:workday.com OR "verification code")')`.
   - Extracts the 6-digit code using `re.search(r'\b\d{6}\b', body)`.
   - Automatically fills the input field and continues.
   - Only if no email arrives within 60 seconds does it escalate to `waiting_for_user` with a VNC Takeover link.

---

### 3. State Machine & Database Decoupling

In the current codebase, `job_applications.stage` mixes recruiting status with automation execution:
```sql
-- Current: stage is overloaded
stage IN ('saved', 'ready_to_submit', 'applied', 'phone_screen', 'onsite', ...)
```
This overloading led directly to:
- PATCH route rejecting `ready_to_submit` because it wasn't in `JOB_APPLICATION_STAGES`.
- Failed submissions or timeouts leaving rows in inconsistent states.

#### Recommended Separation of Concerns:
Add dedicated automation tracking columns to `job_applications`:

```sql
-- 1. Recruiting Pipeline Status (business milestone)
pipeline_stage TEXT NOT NULL DEFAULT 'saved' 
  CHECK (pipeline_stage IN ('saved', 'applied', 'phone_screen', 'onsite', 'offer', 'rejected', 'archived')),

-- 2. Automation Execution Status (worker lifecycle)
automation_status TEXT NOT NULL DEFAULT 'idle'
  CHECK (automation_status IN (
    'idle',
    'preparing',
    'needs_input',         -- Blocked on CAPTCHA or missing profile fact
    'ready_for_review',    -- Form preview generated, waiting for human approval
    'approved',            -- Approved by human, queued for submit worker
    'submitting',          -- Worker has acquired lease and is executing
    'submitted',           -- Successfully submitted with confirmation evidence
    'needs_confirmation',  -- Submission clicked but confirmation ambiguous / crashed
    'failed_terminal',     -- Cannot proceed (e.g. expired job, unsupported platform)
    'failed_retryable'     -- Network blip, worker crash before submit click
  )),

-- 3. Immutability & Concurrency Guarding
preview_revision_hash TEXT NULL,       -- SHA256(answers + resume_hash + candidate_facts)
approved_revision_hash TEXT NULL,      -- Set atomically by RPC during approval
worker_lease_id UUID NULL,             -- Active worker claim
worker_heartbeat_at TIMESTAMPTZ NULL   -- Allows auto-recovery of abandoned leases
```

---

### 4. Candidate Grounding & Screening Prompts

Merge Review Finding #3 identified:
- In `apply_agent.py:79`, `profile_summary=job.get("role", "")` was feeding the *job title* (e.g. "Senior Product Manager") into the prompt as the candidate's personal experience.
- In `job_pick.py:106`, `_llm_judge` was scoring fit against a real candidate while only passing the company, role, and job description, with zero candidate resume data.

#### Solution:
Create a single, authoritative `candidate_facts` profile in Postgres/Supabase (or `applicant_profile.json`):
```json
{
  "full_name": "Kishore Theeraj Vasudevan Jaya",
  "contact": { "email": "kishoretheeraj@gmail.com", "phone": "+1 603-322-0535", "location": "Hanover, NH" },
  "links": { "linkedin": "linkedin.com/in/kishoretheeraj", "github": "..." },
  "work_authorization": {
    "us_authorized": "Yes",
    "requires_sponsorship": "No",
    "visa_status": "F1-OPT"
  },
  "eeo": {
    "gender": "Male",
    "race_ethnicity": "Asian",
    "veteran_status": "No",
    "disability_status": "No"
  },
  "experience_facts": [
    "Over 4 years of software engineering experience in Python, TypeScript, and distributed systems...",
    "Built automated pipeline processing 50k+ daily records with Supabase and Playwright..."
  ]
}
```
- In `_generate_screening_answers`: Feed `candidate_facts["experience_facts"]`. If Claude cannot find facts supporting an answer with high confidence, set `answers[question] = None` and transition to `needs_input` so the user can answer it in the UI, rather than hallucinating answers.
- In `job_pick.py`: Feed candidate highlights directly into `_JUDGE_PROMPT`.

---

### 5. Verifiable Approval & Anti-Duplication Protocol

To eliminate duplicate applications and race conditions:

#### Pre-Submit Form Drift Check
1. When generating the preview, compute a hash of all found form field names and types:
   $$\text{form\_signature} = \text{SHA256}(\text{sorted}(\text{field\_names}))$$
2. When the submit worker reopens the page after human approval:
   - Recompute the page's current $\text{form\_signature}$.
   - If the employer added a new mandatory field or closed the role, **abort submission**, update `automation_status = 'needs_input'`, record `apply_blocked_reason = 'Form changed after approval'`, and notify the user.

#### Two-Phase Submission & Automatic Confirmation Reconciler
If a worker crashes or network cuts out right after clicking `Submit Application`:
1. The status transitions to `needs_confirmation` (never `failed_retryable`).
2. **Automated Reconciler Job**:
   - ATS platforms (Greenhouse, Lever, Ashby, Workday) almost universally dispatch an automated email receipt within 60–120 seconds (`Subject: "Thank you for applying to [Company]"`).
   - The reconciler polls Gmail via `gmail.py` for incoming messages matching the company name.
   - If found: saves the email `Message-ID` into `submission_evidence` and marks `submitted`.
   - If not found after 15 minutes: alerts the user to check the portal manually via VNC before retrying. **Blind retries are permanently blocked.**

---

### 6. Hardware & Concurrency Optimization on the Beelink

The Beelink (16GB RAM, quad-core low-power CPU, e.g. Intel N100) has distinct resource boundaries:
- **PyTorch Contention**: `job_pick.py:129` runs `sentence_transformers` embeddings, which utilizes 100% of all 4 CPU cores during scoring.
- **Rendering & Screen Capture**: Capturing full-screen X11 screenshots for vision models saturates the CPU during PNG compression.

#### Architecture Fix:
1. **Headless Playwright by Default**: Run ATS form preparation in **Headless Chromium**. Headless Playwright consumes ~60% less CPU and RAM than headful Chrome + Xvfb + x11vnc.
2. **On-Demand VNC**: Only launch the Xvfb/VNC display slot if an application actually reaches `needs_input` (e.g. CAPTCHA detected or manual login needed).
3. **Execution Locks / Mutex**:
   - Enforce via systemd or an advisory lock that `job-pick.service` (PyTorch) and `job-apply.service` (Chromium) never run simultaneously.

---

### 7. The Assistant Layer (Hermes & OpenClaw)

The plan recommends keeping Hermes and OpenClaw optional. **This is the correct decision.**
- **Why**: Conversational frameworks lack deterministic state machines, reliable transactions, and database lease management. Using an LLM chat loop as the application orchestrator leads to lost state, prompt drift, and missed errors.
- **The Ideal Interface**:
  Expose a clean, typed REST/CLI API on the engine:
  - `POST /api/applications/:id/prepare`
  - `GET /api/applications/:id/status`
  - `POST /api/applications/:id/approve`
  - `POST /api/applications/:id/resume`
  If you later connect OpenClaw (for Telegram/WhatsApp notifications) or Hermes (for research), they simply invoke these typed endpoints as thin clients.

---

### Suggested Amendments to Implementation Phases

| Phase | Original Plan | Recommended Amendment | Rationale |
| :--- | :--- | :--- | :--- |
| **Phase 0** | Fix merge review defects | **Same, plus Decouple DB Schema**: Separate `pipeline_stage` from `automation_status` immediately. | Prevents fixing patches on an overloaded `stage` column that will need re-migration. |
| **Phase 1** | Persistent ATS browser & account mapping | **Domain-Scoped `storage_state.json` + Gmail OTP Interceptor**: Auto-fill 6-digit codes via `gmail.py`. | Removes human intervention for 90% of email verification flows. |
| **Phase 2** | Repair Browser-Use vs. Skyvern benchmark | **Replace with Stagehand vs. In-Tree Constrained Schema (`ats_agent.py`)**: Drop Skyvern. | Eliminates Docker/Postgres bloat on the Beelink; benchmarks tools actually suited for ATS forms. |
| **Phase 3** | Form validation & evidence capture | **Form Drift Hash Check + Gmail Receipt Reconciler**: Pre-submit hash check, post-submit receipt polling. | Guarantees zero duplicate submissions and automatic recovery from post-click browser crashes. |
| **Phase 4** | Pilot rollout (10 → 50 apps) | **Same, with Headless-by-Default Execution**: VNC launched on-demand only for takeover. | Conserves CPU/RAM on the Beelink during unattended batch preparation. |
