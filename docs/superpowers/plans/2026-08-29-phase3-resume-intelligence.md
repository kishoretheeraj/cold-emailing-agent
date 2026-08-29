# Phase 3 — Resume Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **For the hourly auto-continue workflow:** this plan needs `RESUME_STORAGE_BUCKET` to exist in the
> linked Supabase project (Task 2) and needs LibreOffice (`soffice`) installed to smoke-test PDF
> conversion for real (Task 9's live check). Both are things `build-continue.yml`'s unattended
> environment cannot guarantee. Implement every task's code + mocked tests normally — they never
> touch real Supabase or a real `soffice` binary — but skip any step explicitly marked "live smoke
> test" and leave it unchecked with a `<!-- blocked: ... -->` note, per your own instructions on
> capability gaps.

**Goal:** Build the full resume + cover-letter generation pipeline for a specific
`job_applications` row: propose a strategy, get human approval, build a one-page DOCX/PDF resume
and cover letter, lint them against the rules distilled from 30 real prior sessions, scrub file
metadata, and upload to Supabase Storage. No auto-submit — output is a file the user reviews and
applies with themselves.

**Architecture:** A new `resume/` subpackage (`resume_agent.py` orchestrator,
`resume_lint.py`/`resume_build.py`/`resume_scrub.py` as pure/deterministic modules) plus
git-versioned reference data (`resume/data/*.json`) transcribed from the user's own
`RESUME_AGENT_SPEC.md`. Two-command CLI (`--propose` then `--build`) with a hard approval gate
between them — the strategy step must be reviewed by a human before any document gets built.

**Tech Stack:** Python 3.11 / pytest / pytest-mock, `python-docx` for DOCX generation, LibreOffice
headless (`soffice`, external system binary) for PDF conversion, `pikepdf` for PDF metadata,
`pypdf` for page counting. All mocked at the subprocess/library boundary in tests — no test in this
plan touches a real Supabase project, a real `soffice` binary, or the Anthropic API.

**Spec:** `docs/superpowers/specs/2026-08-29-phase3-resume-intelligence-design.md` (this plan's
design doc — read it first, especially the "Rejected, not deferred: AI-detection evasion" section).
Source data: `/Users/kishoretheeraj/Downloads/RESUME_AGENT_SPEC.md` (the user's 620-line corpus
spec — referenced throughout as "the corpus spec").

## Global Constraints

- Python: no type annotations, no docstrings on `_prefixed` helpers, public functions get a
  one-line docstring, `f""` strings for log lines, `# ── Section ──...` banners for file sections.
- `resume_agent.py` is manual-only, never in cron: `logging.basicConfig` goes inside
  `if __name__ == "__main__":`, following `jobright.py`'s exact shape (own `.log` file,
  `%(asctime)s | %(message)s` format, `%Y-%m-%d %H:%M` datefmt). New log marker: `[RESUME]`, own
  log file `resume_agent.log`.
- `resume_lint.py`, `resume_build.py`, and `resume_scrub.py` are pure/deterministic modules with
  **no logging of their own and no swallowed exceptions** — unlike `ats.py`/`jobright.py`'s
  best-effort "never raises" posture (which exists because those enrich an unattended background
  run), this pipeline is manual and interactive. A failure must surface loudly (a real exception,
  non-zero exit) so the user sees it immediately, not degrade to an empty result. Only
  `resume_agent.py`'s orchestration layer logs; the lower modules just raise.
- Migrations are additive only: `IF NOT EXISTS`, nullable columns, no backfill, no destructive
  change to any existing table or row.
- `data/*.json` content is **transcribed directly from the corpus spec's Parts 4–9**, not
  fabricated or reworded. The three known metric conflicts (vendor cost eliminated, business loss
  prevented, build-vs-buy horizon) get `"resolved": null` explicitly — never silently pick one.
- New dependencies added to `requirements.txt`: `python-docx`, `pikepdf`, `pypdf`. LibreOffice
  (`soffice` binary) is a separate **system** dependency (not pip-installable) — required for
  `resume_build.py`'s PDF conversion to work for real. Every test in this plan mocks the
  `subprocess` call, so CI and the test suite never need it installed; a live smoke test (Task 9)
  does need it (`brew install --cask libreoffice` on macOS).
- Definition of done (root CLAUDE.md): every task ends with tests green (`python3 -m pytest`), then
  (on the last task) `CLAUDE.md` and memory updated, before the final commit.

---

## Task 1: Migration — `job_applications` resume-tracking columns

**Files:**
- Create: `supabase/migrations/20260829000000_add_resume_columns_to_job_applications.sql`

**Interfaces:**
- Produces: seven new nullable columns on `job_applications`. Task 5's `db.py` accessors write to
  `resume_strategy`, `resume_file_ref`, `cover_letter_file_ref`, `resume_variant`. `source_channel`,
  `response_date`, `outcome` are for manual/future use — not written by this plan's code, but the
  schema exists now so a later phase or manual entry doesn't need another migration.

- [ ] **Step 1: Write the migration**

```sql
-- Phase 3 (resume intelligence) -- job_applications gets resume/cover-letter tracking columns.
--
-- resume_strategy: stage-4 output (section order, projects chosen, CL angle, named gaps) --
--   written by `resume_agent.py --propose`, reviewed by a human before any build happens.
-- resume_file_ref / cover_letter_file_ref: Supabase Storage paths for the built DOCX/PDF --
--   written by `resume_agent.py --build`.
-- resume_variant: which data snapshot/section-choices produced this build (traceability).
-- source_channel / response_date / outcome: outcome-tracking columns the corpus spec's own
--   analysis flagged as the single biggest gap across 30+ manually-tracked applications --
--   schema exists now for manual or future-phase use, not written by this plan's code.
--
-- Additive only: all nullable, no backfill, no destructive change to any existing row. See
-- docs/superpowers/specs/2026-08-29-phase3-resume-intelligence-design.md.

ALTER TABLE job_applications
  ADD COLUMN IF NOT EXISTS resume_strategy JSONB,
  ADD COLUMN IF NOT EXISTS resume_file_ref TEXT,
  ADD COLUMN IF NOT EXISTS cover_letter_file_ref TEXT,
  ADD COLUMN IF NOT EXISTS resume_variant TEXT,
  ADD COLUMN IF NOT EXISTS source_channel TEXT,
  ADD COLUMN IF NOT EXISTS response_date DATE,
  ADD COLUMN IF NOT EXISTS outcome TEXT;
```

- [ ] **Step 2: Push the migration**

Run: `supabase db push`
Expected: migration applies cleanly with no errors (additive-only, all nullable, no data at risk).

- [ ] **Step 3: Verify the columns exist**

Run: `supabase db query --linked "select column_name from information_schema.columns where table_name = 'job_applications' order by column_name;"`
Expected: includes all seven new column names alongside the existing `company`/`role`/`stage`/etc.

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/20260829000000_add_resume_columns_to_job_applications.sql
git commit -m "feat: add resume-tracking columns to job_applications"
```

---

## Task 2: Migration — Supabase Storage bucket for resumes

**Files:**
- Create: `supabase/migrations/20260829000001_create_resumes_storage_bucket.sql`

**Interfaces:**
- Produces: a private Storage bucket named `resumes`. Task 5's `db.upload_resume_file` writes into
  it via `get_client().storage.from_(config.RESUME_STORAGE_BUCKET)`.

- [ ] **Step 1: Write the migration**

```sql
-- Phase 3 (resume intelligence) -- Storage bucket for generated resume/cover-letter files.
-- Private (public=false) -- these are personal application documents, not shared links.
--
-- Every other table in this project runs with RLS disabled (see draft_history's migration
-- comment) since this repo authenticates with a single anon key and has no separate
-- service-role credential anywhere in the stack. Storage always enforces RLS-style policies on
-- storage.objects (there is no bucket-level "disable RLS" toggle), so these three policies grant
-- the anon key the same full read/write access on this one bucket that it already has on every
-- table -- scoped to bucket_id = 'resumes' only, not every bucket.

insert into storage.buckets (id, name, public)
values ('resumes', 'resumes', false)
on conflict (id) do nothing;

create policy "resumes bucket -- anon read" on storage.objects
  for select using (bucket_id = 'resumes');

create policy "resumes bucket -- anon write" on storage.objects
  for insert with check (bucket_id = 'resumes');

create policy "resumes bucket -- anon update" on storage.objects
  for update using (bucket_id = 'resumes');
```

- [ ] **Step 2: Push the migration**

Run: `supabase db push`
Expected: migration applies cleanly.

- [ ] **Step 3: Verify the bucket exists**

Run: `supabase db query --linked "select id, public from storage.buckets where id = 'resumes';"`
Expected: one row, `public = false`.

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/20260829000001_create_resumes_storage_bucket.sql
git commit -m "feat: create resumes Storage bucket with anon-key policies"
```

---

## Task 3: `requirements.txt` and `config.py` — new dependencies and constants

**Files:**
- Modify: `requirements.txt`
- Modify: `config.py`

**Interfaces:**
- Produces: `config.RESUME_STORAGE_BUCKET`, `config.RESUME_MODEL`, `config.RESUME_SOFFICE_TIMEOUT_SECONDS`,
  `config.RESUME_COVER_LETTER_MAX_WORDS`, `config.RESUME_MAX_BUILD_RETRIES`. Consumed by every
  later task in this plan.

- [ ] **Step 1: Add dependencies**

Append to `requirements.txt`:

```
python-docx>=1.1.0
pikepdf>=9.0.0
pypdf>=4.3.0
```

- [ ] **Step 2: Install locally**

Run: `source .venv/bin/activate && pip install -r requirements.txt`
Expected: all three packages install cleanly.

- [ ] **Step 3: Add config constants**

At the end of `config.py`, add a new section:

```python
# ── Resume intelligence (Phase 3, full-fledged buildout) ────────────────────────

RESUME_STORAGE_BUCKET = "resumes"
RESUME_MODEL = EMAIL_MODEL
RESUME_SOFFICE_TIMEOUT_SECONDS = 30
RESUME_COVER_LETTER_MAX_WORDS = 300
# preflight.py's own pattern: one automatic regeneration on a lint failure, then give up loudly.
RESUME_MAX_BUILD_RETRIES = 1
```

- [ ] **Step 4: Commit**

```bash
git add requirements.txt config.py
git commit -m "feat: add resume pipeline dependencies and config constants"
```

(No test file for this step alone — these are covered by the tests that consume them starting
Task 6.)

---

## Task 4: `resume/data/*.json` — seed reference data from the corpus spec

**Files:**
- Create: `resume/__init__.py` (empty)
- Create: `resume/data/master.json`
- Create: `resume/data/metrics.json`
- Create: `resume/data/jargon.json`
- Create: `resume/data/projects.json`
- Create: `resume/data/skills.json`
- Create: `resume/data/moments.json`
- Test: `tests/test_resume_data.py`

**Interfaces:**
- Produces: six JSON files, each loadable via `json.load`. Task 6/7's `resume_lint.py` consumes
  `metrics.json` and `jargon.json`. Task 8's `resume_build.py` consumes `master.json`,
  `projects.json`, and `skills.json`. Task 11's `resume_agent.py` consumes `moments.json` (cover
  letter stage) and all the others as prompt context.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resume_data.py`:

```python
"""Structural validation for resume/data/*.json -- these are content files, not code, so the
tests check shape and the metric-conflict invariant rather than business logic."""

import json
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "resume" / "data"


def _load(name):
    with open(_DATA_DIR / name) as f:
        return json.load(f)


def test_all_data_files_are_valid_json():
    for name in ("master.json", "metrics.json", "jargon.json", "projects.json",
                 "skills.json", "moments.json"):
        _load(name)  # raises if malformed


def test_metrics_have_required_fields():
    metrics = _load("metrics.json")
    assert len(metrics) > 0
    for m in metrics:
        assert set(m.keys()) >= {"id", "role", "text", "resolved", "conflicting_values"}
        assert isinstance(m["conflicting_values"], list)


def test_exactly_three_known_metric_conflicts_are_marked_unresolved():
    metrics = _load("metrics.json")
    unresolved = [m for m in metrics if m["resolved"] is None]
    assert len(unresolved) == 3
    ids = {m["id"] for m in unresolved}
    assert ids == {
        "protium_vendor_cost_eliminated",
        "product_analyst_ux_business_loss_prevented",
        "protium_build_vs_buy_horizon",
    }


def test_jargon_is_a_flat_banned_to_allowed_mapping():
    jargon = _load("jargon.json")
    assert len(jargon) >= 15
    for banned, allowed in jargon.items():
        assert isinstance(banned, str) and isinstance(allowed, str)


def test_projects_matrix_covers_every_role_type():
    projects = _load("projects.json")
    expected_role_types = {
        "ai_automation_ml", "pricing_strategy", "operations_manufacturing_cpg",
        "pure_data_analytics", "finance_investment_adjacent", "hardware_semiconductor_ip",
        "product_management_generalist", "consulting", "no_jd_generalist",
    }
    assert set(projects.keys()) == expected_role_types


def test_moments_bank_has_insight_and_used_for_fields():
    moments = _load("moments.json")
    assert len(moments) >= 8
    for m in moments:
        assert set(m.keys()) >= {"moment", "insight", "used_for"}


def test_skills_has_spine_pool_and_banned():
    skills = _load("skills.json")
    assert set(skills.keys()) == {"spine", "swap_pool", "banned", "flagged_unbacked"}
    assert "Tableau" in skills["banned"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_resume_data.py -v`
Expected: FAIL — `resume/data/` doesn't exist yet (`FileNotFoundError`).

- [ ] **Step 3: Create the package and data files**

Create `resume/__init__.py` (empty file).

Create `resume/data/metrics.json` (transcribed from the corpus spec's Part 4):

```json
[
  {"id": "protium_vendor_cost_eliminated", "role": "Associate Product Manager, Protium Finance (Apr 2025 - Aug 2025)", "text": "$20K/year vendor cost eliminated, from a 3-year build vs buy model on risk monitoring", "resolved": null, "conflicting_values": ["$120K/year (Faros draft)", "$200K/year (NiCE draft)"]},
  {"id": "protium_build_vs_buy_horizon", "role": "Associate Product Manager, Protium Finance (Apr 2025 - Aug 2025)", "text": "3-year build vs buy model horizon on risk monitoring", "resolved": null, "conflicting_values": ["5-year (Faros upload)"]},
  {"id": "protium_audit_hours_saved", "role": "Associate Product Manager, Protium Finance (Apr 2025 - Aug 2025)", "text": "110 verified hours/month saved, audit centralization across 90+ branches", "resolved": true, "conflicting_values": []},
  {"id": "protium_stakeholder_interviews", "role": "Associate Product Manager, Protium Finance (Apr 2025 - Aug 2025)", "text": "25+ stakeholder interviews", "resolved": true, "conflicting_values": []},
  {"id": "protium_b2b_api_revenue", "role": "Associate Product Manager, Protium Finance (Apr 2025 - Aug 2025)", "text": "$120K/year projected B2B API revenue opportunity", "resolved": true, "conflicting_values": []},
  {"id": "spa_loan_processing_time", "role": "Senior Product Analyst (Apr 2024 - Apr 2025)", "text": "32% loan processing time reduction", "resolved": true, "conflicting_values": []},
  {"id": "spa_ai_ingestion_adoption", "role": "Senior Product Analyst (Apr 2024 - Apr 2025)", "text": "AI ingestion adoption 1.6% to 65% in one month", "resolved": true, "conflicting_values": []},
  {"id": "spa_document_cost_reduction", "role": "Senior Product Analyst (Apr 2024 - Apr 2025)", "text": "72% reduction in document processing cost and carbon", "resolved": true, "conflicting_values": []},
  {"id": "spa_interest_income", "role": "Senior Product Analyst (Apr 2024 - Apr 2025)", "text": "$40K/year projected interest income from e-signature and identity verification", "resolved": true, "conflicting_values": []},
  {"id": "spa_loan_portfolio_influenced", "role": "Senior Product Analyst (Apr 2024 - Apr 2025)", "text": "$480M+ loan portfolio influenced", "resolved": true, "conflicting_values": []},
  {"id": "product_analyst_ux_business_loss_prevented", "role": "Product Analyst, UX (Sep 2022 - Mar 2024)", "text": "$200K/month business loss prevented, via 20% customer drop-off reduction", "resolved": null, "conflicting_values": ["$10K/month (Faros upload)"]},
  {"id": "product_analyst_ux_approval_rate", "role": "Product Analyst, UX (Sep 2022 - Mar 2024)", "text": "36% increase in partner-bank approval rates", "resolved": true, "conflicting_values": []},
  {"id": "product_analyst_ux_dashboards", "role": "Product Analyst, UX (Sep 2022 - Mar 2024)", "text": "12+ dashboards in Metabase and SQL", "resolved": true, "conflicting_values": []},
  {"id": "product_analyst_ux_compliance_reduction", "role": "Product Analyst, UX (Sep 2022 - Mar 2024)", "text": "22% quarter over quarter reduction in compliance issues", "resolved": true, "conflicting_values": []},
  {"id": "product_analyst_ux_emi_success", "role": "Product Analyst, UX (Sep 2022 - Mar 2024)", "text": "12% EMI payment success improvement via smart routing (Cards, UPI e-mandate, NACH)", "resolved": true, "conflicting_values": []},
  {"id": "intern_reporting_hours_saved", "role": "Product Intern (Apr 2022 - Sep 2022)", "text": "20+ hours/week saved, regulator reporting automation", "resolved": true, "conflicting_values": []},
  {"id": "intern_pin_codes_mapped", "role": "Product Intern (Apr 2022 - Sep 2022)", "text": "19,000+ postal pin codes mapped via Python scraper", "resolved": true, "conflicting_values": []},
  {"id": "intern_bug_escalation_reduction", "role": "Product Intern (Apr 2022 - Sep 2022)", "text": "40% bug escalation reduction via RCA (fishbone, 5-Whys)", "resolved": true, "conflicting_values": []},
  {"id": "intern_identity_verification_speedup", "role": "Product Intern (Apr 2022 - Sep 2022)", "text": "30x identity verification processing speedup", "resolved": true, "conflicting_values": []},
  {"id": "viant_vendor_narrowing", "role": "Dartmouth project -- Viant Medical", "text": "7 vendors narrowed to 2 finalists using a Stage-Gate process and a Pugh matrix; Zebra Technologies may be named as a finalist", "resolved": true, "conflicting_values": []},
  {"id": "viant_savings", "role": "Dartmouth project -- Viant Medical", "text": "$150K investment; $207,600 savings per line; 9-month payback; 138% year-one ROI; $2.15M discounted savings across 6 lines", "resolved": true, "conflicting_values": []},
  {"id": "hiya_users_protected", "role": "Dartmouth project -- Hiya Inc", "text": "500M+ users protected", "resolved": true, "conflicting_values": []},
  {"id": "hiya_labeling_agreement", "role": "Dartmouth project -- Hiya Inc", "text": "Cohen's Kappa 1.000 on 525 voice files across 3 labelers; context kappa 0.71 to 0.84; 6 synthesis bands; three-tier protocol (calibration rows 1-20, overlap 21-70, unique 71+); labelers Kishore, Rineetha, Tanya", "resolved": true, "conflicting_values": []},
  {"id": "iso_ne_forecast", "role": "Dartmouth project -- ISO New England", "text": "96-hour forecast using Random Forest, SARIMAX, MLP", "resolved": true, "conflicting_values": []},
  {"id": "universal_rental_car_profit", "role": "Dartmouth project -- Universal Rental Car (Tuck pricing capstone)", "text": "simulated profit $31.2M to $37.9M (+21%); projected $42M profit on $490M revenue FY27", "resolved": true, "conflicting_values": []},
  {"id": "personal_portfolio_return", "role": "Personal", "text": "51.73% absolute return, 25.87% XIRR, self-directed equity portfolio", "resolved": true, "conflicting_values": []},
  {"id": "personal_pinnacle_app", "role": "Personal", "text": "Pinnacle App: live on Google Play, team of 3", "resolved": true, "conflicting_values": []},
  {"id": "personal_madras_defense_lms", "role": "Personal", "text": "Madras Defense Academy LMS: team of 6, $10K revenue", "resolved": true, "conflicting_values": []},
  {"id": "personal_unschool_mentoring", "role": "Personal", "text": "500+ students mentored via Unschool (2,000+ mentees reached); LinkedIn Top 10 Indian Startup 2020", "resolved": true, "conflicting_values": []},
  {"id": "personal_tiktok_audience_fit", "role": "Personal", "text": "TikTok Audience Fit: shipped in 2 days, live at tiktok-audience-fit.lovable.app", "resolved": true, "conflicting_values": []}
]
```

Create `resume/data/jargon.json` (transcribed from the corpus spec's Part 5):

```json
{
  "NBFI": "digital lending company",
  "non-banking financial institution": "digital lending company",
  "loan TAT": "processing time",
  "turnaround time": "processing time",
  "user churn": "customer drop-off",
  "lending journey": "customer journey",
  "customer funnel analysis": "customer journey analysis",
  "NPA": "defaults",
  "disbursement volume": "volume",
  "policy deviations": "exceptions",
  "compliance escalations": "compliance issues",
  "AI-driven loan ingestion platform": "AI review tool",
  "build vs buy financial model": "cost-benefit model comparing buying vs building",
  "co-lending": "partner-bank channels",
  "NACH": "paperless rollout, digital documentation",
  "e-mandate": "paperless rollout, digital documentation",
  "UPI autopay": "paperless rollout, digital documentation",
  "watchlists": "free public data sources",
  "sanctions screening": "free public data sources",
  "XIRR": "annualized return",
  "sprint planning and retrospectives": "planning and review meetings",
  "eKYC": "identity verification",
  "Video KYC": "identity verification",
  "MEM": "Dartmouth Master's (Tuck/Thayer)"
}
```

Create `resume/data/projects.json` (transcribed from the corpus spec's Part 6 project-selection
matrix -- key names are snake_case role-type identifiers):

```json
{
  "ai_automation_ml": {"lead": "Cold Outreach Agent or Hiya", "second": "TikTok Audience Fit", "third": "Viant", "drop": ["ISO New England", "Pinnacle"]},
  "pricing_strategy": {"lead": "Universal Rental Car", "second": "Viant", "third": null, "drop": ["Hiya", "ISO New England", "TikTok Audience Fit"]},
  "operations_manufacturing_cpg": {"lead": "Viant (2-bullet version)", "second": "ISO New England", "third": null, "drop": ["Hiya", "TikTok Audience Fit", "Cold Outreach Agent"]},
  "pure_data_analytics": {"lead": "ISO New England", "second": "Hiya", "third": "Viant", "drop": ["TikTok Audience Fit", "Pinnacle"]},
  "finance_investment_adjacent": {"lead": "Personal portfolio", "second": "Viant financial model", "third": "ISO New England", "drop": ["Cold Outreach Agent", "Pinnacle"]},
  "hardware_semiconductor_ip": {"lead": "Viant (vendor eval)", "second": "Hiya (production ML)", "third": "TikTok Audience Fit", "drop": ["Pinnacle", "ISO New England"]},
  "product_management_generalist": {"lead": "Hiya", "second": "TikTok Audience Fit", "third": "Viant", "drop": []},
  "consulting": {"lead": "Universal Rental Car", "second": "Viant", "third": null, "drop": ["all AI projects"]},
  "no_jd_generalist": {"lead": "Hiya", "second": "Viant", "third": "ISO New England", "drop": [], "note": "pick for spread, not depth"}
}
```

Create `resume/data/skills.json` (transcribed from the corpus spec's Part 8):

```json
{
  "spine": ["Product Roadmap", "PRDs", "Agile/Scrum", "SQL", "Figma", "Jira"],
  "swap_pool": ["Metabase", "Power BI", "Python", "Excel (advanced)", "Confluence", "Miro", "Claude", "Cursor", "Claude Code", "Lovable"],
  "banned": ["Tableau", "v0", "Replit"],
  "flagged_unbacked": ["GTM"]
}
```

Create `resume/data/moments.json` (transcribed from the corpus spec's Part 9 moment bank):

```json
[
  {"moment": "Row 4 of a labeling rubric, two labelers disagreed", "insight": "Most labeler error is rubric debt, not labeler error. More labels is almost never the fix", "used_for": "Centific"},
  {"moment": "The outreach agent sent a draft with the wrong company name", "insight": "The fix wasn't more regex, it was specifying upstream what an acceptable draft looks like", "used_for": "Photon"},
  {"moment": "Blind-listen review found confidently wrong classifier outputs", "insight": "Confidence and correctness are independent axes", "used_for": "Pindrop"},
  {"moment": "Three months on aggregate pricing models before noticing weekday and weekend were two different markets", "insight": "Most pricing work succeeds or quietly fails at the segment definition step", "used_for": "Cott"},
  {"moment": "A 5-minute digital loan flow next to a 4-hour assisted flow", "insight": "Not a backend problem, a handshake problem between three surfaces", "used_for": "ASSA ABLOY"},
  {"moment": "An hourly sync and an audit checkpoint living in someone's inbox", "insight": "Friction lives at the seams, not in missing features", "used_for": "Dedalus"},
  {"moment": "Demand-curve tails and where data centers sit on them", "insight": "Load forecasting failure modes are in the tail, not the mean", "used_for": "Fluence"},
  {"moment": "The 90-office audit, operator pain", "insight": "Self-serve is a diagnosis problem before it is a tooling problem", "used_for": "Glean (planned, never written)"}
]
```

Create `resume/data/master.json` (structured role/bullet data derived from the corpus spec's Part 4
timeline -- `bullet_ids` reference `metrics.json` entries by id, so a role's bullets stay in sync
with the metrics whitelist rather than duplicating the text):

```json
{
  "roles": [
    {
      "title": "Associate Product Manager",
      "company": "Protium Finance",
      "period": "Apr 2025 - Aug 2025",
      "bullet_ids": ["protium_vendor_cost_eliminated", "protium_audit_hours_saved", "protium_stakeholder_interviews", "protium_b2b_api_revenue"]
    },
    {
      "title": "Senior Product Analyst",
      "company": "Protium Finance",
      "period": "Apr 2024 - Apr 2025",
      "bullet_ids": ["spa_loan_processing_time", "spa_ai_ingestion_adoption", "spa_document_cost_reduction", "spa_interest_income", "spa_loan_portfolio_influenced"]
    },
    {
      "title": "Product Analyst, UX",
      "company": "Protium Finance",
      "period": "Sep 2022 - Mar 2024",
      "bullet_ids": ["product_analyst_ux_business_loss_prevented", "product_analyst_ux_approval_rate", "product_analyst_ux_dashboards", "product_analyst_ux_compliance_reduction", "product_analyst_ux_emi_success"]
    },
    {
      "title": "Product Intern",
      "company": "Protium Finance",
      "period": "Apr 2022 - Sep 2022",
      "bullet_ids": ["intern_reporting_hours_saved", "intern_pin_codes_mapped", "intern_bug_escalation_reduction", "intern_identity_verification_speedup"]
    }
  ],
  "education": {
    "institution": "Dartmouth College",
    "program": "Master's, Engineering Management (Tuck/Thayer)",
    "graduation": "Nov 2026 (off-cycle)"
  },
  "projects": {
    "Viant": {"bullet_ids": ["viant_vendor_narrowing", "viant_savings"]},
    "Hiya": {"bullet_ids": ["hiya_users_protected", "hiya_labeling_agreement"]},
    "ISO New England": {"bullet_ids": ["iso_ne_forecast"]},
    "Universal Rental Car": {"bullet_ids": ["universal_rental_car_profit"]},
    "TikTok Audience Fit": {"bullet_ids": ["personal_tiktok_audience_fit"]},
    "Pinnacle": {"bullet_ids": ["personal_pinnacle_app"]},
    "Cold Outreach Agent": {"bullet_ids": [], "naming_note": "never 'cold email agent' or 'job search agent'; approved names: 'AI Email Workflow Agent', 'AI Workflow Assistant', 'Cold Outreach Agent'"}
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_resume_data.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add resume/__init__.py resume/data/ tests/test_resume_data.py
git commit -m "feat: seed resume reference data from the user's corpus spec"
```

---

## Task 5: `db.py` — resume accessors

**Files:**
- Modify: `db.py` (append near the `job_applications` accessors, after `get_job_application`)
- Test: `tests/test_job_applications_db.py` (append)

**Interfaces:**
- Consumes: `get_client()`, `_retry(fn)` (both already in `db.py`), `config.RESUME_STORAGE_BUCKET`
  (Task 3).
- Produces: `set_resume_strategy(application_id, strategy) -> dict | None`,
  `set_resume_files(application_id, resume_file_ref=None, cover_letter_file_ref=None, resume_variant=None) -> dict | None`,
  `upload_resume_file(storage_path, file_bytes, content_type) -> str` (returns `storage_path`,
  raises on failure -- no best-effort swallowing here, per Global Constraints). Consumed by Task 11
  (`--propose`) and Task 12 (`--build`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_job_applications_db.py`:

```python
def test_set_resume_strategy_updates_the_row(fake_client):
    fake_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
        {"id": 1, "resume_strategy": {"angle": "projects-first"}}
    ]
    result = db.set_resume_strategy(1, {"angle": "projects-first"})
    fake_client.table.assert_called_with("job_applications")
    updated = fake_client.table.return_value.update.call_args[0][0]
    assert updated["resume_strategy"] == {"angle": "projects-first"}
    assert result["id"] == 1


def test_set_resume_files_only_sets_provided_fields(fake_client):
    fake_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [{"id": 1}]
    db.set_resume_files(1, resume_file_ref="resumes/1/resume.pdf")
    updated = fake_client.table.return_value.update.call_args[0][0]
    assert updated["resume_file_ref"] == "resumes/1/resume.pdf"
    assert "cover_letter_file_ref" not in updated
    assert "resume_variant" not in updated


def test_set_resume_files_sets_all_fields_when_provided(fake_client):
    fake_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [{"id": 1}]
    db.set_resume_files(1, resume_file_ref="r.pdf", cover_letter_file_ref="cl.pdf", resume_variant="v1")
    updated = fake_client.table.return_value.update.call_args[0][0]
    assert updated["resume_file_ref"] == "r.pdf"
    assert updated["cover_letter_file_ref"] == "cl.pdf"
    assert updated["resume_variant"] == "v1"


def test_upload_resume_file_calls_storage_and_returns_path(fake_client):
    result = db.upload_resume_file("resumes/1/resume.pdf", b"filebytes", "application/pdf")
    fake_client.storage.from_.assert_called_with(config.RESUME_STORAGE_BUCKET)
    fake_client.storage.from_.return_value.upload.assert_called_once()
    args, kwargs = fake_client.storage.from_.return_value.upload.call_args
    assert args[0] == "resumes/1/resume.pdf"
    assert args[1] == b"filebytes"
    assert result == "resumes/1/resume.pdf"


def test_upload_resume_file_raises_on_failure(fake_client):
    fake_client.storage.from_.return_value.upload.side_effect = RuntimeError("storage down")
    with pytest.raises(RuntimeError):
        db.upload_resume_file("resumes/1/resume.pdf", b"x", "application/pdf")
```

Add `import config` to the top of `tests/test_job_applications_db.py` (alongside `import db`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_job_applications_db.py -v`
Expected: the five new tests FAIL with `AttributeError: module 'db' has no attribute
'set_resume_strategy'` (etc.) — the functions don't exist yet.

- [ ] **Step 3: Implement**

Add `import config` near the top of `db.py`, alongside the existing imports.

Append to `db.py`, immediately after `get_job_application`:

```python
def set_resume_strategy(application_id, strategy):
    """Write stage-4 strategy output onto a job_applications row. Builds nothing."""
    result = _retry(lambda: get_client().table("job_applications")
                     .update({"resume_strategy": strategy, "updated_at": datetime.utcnow().isoformat()})
                     .eq("id", application_id).execute())
    return result.data[0] if result.data else None


def set_resume_files(application_id, resume_file_ref=None, cover_letter_file_ref=None, resume_variant=None):
    """Write built-file references onto a job_applications row after a successful build."""
    payload = {"updated_at": datetime.utcnow().isoformat()}
    if resume_file_ref is not None:
        payload["resume_file_ref"] = resume_file_ref
    if cover_letter_file_ref is not None:
        payload["cover_letter_file_ref"] = cover_letter_file_ref
    if resume_variant is not None:
        payload["resume_variant"] = resume_variant
    result = _retry(lambda: get_client().table("job_applications")
                     .update(payload).eq("id", application_id).execute())
    return result.data[0] if result.data else None


def upload_resume_file(storage_path, file_bytes, content_type):
    """Upload a built file to the resumes Storage bucket. Returns storage_path. Raises on failure --
    unlike the rest of this module's best-effort accessors, a failed upload must not look like success."""
    get_client().storage.from_(config.RESUME_STORAGE_BUCKET).upload(
        storage_path, file_bytes, {"content-type": content_type, "upsert": "true"},
    )
    return storage_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_job_applications_db.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full test suite**

Run: `python3 -m pytest`
Expected: all green — confirms adding `import config` to `db.py` doesn't break anything relying on
`db.py`'s current import-time behavior (it shouldn't; `config.py` has no side effects beyond
reading env vars, which every test already sets via `conftest.py`).

- [ ] **Step 6: Commit**

```bash
git add db.py tests/test_job_applications_db.py
git commit -m "feat: add resume strategy/file/upload accessors to db.py"
```

---

## Task 6: `resume_lint.py` — humanizer, jargon, and metrics-whitelist checks

**Files:**
- Create: `resume_lint.py`
- Test: `tests/test_resume_lint.py`

**Interfaces:**
- Produces: `check_em_dashes(text) -> list[str]`, `check_jargon(text, jargon_map) -> list[str]`,
  `_extract_numbers(text) -> list[str]` (private, also used by Task 7's cover-letter checks),
  `check_metrics_whitelist(text, metrics) -> list[str]`. All pure functions: text in, a list of
  violation strings out (empty list = pass). Consumed by Task 12's `--build` orchestration.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resume_lint.py`:

```python
"""Tests for resume_lint.py. Every check is a pure function -- no mocking needed."""

import resume_lint


# ── check_em_dashes ───────────────────────────────────────────────────────────

def test_check_em_dashes_flags_em_dash():
    assert resume_lint.check_em_dashes("Led the team — shipped on time") != []


def test_check_em_dashes_passes_clean_text():
    assert resume_lint.check_em_dashes("Led the team, shipped on time") == []


# ── check_jargon ───────────────────────────────────────────────────────────────

def test_check_jargon_flags_banned_term_case_insensitively():
    jargon_map = {"NBFI": "digital lending company"}
    violations = resume_lint.check_jargon("Worked at an nbfi startup", jargon_map)
    assert len(violations) == 1
    assert "digital lending company" in violations[0]


def test_check_jargon_passes_clean_text():
    jargon_map = {"NBFI": "digital lending company"}
    assert resume_lint.check_jargon("Worked at a digital lending company", jargon_map) == []


# ── _extract_numbers ──────────────────────────────────────────────────────────

def test_extract_numbers_finds_dollar_and_percent_and_multiplier():
    result = resume_lint._extract_numbers("$20K/year, 32% reduction, 30x speedup")
    assert "$20K" in result
    assert "32%" in result
    assert "30x" in result


# ── check_metrics_whitelist ────────────────────────────────────────────────────

_METRICS = [
    {"id": "vendor_cost", "role": "APM", "text": "$20K/year vendor cost eliminated",
     "resolved": None, "conflicting_values": ["$120K/year (draft)"]},
    {"id": "audit_hours", "role": "APM", "text": "110 verified hours/month saved",
     "resolved": True, "conflicting_values": []},
]


def test_check_metrics_whitelist_flags_unresolved_conflict_number():
    violations = resume_lint.check_metrics_whitelist("Eliminated $120K/year in vendor cost", _METRICS)
    assert len(violations) == 1
    assert "vendor_cost" in violations[0]


def test_check_metrics_whitelist_flags_own_text_number_when_unresolved():
    violations = resume_lint.check_metrics_whitelist("Eliminated $20K/year in vendor cost", _METRICS)
    assert len(violations) == 1
    assert "vendor_cost" in violations[0]


def test_check_metrics_whitelist_passes_resolved_metric():
    violations = resume_lint.check_metrics_whitelist("Saved 110 verified hours/month", _METRICS)
    assert violations == []


def test_check_metrics_whitelist_passes_text_with_no_numbers():
    assert resume_lint.check_metrics_whitelist("Led cross-functional collaboration", _METRICS) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_resume_lint.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'resume_lint'`.

- [ ] **Step 3: Implement**

Create `resume_lint.py`:

```python
"""
Deterministic, pure-function lint checks for generated resume/cover-letter text --
distilled from RESUME_AGENT_SPEC.md's Parts 3, 5, and 9 (rules that failed
repeatedly even when stated directly in a prompt, so they became checks instead).

No I/O, no logging, no swallowed exceptions -- these are pure functions, text in,
a list of violation strings out. Empty list means pass. See
docs/superpowers/specs/2026-08-29-phase3-resume-intelligence-design.md.
"""

import re

_NUMBER_PATTERN = re.compile(r"\$?\d[\d,]*(?:\.\d+)?[KMB%x]?", re.IGNORECASE)


# ── Humanizer ──────────────────────────────────────────────────────────────────

def check_em_dashes(text):
    return ["em dash (—) found in text"] if "—" in text else []


# ── Jargon ─────────────────────────────────────────────────────────────────────

def check_jargon(text, jargon_map):
    violations = []
    lowered = text.lower()
    for banned, allowed in jargon_map.items():
        if banned.lower() in lowered:
            violations.append(f"'{banned}' found -- use '{allowed}' instead")
    return violations


# ── Metrics whitelist ──────────────────────────────────────────────────────────

def _extract_numbers(text):
    return _NUMBER_PATTERN.findall(text)


def check_metrics_whitelist(text, metrics):
    violations = []
    candidate_numbers = set(_extract_numbers(text))
    for metric in metrics:
        if metric.get("resolved") is not None:
            continue
        banned_numbers = set(_extract_numbers(metric.get("text", "")))
        for cv in metric.get("conflicting_values", []):
            banned_numbers |= set(_extract_numbers(cv))
        overlap = candidate_numbers & banned_numbers
        if overlap:
            violations.append(
                f"unresolved metric conflict '{metric['id']}': found {sorted(overlap)} -- "
                f"resolve resume/data/metrics.json before this can be used"
            )
    return violations
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_resume_lint.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add resume_lint.py tests/test_resume_lint.py
git commit -m "feat: add resume_lint.py humanizer, jargon, and metrics-whitelist checks"
```

---

## Task 7: `resume_lint.py` — cover-letter violation checks

**Files:**
- Modify: `resume_lint.py` (append)
- Test: `tests/test_resume_lint.py` (append)

**Interfaces:**
- Consumes: `_extract_numbers` (Task 6, same module).
- Produces: `check_cover_letter(cl_text, resume_text) -> list[str]` — runs all six corpus-spec
  Part 9 detectors and returns their combined violations. Consumed by Task 12's `--build`
  orchestration.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_resume_lint.py`:

```python
# ── check_cover_letter ─────────────────────────────────────────────────────────

_RESUME_TEXT = "Reduced loan processing time by 32% through AI-assisted document review."


def test_check_cover_letter_flags_number_shared_with_resume():
    cl = "I drove a 32% improvement in processing efficiency at my last role."
    violations = resume_lint.check_cover_letter(cl, _RESUME_TEXT)
    assert any("32%" in v for v in violations)


def test_check_cover_letter_flags_shared_six_word_phrase():
    cl = "I worked on loan processing time by 32% every single day at work."
    violations = resume_lint.check_cover_letter(cl, _RESUME_TEXT)
    assert any("shared 6-word phrase" in v for v in violations)


def test_check_cover_letter_flags_three_capability_enumeration():
    cl = ("First, I bring analytical rigor. Second, I bring stakeholder empathy. "
          "Third, I bring execution speed. " + "Filler word. " * 30)
    violations = resume_lint.check_cover_letter(cl, "")
    assert any("enumerates exactly three capabilities" in v for v in violations)


def test_check_cover_letter_flags_banned_opener():
    cl = "I am writing to apply for this exciting role at your company. " + "Filler. " * 30
    violations = resume_lint.check_cover_letter(cl, "")
    assert any("banned phrase" in v for v in violations)


def test_check_cover_letter_flags_word_count_over_limit():
    cl = "word " * 301
    violations = resume_lint.check_cover_letter(cl, "")
    assert any("word count" in v for v in violations)


def test_check_cover_letter_flags_closing_hedge_stack():
    cl = "Body sentence here. " + "Filler word. " * 30 + \
         "I might possibly perhaps be a good fit if you think it could work."
    violations = resume_lint.check_cover_letter(cl, "")
    assert any("hedge words" in v for v in violations)


def test_check_cover_letter_passes_clean_letter():
    cl = ("The rubric row where two labelers disagreed taught me that most labeling "
          "error is rubric debt, not labeler error. I'd bring that same instinct to "
          "your team's data quality work. " + "Additional context sentence. " * 20 +
          "I look forward to discussing this further.")
    violations = resume_lint.check_cover_letter(cl, _RESUME_TEXT)
    assert violations == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_resume_lint.py -v`
Expected: the seven new tests FAIL with `AttributeError: module 'resume_lint' has no attribute
'check_cover_letter'`.

- [ ] **Step 3: Implement**

Append to `resume_lint.py`:

```python
# ── Cover letter (corpus spec Part 9) ──────────────────────────────────────────

_BANNED_OPENERS = ("i am writing to apply", "i am excited to apply")
_HEDGE_WORDS = ("might", "could", "perhaps", "possibly", "may", "hopefully", "i believe", "i think", "i hope")


def _ngrams(text, n):
    words = re.findall(r"[A-Za-z0-9']+", text.lower())
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def check_cover_letter_number_overlap(cl_text, resume_text):
    overlap = set(_extract_numbers(cl_text)) & set(_extract_numbers(resume_text))
    return [f"number '{n}' also appears in the resume" for n in sorted(overlap)]


def check_cover_letter_ngram_overlap(cl_text, resume_text, n=6):
    overlap = _ngrams(cl_text, n) & _ngrams(resume_text, n)
    return [f"shared {n}-word phrase: {' '.join(g)}" for g in sorted(overlap)]


def check_cover_letter_capability_enumeration(text):
    lowered = text.lower()
    if all(marker in lowered for marker in ("first,", "second,", "third,")):
        return ["enumerates exactly three capabilities (First/Second/Third pattern)"]
    return []


def check_cover_letter_banned_opener(text):
    stripped = text.strip().lower()
    for opener in _BANNED_OPENERS:
        if stripped.startswith(opener):
            return [f"opens with banned phrase '{opener}'"]
    return []


def check_cover_letter_word_count(text, max_words=300):
    count = len(text.split())
    return [f"word count {count} exceeds {max_words}"] if count > max_words else []


def check_cover_letter_closing_hedges(text, max_hedges=1):
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    if not sentences:
        return []
    closing = sentences[-1].lower()
    count = sum(closing.count(h) for h in _HEDGE_WORDS)
    return [f"closing sentence has {count} hedge words (max {max_hedges})"] if count > max_hedges else []


def check_cover_letter(cl_text, resume_text):
    violations = []
    violations += check_cover_letter_number_overlap(cl_text, resume_text)
    violations += check_cover_letter_ngram_overlap(cl_text, resume_text)
    violations += check_cover_letter_capability_enumeration(cl_text)
    violations += check_cover_letter_banned_opener(cl_text)
    violations += check_cover_letter_word_count(cl_text)
    violations += check_cover_letter_closing_hedges(cl_text)
    return violations
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_resume_lint.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add resume_lint.py tests/test_resume_lint.py
git commit -m "feat: add resume_lint.py cover-letter violation checks"
```

---

## Task 8: `resume_build.py` — DOCX build

**Files:**
- Create: `resume_build.py`
- Test: `tests/test_resume_build.py`

**Interfaces:**
- Produces: `build_docx(strategy, master, output_path, margin_preset="standard") -> str` (returns
  `output_path`). `_MARGIN_PRESETS` (dict), `_MARGIN_LADDER` (ordered list of preset names) — both
  also consumed by Task 9's fitting-ladder loop.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resume_build.py`:

```python
"""Tests for resume_build.py's DOCX construction. Uses python-docx's own reader to verify
output, not mocks -- building a real (tiny) DOCX in a temp dir is fast and exercises the
real library integration, matching how gmail.py's tests exercise real MIME construction."""

import os

from docx import Document

import resume_build

_STRATEGY = {
    "section_order": ["Experience", "Projects", "Skills"],
    "projects_included": ["Viant", "Hiya"],
}
_MASTER = {
    "roles": [
        {"title": "Associate Product Manager", "company": "Protium Finance",
         "period": "Apr 2025 - Aug 2025", "bullets": ["Eliminated vendor cost via a build-vs-buy model."]},
    ],
    "education": {"institution": "Dartmouth College", "program": "Master's, Engineering Management",
                   "graduation": "Nov 2026"},
    "projects": {
        "Viant": {"bullets": ["Narrowed 7 vendors to 2 finalists."]},
        "Hiya": {"bullets": ["Protected 500M+ users."]},
    },
}


def test_build_docx_creates_file_at_output_path(tmp_path):
    output_path = str(tmp_path / "resume.docx")
    result = resume_build.build_docx(_STRATEGY, _MASTER, output_path)
    assert result == output_path
    assert os.path.exists(output_path)


def test_build_docx_includes_role_and_project_content():
    doc_path = "test_build_docx_includes_role_and_project_content.docx"
    try:
        resume_build.build_docx(_STRATEGY, _MASTER, doc_path)
        doc = Document(doc_path)
        full_text = "\n".join(p.text for p in doc.paragraphs)
        assert "Associate Product Manager" in full_text
        assert "Protium Finance" in full_text
        assert "Viant" in full_text
        assert "Narrowed 7 vendors to 2 finalists." in full_text
    finally:
        if os.path.exists(doc_path):
            os.remove(doc_path)


def test_build_docx_applies_named_margin_preset():
    from docx.shared import Twips
    doc_path = "test_build_docx_applies_named_margin_preset.docx"
    try:
        resume_build.build_docx(_STRATEGY, _MASTER, doc_path, margin_preset="tight")
        doc = Document(doc_path)
        section = doc.sections[0]
        preset = resume_build._MARGIN_PRESETS["tight"]
        assert section.top_margin == Twips(preset["top"])
        assert section.left_margin == Twips(preset["left"])
    finally:
        if os.path.exists(doc_path):
            os.remove(doc_path)


def test_margin_ladder_covers_every_preset():
    assert set(resume_build._MARGIN_LADDER) == set(resume_build._MARGIN_PRESETS.keys())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_resume_build.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'resume_build'`.

- [ ] **Step 3: Implement**

Create `resume_build.py`:

```python
"""
Builds a resume DOCX from a strategy (resume_agent.py's stage-4 output) and the
master resume data (resume/data/master.json). Pure/deterministic -- no LLM calls,
no I/O beyond writing the output file. Raises on failure; does not swallow
exceptions, since this is a manual, interactive tool. See
docs/superpowers/specs/2026-08-29-phase3-resume-intelligence-design.md.
"""

from docx import Document
from docx.shared import Pt, Twips
from docx.enum.text import WD_LINE_SPACING

# ── Typography and spacing presets (corpus spec Part 12) ───────────────────────

_MARGIN_PRESETS = {
    "comfortable": {"top": 1080, "bottom": 1080, "left": 1152, "right": 1152},
    "standard": {"top": 1080, "bottom": 900, "left": 1080, "right": 1080},
    "tight": {"top": 780, "bottom": 720, "left": 1080, "right": 1080},
    "aggressive": {"top": 720, "bottom": 720, "left": 900, "right": 900},
    "floor": {"top": 720, "bottom": 720, "left": 720, "right": 720},
}
_MARGIN_LADDER = ["comfortable", "standard", "tight", "aggressive", "floor"]

_NAME_SIZE_PT = 22
_SECTION_HEADER_SIZE_PT = 10.5
_BODY_SIZE_PT = 10
_FONT_NAME = "Calibri"


# ── Section builders ───────────────────────────────────────────────────────────

def _apply_margins(doc, preset_name):
    preset = _MARGIN_PRESETS[preset_name]
    section = doc.sections[0]
    section.top_margin = Twips(preset["top"])
    section.bottom_margin = Twips(preset["bottom"])
    section.left_margin = Twips(preset["left"])
    section.right_margin = Twips(preset["right"])


def _add_name_heading(doc, name):
    p = doc.add_paragraph()
    run = p.add_run(name)
    run.font.name = _FONT_NAME
    run.font.size = Pt(_NAME_SIZE_PT)
    run.bold = True


def _add_section_header(doc, title):
    p = doc.add_paragraph()
    run = p.add_run(title.upper())
    run.font.name = _FONT_NAME
    run.font.size = Pt(_SECTION_HEADER_SIZE_PT)
    run.bold = True


def _add_body_line(doc, text, bullet=False):
    p = doc.add_paragraph(style="List Bullet" if bullet else None)
    run = p.add_run(text)
    run.font.name = _FONT_NAME
    run.font.size = Pt(_BODY_SIZE_PT)
    p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
    p.paragraph_format.space_after = Pt(4)


def _add_experience_section(doc, master):
    _add_section_header(doc, "Experience")
    for role in master.get("roles", []):
        _add_body_line(doc, f"{role['title']}, {role['company']} ({role['period']})")
        for bullet in role.get("bullets", []):
            _add_body_line(doc, bullet, bullet=True)


def _add_projects_section(doc, master, projects_included):
    _add_section_header(doc, "Projects")
    for project_name in projects_included:
        project = master.get("projects", {}).get(project_name)
        if not project:
            continue
        _add_body_line(doc, project_name)
        for bullet in project.get("bullets", []):
            _add_body_line(doc, bullet, bullet=True)


def _add_education_section(doc, master):
    education = master.get("education")
    if not education:
        return
    _add_section_header(doc, "Education")
    _add_body_line(doc, f"{education['institution']} -- {education['program']} ({education['graduation']})")


_SECTION_BUILDERS = {
    "Experience": _add_experience_section,
    "Education": _add_education_section,
}


def build_docx(strategy, master, output_path, margin_preset="standard"):
    """Build a resume DOCX from `strategy` (section order, projects included) and `master`
    (resume/data/master.json content). Returns output_path."""
    doc = Document()
    _apply_margins(doc, margin_preset)
    _add_name_heading(doc, "Kishore Theeraj Vasudevan Jaya")

    for section_name in strategy.get("section_order", []):
        if section_name == "Projects":
            _add_projects_section(doc, master, strategy.get("projects_included", []))
        elif section_name in _SECTION_BUILDERS:
            _SECTION_BUILDERS[section_name](doc, master)

    doc.save(output_path)
    return output_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_resume_build.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add resume_build.py tests/test_resume_build.py
git commit -m "feat: add resume_build.py DOCX construction with margin/spacing presets"
```

---

## Task 9: `resume_build.py` — PDF conversion, page count, and the fitting ladder

**Files:**
- Modify: `resume_build.py` (append)
- Test: `tests/test_resume_build.py` (append)

**Interfaces:**
- Consumes: `build_docx`, `_MARGIN_LADDER` (Task 8, same module), `config.RESUME_SOFFICE_TIMEOUT_SECONDS`
  (Task 3).
- Produces: `convert_to_pdf(docx_path, output_dir) -> str` (returns the PDF path),
  `page_count(pdf_path) -> int`, `fit_to_one_page(strategy, master, output_path, output_dir) -> tuple[str, str]`
  (returns `(pdf_path, margin_preset_used)`, raises `StillOverflowError` if the deterministic rungs
  (spacing/margins/font floor) can't get it to one page — Task 12's orchestration catches that and
  triggers one content-editing regeneration via Claude, matching `preflight.py`'s established
  retry-once pattern).

**Note on scope**: the corpus spec's Part 13 fitting ladder has 9 rungs. Rungs 1-4 (line spacing,
bullet spacing, header spacing, margins) and rung 9 (font-size floor) are pure formatting —
implemented here, deterministically. Rungs 5-8 (orphan-word trims, folding a section header,
dropping a bullet, dropping a section) are **content edits**, not formatting — those belong to
`resume_agent.py`'s regeneration step (Task 12), which can ask Claude to shorten the content, the
same way `preflight.py` re-prompts Claude with an error list on a failed check. This split keeps
`resume_build.py` pure/deterministic and reuses this repo's existing regenerate-on-failure pattern
instead of inventing new content-editing string logic.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_resume_build.py`:

```python
from unittest.mock import MagicMock

import pytest

import config


# ── convert_to_pdf ─────────────────────────────────────────────────────────────

def test_convert_to_pdf_calls_soffice_and_returns_pdf_path(mocker, tmp_path):
    run = mocker.patch("resume_build.subprocess.run")
    docx_path = str(tmp_path / "resume.docx")
    result = resume_build.convert_to_pdf(docx_path, str(tmp_path))
    run.assert_called_once()
    args = run.call_args[0][0]
    assert args[0] == "soffice"
    assert "--headless" in args
    assert docx_path in args
    assert result == str(tmp_path / "resume.pdf")


def test_convert_to_pdf_uses_configured_timeout(mocker, tmp_path):
    run = mocker.patch("resume_build.subprocess.run")
    resume_build.convert_to_pdf(str(tmp_path / "r.docx"), str(tmp_path))
    assert run.call_args.kwargs["timeout"] == config.RESUME_SOFFICE_TIMEOUT_SECONDS


# ── page_count ─────────────────────────────────────────────────────────────────

def test_page_count_reads_pdf_page_count(mocker):
    fake_reader = MagicMock()
    fake_reader.pages = [MagicMock(), MagicMock()]
    mocker.patch("resume_build.PdfReader", return_value=fake_reader)
    assert resume_build.page_count("fake.pdf") == 2


# ── fit_to_one_page ────────────────────────────────────────────────────────────

def test_fit_to_one_page_returns_immediately_when_already_one_page(mocker, tmp_path):
    mocker.patch.object(resume_build, "build_docx", return_value="r.docx")
    mocker.patch.object(resume_build, "convert_to_pdf", return_value="r.pdf")
    mocker.patch.object(resume_build, "page_count", return_value=1)
    pdf_path, preset = resume_build.fit_to_one_page(_STRATEGY, _MASTER, "r.docx", str(tmp_path))
    assert pdf_path == "r.pdf"
    assert preset == "standard"


def test_fit_to_one_page_walks_margin_ladder_until_it_fits(mocker, tmp_path):
    mocker.patch.object(resume_build, "build_docx", return_value="r.docx")
    mocker.patch.object(resume_build, "convert_to_pdf", return_value="r.pdf")
    # 5 rungs total (line/bullet/header spacing don't change page count in this fake sequence,
    # margins rung on attempt 4 finally fits)
    mocker.patch.object(resume_build, "page_count", side_effect=[2, 2, 2, 1])
    pdf_path, preset = resume_build.fit_to_one_page(_STRATEGY, _MASTER, "r.docx", str(tmp_path))
    assert pdf_path == "r.pdf"
    assert preset in resume_build._MARGIN_LADDER


def test_fit_to_one_page_raises_still_overflow_error_after_exhausting_ladder(mocker, tmp_path):
    mocker.patch.object(resume_build, "build_docx", return_value="r.docx")
    mocker.patch.object(resume_build, "convert_to_pdf", return_value="r.pdf")
    mocker.patch.object(resume_build, "page_count", return_value=2)
    with pytest.raises(resume_build.StillOverflowError):
        resume_build.fit_to_one_page(_STRATEGY, _MASTER, "r.docx", str(tmp_path))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_resume_build.py -v`
Expected: the seven new tests FAIL — `resume_build.subprocess`/`PdfReader`/`convert_to_pdf`/
`page_count`/`fit_to_one_page`/`StillOverflowError` don't exist yet.

- [ ] **Step 3: Implement**

Add these imports to the top of `resume_build.py`:

```python
import os
import subprocess

from pypdf import PdfReader

import config
```

Append to `resume_build.py`:

```python
# ── PDF conversion ──────────────────────────────────────────────────────────────

class StillOverflowError(Exception):
    """Raised when the deterministic fitting-ladder rungs can't get a resume to one page.
    Caller (resume_agent.py) catches this and triggers a content-editing regeneration."""


def convert_to_pdf(docx_path, output_dir):
    """Convert docx_path to PDF via LibreOffice headless. Returns the output PDF path. Raises on
    any failure (missing soffice binary, conversion error, timeout) -- never swallowed."""
    subprocess.run(
        ["soffice", "--headless", "--convert-to", "pdf", "--outdir", output_dir, docx_path],
        check=True, capture_output=True, timeout=config.RESUME_SOFFICE_TIMEOUT_SECONDS,
    )
    base = os.path.splitext(os.path.basename(docx_path))[0]
    return os.path.join(output_dir, base + ".pdf")


def page_count(pdf_path):
    return len(PdfReader(pdf_path).pages)


# ── Fitting ladder (corpus spec Part 13, formatting rungs only -- see Task 9's docstring note) ──

_FORMATTING_RUNGS = ["line_spacing", "bullet_spacing", "header_spacing", "margins", "font_floor"]


def fit_to_one_page(strategy, master, output_path, output_dir):
    """
    Build, convert, and check page count, walking the deterministic formatting rungs of the
    corpus spec's Part 13 ladder in order (line spacing -> bullet spacing -> header spacing ->
    margins -> font floor) until the PDF is one page. Returns (pdf_path, margin_preset_used).
    Raises StillOverflowError if still >1 page after every rung -- the caller should treat that
    as a signal to shorten content, not retry formatting again.
    """
    preset_index = 0
    for rung_index, rung in enumerate(_FORMATTING_RUNGS):
        preset_name = _MARGIN_LADDER[preset_index]
        docx_path = build_docx(strategy, master, output_path, margin_preset=preset_name)
        pdf_path = convert_to_pdf(docx_path, output_dir)
        if page_count(pdf_path) <= 1:
            return pdf_path, preset_name
        if rung == "margins" and preset_index < len(_MARGIN_LADDER) - 1:
            preset_index += 1
    raise StillOverflowError(
        f"still overflows one page after every formatting rung (final preset: "
        f"{_MARGIN_LADDER[preset_index]})"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_resume_build.py -v`
Expected: all PASS.

- [ ] **Step 5: Live smoke test (requires LibreOffice installed locally)**

In your own terminal, confirm LibreOffice is installed (`brew install --cask libreoffice` on macOS
if not), then:

```bash
source .venv/bin/activate && python3 -c "
import resume_build
strategy = {'section_order': ['Experience'], 'projects_included': []}
master = {'roles': [{'title': 'Test Role', 'company': 'Test Co', 'period': '2026', 'bullets': ['A bullet.']}]}
pdf_path, preset = resume_build.fit_to_one_page(strategy, master, '/tmp/test_resume.docx', '/tmp')
print(f'built {pdf_path} using preset {preset}, pages={resume_build.page_count(pdf_path)}')
"
```

Expected: prints a real PDF path with `pages=1`. This confirms the real `soffice` conversion works
end-to-end, not just the mocked test. Do not commit anything from this step.

- [ ] **Step 6: Commit**

```bash
git add resume_build.py tests/test_resume_build.py
git commit -m "feat: add PDF conversion and deterministic fitting-ladder rungs to resume_build.py"
```

---

## Task 10: `resume_scrub.py` — metadata scrub and fingerprint verification

**Files:**
- Create: `resume_scrub.py`
- Test: `tests/test_resume_scrub.py`

**Interfaces:**
- Produces: `scrub_pdf_metadata(pdf_path, title, keywords) -> None` (mutates the file in place),
  `verify_no_fingerprints(unpacked_or_text) -> list[str]` (returns matched fingerprint strings,
  empty means clean), `read_pdf_metadata_text(pdf_path) -> str` (concatenates docinfo values,
  feeds `verify_no_fingerprints`). Consumed by Task 12's `--build` orchestration.

**Note on scope**: the corpus spec's DOCX metadata scrub (`core.xml`/`app.xml` rewriting) targets
the Node.js `docx` package's zip output structure. `python-docx` (Task 8's build tool) already
writes reasonable `core.xml`/`app.xml` properties as part of normal document creation — there is no
"LibreOffice" or "python-docx" string embedded in those XML fields the way there was in the
corpus's Node/LibreOffice pipeline, since `python-docx` sets its own core properties directly with
no external tool naming itself in the XML by default. This task therefore focuses on **explicitly
setting** `core_properties` (author, title, keywords, timestamps) via `python-docx`'s own API
(no zip-level XML patching needed) and on the **PDF** side, where `pikepdf` is required (PDF
metadata defaults to naming the converting tool, e.g. `LibreOffice`) — plus a fingerprint grep that
covers both file types for defense in depth.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resume_scrub.py`:

```python
"""Tests for resume_scrub.py. PDF scrubbing is tested against pikepdf's real API on a tiny
real PDF (fast, no mocking needed for a library that's already deterministic and local)."""

import datetime

import pikepdf
import pytest

import resume_scrub


@pytest.fixture
def tiny_pdf(tmp_path):
    path = str(tmp_path / "test.pdf")
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    with pdf.open_metadata() as meta:
        meta["dc:creator"] = ["LibreOffice"]
        meta["pdf:Producer"] = "LibreOffice 24.2"
    pdf.save(path)
    return path


def test_scrub_pdf_metadata_overwrites_creator_and_producer(tiny_pdf):
    resume_scrub.scrub_pdf_metadata(tiny_pdf, title="Kishore Theeraj - Resume", keywords="PM, SQL")
    with pikepdf.open(tiny_pdf) as pdf:
        with pdf.open_metadata() as meta:
            assert meta.get("dc:creator") not in (["LibreOffice"], "LibreOffice")
            assert meta.get("pdf:Producer") != "LibreOffice 24.2"
            assert meta.get("dc:title") == "Kishore Theeraj - Resume"


def test_scrub_pdf_metadata_sets_realistic_non_identical_timestamps(tiny_pdf):
    resume_scrub.scrub_pdf_metadata(tiny_pdf, title="T", keywords="k")
    with pikepdf.open(tiny_pdf) as pdf:
        docinfo = pdf.docinfo
        created = str(docinfo.get("/CreationDate", ""))
        modified = str(docinfo.get("/ModDate", ""))
        assert created and modified
        assert created != modified


# ── verify_no_fingerprints ──────────────────────────────────────────────────────

def test_verify_no_fingerprints_flags_tool_names():
    text = "Producer: LibreOffice 24.2, generated via python-docx"
    violations = resume_scrub.verify_no_fingerprints(text)
    assert any("libreoffice" in v.lower() for v in violations)
    assert any("python-docx" in v.lower() for v in violations)


def test_verify_no_fingerprints_passes_clean_text():
    assert resume_scrub.verify_no_fingerprints("Producer: Microsoft: Print To PDF") == []


def test_verify_no_fingerprints_does_not_flag_claude_in_resume_content():
    text = "Skills: Claude, Cursor, Claude Code, SQL, Python"
    assert resume_scrub.verify_no_fingerprints(text) == []


# ── read_pdf_metadata_text ──────────────────────────────────────────────────────

def test_read_pdf_metadata_text_reflects_scrubbed_values(tiny_pdf):
    resume_scrub.scrub_pdf_metadata(tiny_pdf, title="My Resume Title", keywords="PM, SQL")
    text = resume_scrub.read_pdf_metadata_text(tiny_pdf)
    assert "My Resume Title" in text
    assert "libreoffice" not in text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_resume_scrub.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'resume_scrub'`.

- [ ] **Step 3: Implement**

Create `resume_scrub.py`:

```python
"""
Overwrites PDF metadata (XMP + docinfo) that LibreOffice's PDF conversion leaves
behind, and verifies no tool fingerprint survives in the built file. Corpus spec
Part 14. No I/O beyond mutating the given file path; raises on failure. See
docs/superpowers/specs/2026-08-29-phase3-resume-intelligence-design.md.
"""

import datetime
import re

import pikepdf

# Fingerprints that must never survive in a built file's metadata (case-insensitive).
# NOTE: "claude" is deliberately excluded from this list -- it legitimately appears in
# resume *content* (Skills, project descriptions), and this function is also used to scan
# metadata-only strings where that distinction doesn't apply the same way. Callers that scan
# whole-file content (not just metadata fields) are responsible for only passing metadata text.
_FINGERPRINTS = ("libreoffice", "soffice", "python-docx", "docx-js", "openoffice")


def scrub_pdf_metadata(pdf_path, title, keywords):
    """Overwrite XMP and docinfo metadata on pdf_path in place. Target values match a real
    Microsoft Word export, not a LibreOffice/tool default."""
    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        created = datetime.datetime.now() - datetime.timedelta(days=5)
        modified = datetime.datetime.now()

        with pdf.open_metadata() as meta:
            meta["xmp:CreatorTool"] = "Microsoft Word"
            meta["pdf:Producer"] = "Microsoft: Print To PDF"
            meta["dc:creator"] = ["Kishore Theeraj Vasudevan Jaya"]
            meta["dc:title"] = title

        pdf.docinfo["/Creator"] = pikepdf.String("Microsoft Word")
        pdf.docinfo["/Producer"] = pikepdf.String("Microsoft: Print To PDF")
        pdf.docinfo["/Author"] = pikepdf.String("Kishore Theeraj Vasudevan Jaya")
        pdf.docinfo["/Title"] = pikepdf.String(title)
        pdf.docinfo["/Keywords"] = pikepdf.String(keywords)
        pdf.docinfo["/CreationDate"] = pikepdf.String(created.strftime("D:%Y%m%d%H%M%S"))
        pdf.docinfo["/ModDate"] = pikepdf.String(modified.strftime("D:%Y%m%d%H%M%S"))

        pdf.save(pdf_path)


def verify_no_fingerprints(text):
    """Return a list of matched tool-fingerprint strings found in `text` (case-insensitive).
    Empty list means clean. Callers must pass metadata/property text, not resume body content --
    'Claude' legitimately appears in Skills/Projects and is not a fingerprint by itself."""
    lowered = text.lower()
    return [fp for fp in _FINGERPRINTS if fp in lowered]


def read_pdf_metadata_text(pdf_path):
    """Concatenate a PDF's docinfo metadata field values into one string, for
    verify_no_fingerprints to scan after scrub_pdf_metadata has run."""
    with pikepdf.open(pdf_path) as pdf:
        return " ".join(str(v) for v in pdf.docinfo.values())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_resume_scrub.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add resume_scrub.py tests/test_resume_scrub.py
git commit -m "feat: add resume_scrub.py PDF metadata scrub and fingerprint verification"
```

---

## Task 11: `resume_agent.py` — `--propose` mode

**Files:**
- Create: `resume_agent.py`
- Test: `tests/test_resume_agent.py`

**Interfaces:**
- Consumes: `db.get_job_application(application_id)`, `db.set_resume_strategy(application_id, strategy)`
  (Task 5), `config.RESUME_MODEL` (Task 3).
- Produces: `_call_claude(prompt, system=None) -> str` (private, own minimal Anthropic client --
  mirrors `emailer.py`'s `_call_claude` shape but is independent, matching this repo's
  self-contained-module convention), `_check_deadline(job) -> bool` (True if safe to proceed),
  `propose(application_id) -> dict` (the strategy dict, also written to the DB). `run_propose()` --
  CLI entry point for `--propose`. Task 12 adds `--build`'s entry point to this same file.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resume_agent.py`:

```python
"""Tests for resume_agent.py. All Claude calls and db.py calls are mocked -- no real network,
no real Supabase, no real credentials."""

import datetime

import pytest

import db
import resume_agent


# ── _check_deadline ──────────────────────────────────────────────────────────

def test_check_deadline_true_when_no_deadline_known():
    assert resume_agent._check_deadline({"posting_snapshot": {}}) is True


def test_check_deadline_true_when_deadline_in_future():
    future = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
    job = {"posting_snapshot": {"deadline": future}}
    assert resume_agent._check_deadline(job) is True


def test_check_deadline_false_when_deadline_has_passed():
    past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    job = {"posting_snapshot": {"deadline": past}}
    assert resume_agent._check_deadline(job) is False


# ── propose ────────────────────────────────────────────────────────────────────

def test_propose_raises_when_job_not_found(mocker):
    mocker.patch.object(db, "get_job_application", return_value=None)
    with pytest.raises(ValueError, match="not found"):
        resume_agent.propose(999)


def test_propose_raises_when_deadline_has_passed(mocker):
    past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    mocker.patch.object(db, "get_job_application", return_value={
        "id": 1, "company": "Acme", "role": "PM", "posting_snapshot": {"deadline": past},
    })
    with pytest.raises(resume_agent.DeadlinePassedError):
        resume_agent.propose(1)


def test_propose_writes_strategy_to_db(mocker):
    mocker.patch.object(db, "get_job_application", return_value={
        "id": 1, "company": "Acme", "role": "Product Manager", "posting_snapshot": {},
    })
    mocker.patch.object(resume_agent, "_call_claude", return_value=(
        '{"section_order": ["Experience", "Projects"], "projects_included": ["Hiya"], '
        '"cover_letter_angle": "test angle", "named_gaps": ["no fintech background"]}'
    ))
    set_strategy = mocker.patch.object(db, "set_resume_strategy", return_value={"id": 1})
    result = resume_agent.propose(1)
    assert result["section_order"] == ["Experience", "Projects"]
    set_strategy.assert_called_once()
    args, kwargs = set_strategy.call_args
    assert args[0] == 1
    assert args[1]["cover_letter_angle"] == "test angle"


def test_propose_raises_on_malformed_claude_response(mocker):
    mocker.patch.object(db, "get_job_application", return_value={
        "id": 1, "company": "Acme", "role": "PM", "posting_snapshot": {},
    })
    mocker.patch.object(resume_agent, "_call_claude", return_value="not json")
    with pytest.raises(ValueError, match="could not parse strategy"):
        resume_agent.propose(1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_resume_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'resume_agent'`.

- [ ] **Step 3: Implement**

Create `resume_agent.py`:

```python
"""
Orchestrates the resume/cover-letter generation pipeline for one job_applications row.
Manual, interactive, two-command CLI -- see docs/superpowers/specs/2026-08-29-
phase3-resume-intelligence-design.md for the full design and why this can't run
unattended.

Usage:
  python3 resume_agent.py --job-id 42 --propose
  python3 resume_agent.py --job-id 42 --build
"""

import argparse
import datetime
import json
import logging

import anthropic

import config
import db

log = logging.getLogger(__name__)

_claude = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=4)


class DeadlinePassedError(Exception):
    """Raised when a job's posted deadline has already passed -- refuse to build."""


# ── Claude client ────────────────────────────────────────────────────────────────

def _call_claude(prompt, system=None):
    kwargs = dict(
        model=config.RESUME_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    if system:
        kwargs["system"] = system
    resp = _claude.messages.create(**kwargs)
    return resp.content[0].text


# ── Deadline gate (corpus spec Part 11: Cott/McKinsey lesson) ──────────────────

def _check_deadline(job):
    deadline_str = (job.get("posting_snapshot") or {}).get("deadline")
    if not deadline_str:
        return True
    deadline = datetime.date.fromisoformat(deadline_str[:10])
    return deadline >= datetime.date.today()


# ── Stage 0-4: propose ──────────────────────────────────────────────────────────

_STRATEGY_PROMPT = """You are diagnosing a job posting and proposing a resume strategy.
Company: {company}
Role: {role}
Posting details: {posting_snapshot}

Follow this process:
1. Diagnose the role type and the 8-12 capabilities the posting screens for.
2. Identify the top 3 matches and 2 honest gaps against a Product Manager background.
3. Propose a strategy: section order, which projects to include (max 3), a one-line
   cover letter angle, and the honest gaps to name rather than hide.

Respond with ONLY a JSON object, no other text:
{{"section_order": [...], "projects_included": [...], "cover_letter_angle": "...", "named_gaps": [...]}}"""


def propose(application_id):
    """Run stages 0-4 (load context, diagnose, research, strategy) for one job_applications row.
    Writes the resulting strategy to job_applications.resume_strategy. Builds nothing. Raises
    ValueError if the row doesn't exist or Claude's response can't be parsed, DeadlinePassedError
    if the posting's deadline has already passed."""
    job = db.get_job_application(application_id)
    if job is None:
        raise ValueError(f"job_applications row {application_id} not found")
    if not _check_deadline(job):
        raise DeadlinePassedError(
            f"job_applications row {application_id}'s deadline has passed -- refusing to build"
        )

    prompt = _STRATEGY_PROMPT.format(
        company=job.get("company"), role=job.get("role"),
        posting_snapshot=json.dumps(job.get("posting_snapshot") or {}),
    )
    raw = _call_claude(prompt)
    try:
        strategy = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"could not parse strategy from Claude's response: {exc}") from exc

    db.set_resume_strategy(application_id, strategy)
    log.info(f"[RESUME] | {application_id} | {job.get('company')} | strategy proposed")
    return strategy


def run_propose(application_id):
    strategy = propose(application_id)
    print(json.dumps(strategy, indent=2))
    print(f"\nStrategy written to job_applications.resume_strategy for row {application_id}.")
    print("Review it, then run with --build to generate the documents.")


if __name__ == "__main__":
    logging.basicConfig(
        filename="resume_agent.log",
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", type=int, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--propose", action="store_true")
    mode.add_argument("--build", action="store_true")
    args = parser.parse_args()

    if args.propose:
        run_propose(args.job_id)
    elif args.build:
        print("--build is not implemented yet.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_resume_agent.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full test suite**

Run: `python3 -m pytest`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add resume_agent.py tests/test_resume_agent.py
git commit -m "feat: add resume_agent.py --propose mode (diagnose, research, strategy)"
```

---

## Task 12: `resume_agent.py` — `--build` mode

**Files:**
- Modify: `resume_agent.py` (append)
- Test: `tests/test_resume_agent.py` (append)

**Interfaces:**
- Consumes: `db.get_job_application`, `db.set_resume_files`, `db.upload_resume_file` (Task 5),
  `resume_build.fit_to_one_page`, `resume_build.StillOverflowError` (Task 9),
  `resume_lint.check_em_dashes`, `check_jargon`, `check_metrics_whitelist`, `check_cover_letter`
  (Tasks 6-7), `resume_scrub.scrub_pdf_metadata`, `verify_no_fingerprints`, `read_pdf_metadata_text`
  (Task 10), `config.RESUME_MAX_BUILD_RETRIES`, `config.RESUME_COVER_LETTER_MAX_WORDS` (Task 3), the six
  `resume/data/*.json` files (Task 4) -- note `master.json`'s `bullet_ids` (referencing
  `metrics.json` by id) must be resolved into literal `bullets` text before calling
  `resume_build.build_docx`/`fit_to_one_page`, which expect the resolved shape (see Task 8's test
  fixture). `_resolve_master(master, metrics)` (this task) is that bridge.
- Produces: `_resolve_master(master, metrics) -> dict` (private -- resolves `bullet_ids` into
  literal `bullets`, all roles included unconditionally, projects filtered to
  `strategy["projects_included"]`). `build(application_id) -> dict` (returns
  `{"resume_file_ref": ..., "cover_letter_file_ref": ...}`). `run_build(application_id)` -- CLI
  entry point for `--build`.

**Important real-data consequence**: Task 4's `metrics.json` has three entries with
`"resolved": null` (the corpus spec's own flagged conflicts), and every Protium role's bullets are
included unconditionally by `_resolve_master`. That means `build()` run against the *real*
`resume/data/*.json` files will always raise `LintFailedError` on the metrics-whitelist check until
the user resolves those three conflicts by hand in `metrics.json` -- this is the intended behavior
(Global Constraints: "fail loudly, don't silently pick"), not a bug. This task's own tests mock
`_load_data` with clean fixtures so they test the mechanism, not today's real unresolved data.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_resume_agent.py`:

```python
# ── _resolve_master ────────────────────────────────────────────────────────────

_CLEAN_MASTER = {
    "roles": [{"title": "Associate Product Manager", "company": "Acme", "period": "2025",
               "bullet_ids": ["m1"]}],
    "education": {"institution": "Dartmouth", "program": "MEM", "graduation": "2026"},
    "projects": {"Hiya": {"bullet_ids": ["m2"]}, "Viant": {"bullet_ids": ["m3"]}},
}
_CLEAN_METRICS = [
    {"id": "m1", "role": "APM", "text": "Shipped a roadmap.", "resolved": True, "conflicting_values": []},
    {"id": "m2", "role": "Project", "text": "Protected 500M+ users.", "resolved": True, "conflicting_values": []},
    {"id": "m3", "role": "Project", "text": "Narrowed 7 vendors to 2.", "resolved": True, "conflicting_values": []},
]
_CONFLICTED_METRICS = [
    {"id": "m1", "role": "APM", "text": "$20K/year saved.", "resolved": None, "conflicting_values": ["$120K/year"]},
]


def test_resolve_master_converts_bullet_ids_to_text_for_roles_and_filters_projects():
    strategy = {"projects_included": ["Hiya"]}
    resolved = resume_agent._resolve_master(_CLEAN_MASTER, _CLEAN_METRICS, strategy)
    assert resolved["roles"][0]["bullets"] == ["Shipped a roadmap."]
    assert resolved["projects"]["Hiya"]["bullets"] == ["Protected 500M+ users."]


# ── build ──────────────────────────────────────────────────────────────────────

_JOB_WITH_STRATEGY = {
    "id": 1, "company": "Acme", "role": "Product Manager", "posting_snapshot": {},
    "resume_strategy": {
        "section_order": ["Experience"], "projects_included": [],
        "cover_letter_angle": "test angle", "named_gaps": [],
    },
}


def _mock_clean_data(mocker):
    mocker.patch.object(resume_agent, "_load_data", side_effect=lambda name: {
        "master.json": _CLEAN_MASTER, "metrics.json": _CLEAN_METRICS, "jargon.json": {},
    }[name])


def test_build_raises_when_no_strategy_proposed_yet(mocker):
    mocker.patch.object(db, "get_job_application", return_value={
        "id": 1, "company": "Acme", "role": "PM", "resume_strategy": None,
    })
    with pytest.raises(ValueError, match="propose"):
        resume_agent.build(1)


def test_build_raises_lint_failed_error_on_unresolved_metric_conflict(mocker):
    job = dict(_JOB_WITH_STRATEGY, resume_strategy=dict(
        _JOB_WITH_STRATEGY["resume_strategy"], projects_included=[],
    ))
    mocker.patch.object(db, "get_job_application", return_value=job)
    mocker.patch.object(resume_agent, "_load_data", side_effect=lambda name: {
        "master.json": {"roles": [{"title": "APM", "company": "Acme", "period": "2025",
                                    "bullet_ids": ["m1"]}], "education": None, "projects": {}},
        "metrics.json": _CONFLICTED_METRICS, "jargon.json": {},
    }[name])
    with pytest.raises(resume_agent.LintFailedError, match="unresolved metric conflict"):
        resume_agent.build(1)


def test_build_happy_path_uploads_and_writes_file_refs(mocker):
    mocker.patch.object(db, "get_job_application", return_value=_JOB_WITH_STRATEGY)
    _mock_clean_data(mocker)
    mocker.patch.object(resume_agent, "_call_claude", return_value="A clean cover letter body.")
    mocker.patch("resume_agent.resume_build.fit_to_one_page", return_value=("/tmp/r.pdf", "standard"))
    mocker.patch("resume_agent.resume_build.convert_to_pdf", return_value="/tmp/cl.pdf")
    mocker.patch("resume_agent.resume_scrub.scrub_pdf_metadata")
    mocker.patch("resume_agent.resume_scrub.read_pdf_metadata_text", return_value="Microsoft Word")
    mocker.patch("resume_agent.resume_scrub.verify_no_fingerprints", return_value=[])
    mocker.patch("resume_agent.Document")
    mocker.patch("builtins.open", mocker.mock_open(read_data=b"pdfbytes"))
    upload = mocker.patch.object(db, "upload_resume_file", side_effect=[
        "resumes/1/resume.pdf", "resumes/1/cover_letter.pdf",
    ])
    set_files = mocker.patch.object(db, "set_resume_files", return_value={"id": 1})

    result = resume_agent.build(1)

    assert result["resume_file_ref"] == "resumes/1/resume.pdf"
    assert result["cover_letter_file_ref"] == "resumes/1/cover_letter.pdf"
    assert upload.call_count == 2
    set_files.assert_called_once()


def test_build_raises_on_cover_letter_lint_violation_after_one_retry(mocker):
    mocker.patch.object(db, "get_job_application", return_value=_JOB_WITH_STRATEGY)
    _mock_clean_data(mocker)
    mocker.patch("resume_agent.resume_build.fit_to_one_page", return_value=("/tmp/r.pdf", "standard"))
    mocker.patch("resume_agent.resume_scrub.scrub_pdf_metadata")
    mocker.patch("resume_agent.resume_scrub.read_pdf_metadata_text", return_value="Microsoft Word")
    mocker.patch("resume_agent.resume_scrub.verify_no_fingerprints", return_value=[])
    # Cover letter always contains an em dash -- lint keeps failing across the one retry.
    mocker.patch.object(resume_agent, "_call_claude", return_value="Bad letter — with an em dash.")
    with pytest.raises(resume_agent.LintFailedError):
        resume_agent.build(1)


def test_build_raises_lint_failed_error_when_resume_pdf_metadata_still_has_fingerprints(mocker):
    mocker.patch.object(db, "get_job_application", return_value=_JOB_WITH_STRATEGY)
    _mock_clean_data(mocker)
    mocker.patch("resume_agent.resume_build.fit_to_one_page", return_value=("/tmp/r.pdf", "standard"))
    mocker.patch("resume_agent.resume_scrub.scrub_pdf_metadata")
    mocker.patch("resume_agent.resume_scrub.read_pdf_metadata_text", return_value="Producer: LibreOffice 24.2")
    with pytest.raises(resume_agent.LintFailedError, match="fingerprint"):
        resume_agent.build(1)


def test_build_still_overflow_error_propagates_after_retry(mocker):
    mocker.patch.object(db, "get_job_application", return_value=_JOB_WITH_STRATEGY)
    _mock_clean_data(mocker)
    mocker.patch(
        "resume_agent.resume_build.fit_to_one_page",
        side_effect=resume_agent.resume_build.StillOverflowError("still too long"),
    )
    with pytest.raises(resume_agent.resume_build.StillOverflowError):
        resume_agent.build(1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_resume_agent.py -v`
Expected: the four new tests FAIL — `resume_agent.build`/`LintFailedError`/`resume_build`/
`resume_scrub` references don't exist in `resume_agent.py` yet.

- [ ] **Step 3: Implement**

Add these imports to the top of `resume_agent.py`, alongside the existing ones:

```python
import os

from docx import Document

import resume_build
import resume_lint
import resume_scrub
```

Add a module-level data loader near the top of `resume_agent.py` (after the `_claude` client
line):

```python
_DATA_DIR = os.path.join(os.path.dirname(__file__), "resume", "data")


def _load_data(name):
    with open(os.path.join(_DATA_DIR, name)) as f:
        return json.load(f)
```

Add `LintFailedError` next to `DeadlinePassedError`:

```python
class LintFailedError(Exception):
    """Raised when a cover letter or resume fails lint checks after the one allowed retry."""
```

Append to `resume_agent.py` (after `propose`/`run_propose`, before the `if __name__ ==` block):

```python
# ── Stage 5-9: build ─────────────────────────────────────────────────────────────

_COVER_LETTER_PROMPT = """Write a cover letter for {company}, {role}.
Cover letter angle: {angle}
Named gaps to acknowledge honestly, not hide: {gaps}

Rules: do not restate resume bullets in prose. Open with a specific moment, not a
generic statement. Around 250-290 words. No em dashes. End with one concrete,
scoped 90-day item."""


def _resolve_master(master, metrics, strategy):
    """Resolve master.json's bullet_ids (referencing metrics.json entries) into literal bullet
    text, which is the shape resume_build.build_docx expects. All roles are included
    unconditionally; projects are filtered to strategy["projects_included"]."""
    metrics_by_id = {m["id"]: m["text"] for m in metrics}
    resolved = {"roles": [], "education": master.get("education"), "projects": {}}
    for role in master.get("roles", []):
        resolved["roles"].append({
            "title": role["title"], "company": role["company"], "period": role["period"],
            "bullets": [metrics_by_id[bid] for bid in role.get("bullet_ids", []) if bid in metrics_by_id],
        })
    for name in strategy.get("projects_included", []):
        project = master.get("projects", {}).get(name)
        if project is None:
            continue
        resolved["projects"][name] = {
            "bullets": [metrics_by_id[bid] for bid in project.get("bullet_ids", []) if bid in metrics_by_id],
        }
    return resolved


def _resume_content_text(resolved_master):
    bullets = [b for role in resolved_master["roles"] for b in role["bullets"]]
    bullets += [b for p in resolved_master["projects"].values() for b in p["bullets"]]
    return " ".join(bullets)


def _lint_cover_letter(cl_text, resume_text):
    violations = []
    violations += resume_lint.check_em_dashes(cl_text)
    violations += resume_lint.check_jargon(cl_text, _load_data("jargon.json"))
    violations += resume_lint.check_cover_letter(cl_text, resume_text)
    return violations


def build(application_id):
    """Run stages 5-9 (build, humanize/lint, scrub, upload) for a job_applications row that
    already has a proposed resume_strategy. Returns {"resume_file_ref": ..., "cover_letter_file_ref": ...}.
    Raises ValueError if no strategy has been proposed yet, LintFailedError if the resume content
    references an unresolved metric conflict (no retry -- the data is static, retrying changes
    nothing until the user edits resume/data/metrics.json) or if the cover letter still fails lint
    after one retry, resume_build.StillOverflowError if the resume can't be formatted to one page
    after one content-editing retry."""
    job = db.get_job_application(application_id)
    strategy = job.get("resume_strategy")
    if not strategy:
        raise ValueError(f"job_applications row {application_id} has no resume_strategy -- run --propose first")

    master_raw = _load_data("master.json")
    metrics = _load_data("metrics.json")
    jargon = _load_data("jargon.json")
    master = _resolve_master(master_raw, metrics, strategy)

    resume_text = _resume_content_text(master)
    resume_violations = resume_lint.check_metrics_whitelist(resume_text, metrics)
    resume_violations += resume_lint.check_jargon(resume_text, jargon)
    if resume_violations:
        raise LintFailedError(f"resume content fails lint: {resume_violations}")

    pdf_path = None
    for attempt in range(config.RESUME_MAX_BUILD_RETRIES + 1):
        try:
            pdf_path, preset_used = resume_build.fit_to_one_page(
                strategy, master, f"/tmp/resume_{application_id}.docx", "/tmp",
            )
            break
        except resume_build.StillOverflowError:
            if attempt >= config.RESUME_MAX_BUILD_RETRIES:
                raise
            log.warning(f"[RESUME] | {application_id} | still overflows one page, retrying once")

    resume_scrub.scrub_pdf_metadata(
        pdf_path, title=f"{job.get('company')} - Resume", keywords=job.get("role", ""),
    )
    resume_fingerprints = resume_scrub.verify_no_fingerprints(resume_scrub.read_pdf_metadata_text(pdf_path))
    if resume_fingerprints:
        raise LintFailedError(f"resume PDF metadata still contains fingerprints: {resume_fingerprints}")

    cl_prompt = _COVER_LETTER_PROMPT.format(
        company=job.get("company"), role=job.get("role"),
        angle=strategy.get("cover_letter_angle", ""), gaps=strategy.get("named_gaps", []),
    )

    cl_text = None
    for attempt in range(config.RESUME_MAX_BUILD_RETRIES + 1):
        cl_text = _call_claude(cl_prompt)
        violations = _lint_cover_letter(cl_text, resume_text)
        if not violations:
            break
        if attempt >= config.RESUME_MAX_BUILD_RETRIES:
            raise LintFailedError(f"cover letter still fails lint after retry: {violations}")
        log.warning(f"[RESUME] | {application_id} | cover letter lint failed, retrying once: {violations}")
        cl_prompt = cl_prompt + f"\n\nFix these violations from the previous draft: {violations}"

    cl_docx_path = f"/tmp/cover_letter_{application_id}.docx"
    cl_doc = Document()
    cl_doc.add_paragraph(cl_text)
    cl_doc.save(cl_docx_path)
    cl_pdf_path = resume_build.convert_to_pdf(cl_docx_path, "/tmp")
    resume_scrub.scrub_pdf_metadata(
        cl_pdf_path, title=f"{job.get('company')} - Cover Letter", keywords=job.get("role", ""),
    )
    cl_fingerprints = resume_scrub.verify_no_fingerprints(resume_scrub.read_pdf_metadata_text(cl_pdf_path))
    if cl_fingerprints:
        raise LintFailedError(f"cover letter PDF metadata still contains fingerprints: {cl_fingerprints}")

    with open(pdf_path, "rb") as f:
        resume_ref = db.upload_resume_file(f"resumes/{application_id}/resume.pdf", f.read(), "application/pdf")
    with open(cl_pdf_path, "rb") as f:
        cl_ref = db.upload_resume_file(f"resumes/{application_id}/cover_letter.pdf", f.read(), "application/pdf")

    db.set_resume_files(application_id, resume_file_ref=resume_ref, cover_letter_file_ref=cl_ref)
    log.info(f"[RESUME] | {application_id} | {job.get('company')} | build complete")
    return {"resume_file_ref": resume_ref, "cover_letter_file_ref": cl_ref}


def run_build(application_id):
    result = build(application_id)
    print(f"Resume: {result['resume_file_ref']}")
    print(f"Cover letter: {result['cover_letter_file_ref']}")
```

Replace the `elif args.build:` branch at the bottom of `resume_agent.py`:

```python
    elif args.build:
        run_build(args.job_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_resume_agent.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full test suite**

Run: `python3 -m pytest`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add resume_agent.py tests/test_resume_agent.py
git commit -m "feat: add resume_agent.py --build mode (build, lint, scrub, upload)"
```

---

## Task 13: Docs — close out this plan

**Files:**
- Modify: `CLAUDE.md` (root)
- Modify: `docs/python/db-schema.md`
- Modify: `docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md`

**Interfaces:** none — documentation only.

- [ ] **Step 1: Update the root `CLAUDE.md` module layout list**

Add `resume_agent.py`, `resume_lint.py`, `resume_build.py`, `resume_scrub.py`, and `resume/` to the
module-layout code block, right after `jobright.py`.

- [ ] **Step 2: Add a "Resume intelligence" section to root `CLAUDE.md`**

Add a new `##` section, right after the "JobRight puller" section:

```markdown
## Resume intelligence (full-fledged buildout, Phase 3)

`resume_agent.py` (manual only, two-command CLI: `--propose` then `--build`) generates a tailored
resume + cover letter for a specific `job_applications` row, distilled from the user's own 30-session
corpus spec (`RESUME_AGENT_SPEC.md`). `--propose` runs JD diagnosis/research/strategy and writes
`job_applications.resume_strategy` (JSONB) -- nothing is built yet. `--build` only proceeds if a
strategy already exists (i.e. a human reviewed it), then builds the DOCX, converts to PDF via
LibreOffice (`soffice`, external system binary), lints, scrubs metadata, and uploads to the
`resumes` Supabase Storage bucket. This mirrors the Gmail-draft pattern (propose, human reviews,
human acts), not the critic-loop pattern -- strategy correctness isn't something a rubric score can
validate.

Reference data lives in git-versioned `resume/data/*.json` (`master.json`, `metrics.json`,
`jargon.json`, `projects.json`, `skills.json`, `moments.json`), transcribed directly from the
corpus spec, not fabricated. `metrics.json` entries carry `resolved: null` for the three flagged
metric conflicts -- `resume_lint.check_metrics_whitelist` hard-fails a build that uses any of those
numbers until the user resolves them by hand.

`resume_lint.py`, `resume_build.py`, and `resume_scrub.py` are pure/deterministic modules that
**raise on failure rather than swallowing it** -- unlike `ats.py`/`jobright.py`'s best-effort
posture (built for unattended background runs), this pipeline is manual and interactive, so a
failure should surface immediately.

`resume_build.py`'s fitting ladder only implements the deterministic formatting rungs (spacing,
margins, font floor) from the corpus spec's Part 13; the content-editing rungs (orphan-word trims,
section folding, bullet drops) are handled by `resume_agent.py --build`'s one-retry regeneration
loop instead (same pattern as `preflight.py`'s regenerate-with-error-list retry), since those are
content decisions, not formatting.

**AI-content-detection evasion was explicitly declined** -- see
docs/superpowers/specs/2026-08-29-phase3-resume-intelligence-design.md's "Rejected, not deferred"
section. The humanizer lint pass (em dashes, jargon) and the PDF metadata scrub (tool-fingerprint
removal, realistic timestamps) both shipped; a dedicated AI-detector-evasion layer did not, and no
third-party tool was fetched or integrated for that purpose.

No auto-submit exists in this phase -- that is Phase 2.5 (auto-apply agent), a separate future
design gated behind its own explicit opt-in.
```

- [ ] **Step 3: Update `docs/python/db-schema.md`**

In the `job_applications` section, after the existing bullets about `posting_snapshot`, add:

```markdown
- **Phase 3 (resume intelligence)** added seven columns: `resume_strategy` (JSONB, stage-4
  strategy output written by `resume_agent.py --propose`), `resume_file_ref` /
  `cover_letter_file_ref` (Supabase Storage paths, written by `--build`), `resume_variant`
  (traceability), and `source_channel` / `response_date` / `outcome` (outcome-tracking columns,
  schema-only in this phase -- not written by any code yet, for manual or future-phase use).
- `set_resume_strategy`, `set_resume_files`, and `upload_resume_file` (writes to the `resumes`
  Storage bucket) are the Phase 3 accessors in `db.py`. `upload_resume_file` is **not**
  best-effort -- it raises on failure, unlike most of this file's other accessors, since a silent
  failure here would mean `job_applications` pointing at a file that doesn't exist in Storage.
```

- [ ] **Step 4: Update the buildout spec's Phase 3 pointer**

In `docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md`, change the Phase 3
stub heading's status. Find the line `## Phase 3 — Resume intelligence (stub)` and change it to:

```markdown
## Phase 3 — Resume intelligence (shipped 2026-08-29)

Full design: `docs/superpowers/specs/2026-08-29-phase3-resume-intelligence-design.md`. Plan:
`docs/superpowers/plans/2026-08-29-phase3-resume-intelligence.md`. Absorbed the Phase 2.5 "resume
generation" bullet below -- Phase 2.5 now only needs to add the actual submit action on top of the
documents this phase produces.
```

- [ ] **Step 5: Add a memory entry**

Write `~/.claude/projects/-Users-kishoretheeraj-Documents-cold-email-agent/memory/project-phase3-resume-intelligence.md`
following the existing memory file format (frontmatter with `name`, `description`,
`metadata.type: project`), summarizing: shipped date, the two-command propose/build CLI with the
strategy-approval gate, data transcribed from the user's own corpus spec (not fabricated), the
three unresolved metric conflicts requiring the user's one-time decision, the AI-detection-evasion
request that was declined (with the reasoning), and that Phase 2.5 (auto-apply) now only needs the
submit action. Add one line to `MEMORY.md`'s index.

(Skip this step entirely if running under `build-continue.yml` — the memory path doesn't exist in
that environment. Note in the commit message instead that memory needs updating in an interactive
session.)

- [ ] **Step 6: Run the full test suite one last time**

Run: `python3 -m pytest`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md docs/python/db-schema.md docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md
git commit -m "docs: close out Phase 3 resume intelligence plan"
```
