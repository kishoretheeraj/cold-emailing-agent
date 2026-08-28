# Job & Company Discovery (Phase 2 — ATS path only) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **For the hourly auto-continue workflow:** find the first `- [ ]` below (top to bottom across
> all tasks) and execute only that task's remaining steps, then stop. Do not skip ahead. This
> plan covers the ATS-scan half of Phase 2 only. The JobRight puller is a **separate** plan file
> (`docs/superpowers/plans/2026-08-27-jobright-puller.md`, not yet written — it needs interactive
> reconnaissance of JobRight's real endpoints first, which this workflow cannot do). Do not start
> that work from this file; when every task below is checked, stop and let a human write that plan.

**Goal:** Persist currently-open job postings from companies already known to this repo (via
`contacts` and `company_intel`) into `job_applications` at `stage='saved'`, reusing the existing
`ats.py` Greenhouse/Ashby/Lever cascade instead of writing a new scraper. Read-only/discovery
only — this plan never submits an application.

**Architecture:** One new manual (not-in-cron) script, `job_discovery.py`, mirroring
`visa_match_new.py`'s shape (own log file, per-company `try/except` isolation,
`db.record_run(..., source="job_discovery")`). It builds a company universe from `contacts` +
`company_intel`, calls a lightly-extended `ats.fetch_jobs(company, max_jobs=...)` per company,
filters by a new `target_roles` prompts key, and persists via a dedup-aware
`db.create_job_application`.

**Tech Stack:** Python 3.11 / pytest / pytest-mock. No frontend changes — `job_applications` rows
created here show up on the existing `/applications` page (Phase 1) with no code changes there.

**Spec:** `docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md` (Phase 2
section and "JobRight scheduling" section — the latter belongs to the sibling JobRight plan, not
this one).

## Global Constraints

- Python: no type annotations, no docstrings on `_prefixed` helpers, public functions get a
  one-line docstring, `f""` strings for log lines, `# ── Section ──...` banners for file sections.
- `logging.basicConfig` for `job_discovery.py` goes inside `if __name__ == "__main__":`, not at
  module top — it's a standalone leaf script never imported by `agent.py`/`monitor.py`, so the
  root-logger race the CLAUDE.md logging invariant warns about doesn't apply here. Follow
  `visa_match_new.py`'s exact shape (own `.log` file, `%(asctime)s | %(message)s` format,
  `%Y-%m-%d %H:%M` datefmt).
- New log marker: `[DISCOVERY]`, used only in `job_discovery.py`. Every `ats.py`-internal log line
  keeps its existing `[RESEARCH-A]` marker unchanged — `ats.py` doesn't know or care which caller
  (research pipeline or discovery) invoked it, and changing that would be out of scope.
- `ats.fetch_jobs`/`_rank_jobs` changes must be **fully backward compatible**: `research.py`'s
  existing call site (`ats.fetch_jobs(company, role=contact.get("role"))`) must behave
  byte-identically after this plan — verified by the existing `test_ats.py` suite passing
  unmodified except for the two new tests this plan adds.
- Every per-company and per-posting operation in `job_discovery.py` gets its own `try/except` —
  one company's ATS failure or one posting's insert failure must never stop the rest of the scan.
  Same posture as `visa_match_new.py` and `ingest_form_d.py`.
- Migrations are additive only: `IF NOT EXISTS`, nullable/defaulted columns or a partial index, no
  backfill, no destructive change to any existing table or row.
- Definition of done (root CLAUDE.md): every task ends with tests green (`python3 -m pytest`),
  then (on the last task) `CLAUDE.md` and memory updated, before the final commit.

---

## Task 1: Migration — unique index on `job_applications.job_url`

**Files:**
- Create: `supabase/migrations/20260827000000_add_job_applications_job_url_unique_index.sql`

**Interfaces:**
- Produces: a partial unique index so two rows can never share a non-null `job_url`. Task 5's
  `db.py` dedup check relies on this as the DB-level backstop behind its own select-before-insert.

- [ ] **Step 1: Write the migration**

```sql
-- job_applications.job_url dedup backstop.
--
-- Sub-project: full-fledged buildout, Phase 2 (job & company discovery). job_discovery.py scans
-- the same companies repeatedly (every manual run), so without this a repeated scan would create
-- duplicate 'saved' rows for postings already seen. db.create_job_application does its own
-- select-before-insert check (application-level dedup); this partial unique index is the
-- database-level backstop against a race between two concurrent inserts for the same job_url.
-- Partial (WHERE job_url IS NOT NULL) because job_url is nullable -- e.g. a manually-entered
-- application with no posting link -- and multiple NULLs must not collide.

CREATE UNIQUE INDEX IF NOT EXISTS idx_job_applications_job_url_unique
  ON job_applications(job_url)
  WHERE job_url IS NOT NULL;
```

- [ ] **Step 2: Push the migration**

Run: `supabase db push`
Expected: migration applies cleanly with no errors (additive-only index, no data at risk).

- [ ] **Step 3: Verify the index exists**

Run: `supabase db query --linked "select indexname from pg_indexes where tablename = 'job_applications';"`
(note: `supabase db execute` is not a real subcommand — `db query --linked` is correct, per the
Phase 1 plan's Task 1.)
Expected: includes `idx_job_applications_job_url_unique` alongside the existing stage/contact_id
indexes.

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/20260827000000_add_job_applications_job_url_unique_index.sql
git commit -m "feat: add unique index on job_applications.job_url for discovery dedup"
```

---

## Task 2: Migration — `target_roles` prompts row

**Files:**
- Create: `supabase/migrations/20260827000001_add_target_roles_prompt.sql`
- Modify: `docs/python/prompt-keys.md`

**Interfaces:**
- Produces: a `prompts` row with `key='target_roles'`, newline-delimited role strings. Task 6's
  `job_discovery.py` reads it via `db.load_prompts()["target_roles"]`.

- [ ] **Step 1: Write the migration**

```sql
-- target_roles prompts row — full-fledged buildout, Phase 2 (job & company discovery).
--
-- Every existing "role" key in config.py/prompts means the CONTACT's role (used to personalize
-- outreach). This is the first key that means the USER's own target role(s) — job_discovery.py
-- filters scanned postings against it. Newline-delimited, same convention as
-- guardrail_company_list/forbidden_phrases (sort_orders 62-63); this is sort_order 65, one past
-- voice_dna (64), the highest existing key.
--
-- Seeded with a single reasonable default. Edit via the contact-manager's Prompts page — no code
-- change needed to change it, same as every other schema-driven prompt.

INSERT INTO prompts (key, value, display_title, description, default_value, sort_order, updated_at)
VALUES
  ('target_roles',
   'Product Manager',
   'Target roles (job discovery)',
   'Newline-delimited list of role titles job_discovery.py matches scanned ATS postings against. A posting matches if its title shares any word with any line here. Empty means match everything (no filtering).',
   'Product Manager',
   65,
   now())
ON CONFLICT (key) DO NOTHING;
```

- [ ] **Step 2: Push the migration**

Run: `supabase db push`
Expected: migration applies cleanly; `select key, sort_order from prompts where key = 'target_roles';`
returns one row.

- [ ] **Step 3: Update `docs/python/prompt-keys.md`**

Change the header line `# Prompt keys (Supabase prompts table — 24 rows)` to `— 25 rows)` and add
a row to the table, immediately after the `voice_dna` row:

```markdown
| `target_roles` | 65 | Newline-delimited role titles `job_discovery.py` filters ATS postings against; empty means match everything |
```

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/20260827000001_add_target_roles_prompt.sql docs/python/prompt-keys.md
git commit -m "feat: add target_roles prompt for job discovery filtering"
```

---

## Task 3: `config.py` — discovery job cap

**Files:**
- Modify: `config.py`

**Interfaces:**
- Produces: `config.ATS_DISCOVERY_MAX_JOBS` (int). Consumed by Task 6's `job_discovery.py`.

- [ ] **Step 1: Add the constant**

In `config.py`, in the `# ── ATS career-page enrichment ──` section, right after `ATS_MAX_SLUG_CANDIDATES = 2`:

```python
# Discovery (job_discovery.py) wants every currently-open posting for a company, not
# just the single best match for one contact's role — this cap is deliberately higher
# than ATS_MAX_JOBS (which sizes a research-brief snippet, not a discovery scan).
ATS_DISCOVERY_MAX_JOBS = 25
```

- [ ] **Step 2: Commit**

```bash
git add config.py
git commit -m "feat: add ATS_DISCOVERY_MAX_JOBS config constant"
```

(No test file for this step alone — `config.py` constants are covered by the tests that consume
them in Task 4.)

---

## Task 4: `ats.py` — thread `max_jobs` through `fetch_jobs`/`_rank_jobs`

**Files:**
- Modify: `ats.py:214-257` (`_rank_jobs` and `fetch_jobs`)
- Test: `tests/test_ats.py` (append)

**Interfaces:**
- Consumes: `config.ATS_MAX_JOBS` (existing), `config.ATS_DISCOVERY_MAX_JOBS` (Task 3, not
  referenced inside `ats.py` itself — the caller passes the number in).
- Produces: `fetch_jobs(company, role=None, max_jobs=None) -> list[dict]` — `max_jobs=None` falls
  back to `config.ATS_MAX_JOBS`, preserving today's behavior for `research.py`'s existing call
  site byte-for-byte. Task 6's `job_discovery.py` calls `fetch_jobs(company, max_jobs=config.ATS_DISCOVERY_MAX_JOBS)`
  with **no** `role` argument (passing one would engage relevance sorting discovery doesn't want —
  discovery wants every posting in source order, then filters by target role itself).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ats.py`:

```python
def test_max_jobs_param_overrides_config_default(mocker):
    _routed_http(mocker, {"greenhouse": _GREENHOUSE_PAYLOAD})
    assert len(ats.fetch_jobs("Acme", max_jobs=1)) == 1


def test_max_jobs_none_falls_back_to_config_default(mocker):
    mocker.patch.object(config, "ATS_MAX_JOBS", 1)
    _routed_http(mocker, {"greenhouse": _GREENHOUSE_PAYLOAD})
    assert len(ats.fetch_jobs("Acme")) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_ats.py::test_max_jobs_param_overrides_config_default tests/test_ats.py::test_max_jobs_none_falls_back_to_config_default -v`
Expected: `test_max_jobs_param_overrides_config_default` FAILS with `TypeError: fetch_jobs() got an
unexpected keyword argument 'max_jobs'`. `test_max_jobs_none_falls_back_to_config_default` PASSES
already (it's exercising unchanged behavior) — that's fine, it's here to lock in the fallback once
Step 3 lands.

- [ ] **Step 3: Implement**

In `ats.py`, replace `_rank_jobs` and `fetch_jobs`:

```python
def _rank_jobs(jobs, role, max_jobs):
    role_tokens = _tokens(role)
    if not role_tokens:
        return jobs[:max_jobs]
    ordered = sorted(
        enumerate(jobs),
        key=lambda pair: (-len(role_tokens & _tokens(pair[1].get("title"))), pair[0]),
    )
    return [job for _, job in ordered][:max_jobs]


# ── Public entry point ─────────────────────────────────────────────────────────

def fetch_jobs(company, role=None, max_jobs=None):
    """
    Return up to `max_jobs` (default config.ATS_MAX_JOBS) active job postings for
    `company`, ranked by relevance to `role` when given, from the first public ATS
    that recognises the company.

    Returns [] when the company is not on a supported ATS, when ATS_ENABLED is
    off, or on any failure. Never raises -- enrichment must never cost a draft.
    """
    try:
        if not config.ATS_ENABLED:
            return []

        cap = max_jobs if max_jobs is not None else config.ATS_MAX_JOBS

        candidates = _slug_candidates(company)
        if not candidates:
            return []

        for source, template, parser in _PROVIDERS:
            for slug in candidates:
                jobs = _try_provider(source, template, parser, slug)
                if jobs:
                    ranked = _rank_jobs(jobs, role, cap)
                    log.info(
                        f"[RESEARCH-A] | {company} | source={source} | "
                        f"slug={slug} | found={len(jobs)} | kept={len(ranked)}"
                    )
                    return ranked

        log.info(f"[RESEARCH-A] | {company} | no_ats_match | candidates={len(candidates)}")
        return []
    except Exception as exc:
        log.warning(f"[RESEARCH-A] | {company} | unexpected error: {exc}")
        return []
```

(Only the signature and the two call sites of `_rank_jobs`/cap changed — everything else in the
function body is unchanged from today.)

- [ ] **Step 4: Run the full ATS test suite**

Run: `python3 -m pytest tests/test_ats.py tests/test_research_ats.py -v`
Expected: all tests PASS, including the two new ones and every existing test unmodified.

- [ ] **Step 5: Commit**

```bash
git add ats.py tests/test_ats.py
git commit -m "feat: add max_jobs param to ats.fetch_jobs for discovery scans"
```

---

## Task 5: `db.py` — dedup-aware `create_job_application` + `get_all_company_intel_names`

**Files:**
- Modify: `db.py:532-547` (`create_job_application`)
- Modify: `db.py` (append new function near the `company_intel` accessors, ~line 528)
- Test: `tests/test_job_applications_db.py` (modify + append)
- Test: `tests/test_visa_intel_db.py` (append — this is where the existing `company_intel`
  accessor tests already live, per CLAUDE.md's test-file list)

**Interfaces:**
- Consumes: `get_client()`, `_retry(fn)` (both already in `db.py`).
- Produces: `create_job_application(...) -> dict | None` — now returns `None` in two cases (empty
  insert result, unchanged; OR `job_url` already exists on another row, new) instead of one.
  `get_all_company_intel_names() -> list[str]` — flattens every `company_intel.raw_company_names`
  array into one list, duplicates and casing preserved (Task 6's `_company_universe` does its own
  dedup across this and `contacts.company`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_job_applications_db.py`, first fix the existing test that will break once the dedup
check lands (it passes a `job_url` but never mocks the new `select` call the dedup check makes) —
replace `test_create_job_application_passes_optional_fields` with:

```python
def test_create_job_application_passes_optional_fields(fake_client):
    fake_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
    fake_client.table.return_value.insert.return_value.execute.return_value.data = [{"id": 2}]
    db.create_job_application(
        company="Acme", role="PM", job_url="https://x", source="manual",
        contact_id=5, applied_date="2026-08-26", notes="hi",
        posting_snapshot={"salary": "150k"},
    )
    inserted = fake_client.table.return_value.insert.call_args[0][0]
    assert inserted["job_url"] == "https://x"
    assert inserted["source"] == "manual"
    assert inserted["contact_id"] == 5
    assert inserted["applied_date"] == "2026-08-26"
    assert inserted["notes"] == "hi"
    assert inserted["posting_snapshot"] == {"salary": "150k"}
```

Then append two new tests to the same file:

```python
def test_create_job_application_skips_when_job_url_already_exists(fake_client):
    fake_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"id": 9}
    ]
    result = db.create_job_application(company="Acme", role="PM", job_url="https://x")
    assert result is None
    fake_client.table.return_value.insert.assert_not_called()


def test_create_job_application_skips_dedup_check_when_no_job_url(fake_client):
    fake_client.table.return_value.insert.return_value.execute.return_value.data = [{"id": 3}]
    result = db.create_job_application(company="Acme", role="PM")
    fake_client.table.return_value.select.assert_not_called()
    assert result["id"] == 3
```

Append to `tests/test_visa_intel_db.py` (following its existing `fake_client` fixture pattern):

```python
def test_get_all_company_intel_names_flattens_raw_company_names(fake_client):
    fake_client.table.return_value.select.return_value.execute.return_value.data = [
        {"raw_company_names": ["Acme Inc", "Acme"]},
        {"raw_company_names": ["Globex"]},
        {"raw_company_names": None},
    ]
    result = db.get_all_company_intel_names()
    assert result == ["Acme Inc", "Acme", "Globex"]


def test_get_all_company_intel_names_returns_empty_list_on_no_rows(fake_client):
    fake_client.table.return_value.select.return_value.execute.return_value.data = []
    assert db.get_all_company_intel_names() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_job_applications_db.py tests/test_visa_intel_db.py -v`
Expected: the two new `create_job_application` tests FAIL (no dedup check exists yet); the two new
`get_all_company_intel_names` tests FAIL with `AttributeError: module 'db' has no attribute
'get_all_company_intel_names'`. The rewritten `test_create_job_application_passes_optional_fields`
PASSES already (it's not exercising new behavior yet, just new mock setup) — that's fine.

- [ ] **Step 3: Implement**

In `db.py`, replace `create_job_application`:

```python
def create_job_application(company, role, job_url=None, source=None, contact_id=None,
                            applied_date=None, notes=None, posting_snapshot=None):
    """Create a new job application row at stage 'saved'. Returns None if job_url is
    already present on another row (dedup) or if the insert returns no row."""
    if job_url:
        existing = _retry(lambda: get_client().table("job_applications")
                           .select("id").eq("job_url", job_url).execute())
        if existing.data:
            return None
    payload = {
        "company": company,
        "role": role,
        "job_url": job_url,
        "source": source,
        "contact_id": contact_id,
        "applied_date": applied_date,
        "notes": notes,
        "posting_snapshot": posting_snapshot,
        "stage": "saved",
    }
    result = _retry(lambda: get_client().table("job_applications").insert(payload).execute())
    return result.data[0] if result.data else None
```

Add `get_all_company_intel_names` near the other `company_intel` accessors (after
`upsert_company_funding`, before the `# ── Job application tracking ──` banner):

```python
def get_all_company_intel_names():
    """Flatten raw_company_names across every company_intel row into one list."""
    result = _retry(lambda: get_client().table("company_intel").select("raw_company_names").execute())
    names = []
    for row in (result.data or []):
        names.extend(row.get("raw_company_names") or [])
    return names
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_job_applications_db.py tests/test_visa_intel_db.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full test suite**

Run: `python3 -m pytest`
Expected: all green — this touches a shared function (`create_job_application`), so confirm
nothing else in the suite calls it with assumptions the dedup check breaks.

- [ ] **Step 6: Commit**

```bash
git add db.py tests/test_job_applications_db.py tests/test_visa_intel_db.py
git commit -m "feat: dedup create_job_application by job_url, add get_all_company_intel_names"
```

---

## Task 6: `job_discovery.py` — orchestration script

**Files:**
- Create: `job_discovery.py`
- Test: `tests/test_job_discovery.py`

**Interfaces:**
- Consumes: `db.get_all_contacts()`, `db.get_all_company_intel_names()`, `db.load_prompts()`,
  `db.create_job_application(...)`, `db.record_run(...)` (all existing/Task 5), `ats.fetch_jobs(company, max_jobs=...)`
  (Task 4), `config.ATS_DISCOVERY_MAX_JOBS` (Task 3).
- Produces: `run()` — the script's sole public entry point, called from `__main__`. No return
  value; effects are `job_applications` rows and one `agent_runs` row via `db.record_run`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_job_discovery.py`:

```python
"""Tests for job_discovery.py."""

from unittest.mock import MagicMock

import pytest

import db
import job_discovery


@pytest.fixture(autouse=True)
def no_real_calls(mocker):
    mocker.patch.object(db, "record_run")


def test_company_universe_dedupes_case_insensitively(mocker):
    mocker.patch.object(db, "get_all_contacts", return_value=[
        {"company": "Acme"}, {"company": "acme"}, {"company": ""}, {"company": None},
    ])
    mocker.patch.object(db, "get_all_company_intel_names", return_value=["ACME", "Globex"])
    universe = job_discovery._company_universe()
    assert universe == ["Acme", "Globex"]


def test_target_role_token_sets_splits_lines_and_drops_blank_lines(mocker):
    result = job_discovery._target_role_token_sets("Product Manager\n\n  Data Scientist  \n")
    assert result == [{"product", "manager"}, {"data", "scientist"}]


def test_target_role_token_sets_handles_none_and_empty(mocker):
    assert job_discovery._target_role_token_sets(None) == []
    assert job_discovery._target_role_token_sets("") == []


def test_matches_target_roles_true_on_any_token_overlap():
    job = {"title": "Senior Product Manager, Growth"}
    role_token_sets = [{"product", "manager"}]
    assert job_discovery._matches_target_roles(job, role_token_sets) is True


def test_matches_target_roles_false_on_no_overlap():
    job = {"title": "Staff Software Engineer"}
    role_token_sets = [{"product", "manager"}]
    assert job_discovery._matches_target_roles(job, role_token_sets) is False


def test_matches_target_roles_true_for_everything_when_no_roles_configured():
    job = {"title": "Anything At All"}
    assert job_discovery._matches_target_roles(job, []) is True


def test_run_persists_matching_jobs_and_records_run(mocker):
    mocker.patch.object(db, "load_prompts", return_value={"target_roles": "Product Manager"})
    mocker.patch.object(job_discovery, "_company_universe", return_value=["Acme"])
    mocker.patch.object(job_discovery.ats, "fetch_jobs", return_value=[
        {"title": "Product Manager", "url": "https://x/1", "location": "", "description": "", "source": "greenhouse"},
        {"title": "Software Engineer", "url": "https://x/2", "location": "", "description": "", "source": "greenhouse"},
    ])
    create = mocker.patch.object(db, "create_job_application", return_value={"id": 1})
    job_discovery.run()
    assert create.call_count == 1
    _, kwargs = create.call_args
    assert kwargs["company"] == "Acme"
    assert kwargs["role"] == "Product Manager"
    assert kwargs["job_url"] == "https://x/1"
    assert kwargs["source"] == "ats_scan"
    db.record_run.assert_called_once()
    assert db.record_run.call_args.kwargs["source"] == "job_discovery"


def test_run_counts_dedup_skip_separately_from_saved(mocker):
    mocker.patch.object(db, "load_prompts", return_value={"target_roles": ""})
    mocker.patch.object(job_discovery, "_company_universe", return_value=["Acme"])
    mocker.patch.object(job_discovery.ats, "fetch_jobs", return_value=[
        {"title": "Anything", "url": "https://x/1", "location": "", "description": "", "source": "greenhouse"},
    ])
    mocker.patch.object(db, "create_job_application", return_value=None)
    job_discovery.run()
    args, kwargs = db.record_run.call_args
    assert args[0] == "success"
    assert args[1] == 0   # saved
    assert args[2] == 1   # skipped (dedup)
    assert args[3] == 0   # errors


def test_run_isolates_one_companys_ats_failure_from_the_rest(mocker):
    mocker.patch.object(db, "load_prompts", return_value={"target_roles": ""})
    mocker.patch.object(job_discovery, "_company_universe", return_value=["Broken", "Acme"])

    def fake_fetch(company, max_jobs=None):
        if company == "Broken":
            raise RuntimeError("boom")
        return [{"title": "Anything", "url": "https://x/1", "location": "", "description": "", "source": "greenhouse"}]

    mocker.patch.object(job_discovery.ats, "fetch_jobs", side_effect=fake_fetch)
    mocker.patch.object(db, "create_job_application", return_value={"id": 1})
    job_discovery.run()
    args, kwargs = db.record_run.call_args
    assert args[1] == 1   # saved (Acme still processed)
    assert args[3] == 1   # errors (Broken counted, did not stop the run)


def test_run_isolates_one_postings_persist_failure_from_the_rest(mocker):
    mocker.patch.object(db, "load_prompts", return_value={"target_roles": ""})
    mocker.patch.object(job_discovery, "_company_universe", return_value=["Acme"])
    mocker.patch.object(job_discovery.ats, "fetch_jobs", return_value=[
        {"title": "First", "url": "https://x/1", "location": "", "description": "", "source": "greenhouse"},
        {"title": "Second", "url": "https://x/2", "location": "", "description": "", "source": "greenhouse"},
    ])
    mocker.patch.object(db, "create_job_application", side_effect=[RuntimeError("boom"), {"id": 2}])
    job_discovery.run()
    args, kwargs = db.record_run.call_args
    assert args[1] == 1   # saved (Second succeeded)
    assert args[3] == 1   # errors (First's persist failure counted, did not stop Second)


def test_run_with_empty_universe_records_success_and_does_nothing(mocker):
    mocker.patch.object(db, "load_prompts", return_value={"target_roles": ""})
    mocker.patch.object(job_discovery, "_company_universe", return_value=[])
    create = mocker.patch.object(db, "create_job_application")
    job_discovery.run()
    create.assert_not_called()
    args, kwargs = db.record_run.call_args
    assert args[0] == "success"


def test_run_survives_load_prompts_failure(mocker):
    mocker.patch.object(db, "load_prompts", side_effect=RuntimeError("supabase down"))
    mocker.patch.object(job_discovery, "_company_universe", return_value=["Acme"])
    mocker.patch.object(job_discovery.ats, "fetch_jobs", return_value=[
        {"title": "Anything", "url": "https://x/1", "location": "", "description": "", "source": "greenhouse"},
    ])
    mocker.patch.object(db, "create_job_application", return_value={"id": 1})
    job_discovery.run()   # must not raise
    args, kwargs = db.record_run.call_args
    assert args[0] == "success"
    assert args[1] == 1   # target_roles defaulted to "match everything" on load_prompts failure


def test_run_survives_company_universe_failure(mocker):
    mocker.patch.object(db, "load_prompts", return_value={})
    mocker.patch.object(job_discovery, "_company_universe", side_effect=RuntimeError("supabase down"))
    job_discovery.run()   # must not raise
    args, kwargs = db.record_run.call_args
    assert args[0] == "failure"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_job_discovery.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'job_discovery'` (file doesn't exist
yet).

- [ ] **Step 3: Implement**

Create `job_discovery.py`:

```python
"""
Scans public ATS job boards (via ats.py) for companies already known to this repo
-- contacts and company_intel -- and persists open postings that match the user's
target roles into job_applications at stage='saved'.

Run manually, not from the daily cron. Read-only/discovery only -- never submits
an application. See docs/superpowers/specs/2026-08-26-full-fledged-job-platform-
buildout.md, Phase 2.

Usage: python3 job_discovery.py
"""

import logging
import re
import time

import ats
import config
import db

log = logging.getLogger(__name__)

_STOPWORDS = frozenset({
    "a", "an", "and", "at", "for", "in", "of", "on", "the", "to", "with",
})


def _tokens(text):
    if not text or not isinstance(text, str):
        return set()
    words = re.split(r"[^a-z0-9]+", text.lower())
    return {w for w in words if w and w not in _STOPWORDS}


def _company_universe():
    seen = set()
    universe = []
    contacts = db.get_all_contacts()
    intel_names = db.get_all_company_intel_names()
    for name in [c.get("company") for c in contacts] + intel_names:
        if not isinstance(name, str):
            continue
        name = name.strip()
        key = name.lower()
        if key and key not in seen:
            seen.add(key)
            universe.append(name)
    return universe


def _target_role_token_sets(target_roles_text):
    token_sets = []
    for line in (target_roles_text or "").splitlines():
        tokens = _tokens(line)
        if tokens:
            token_sets.append(tokens)
    return token_sets


def _matches_target_roles(job, role_token_sets):
    if not role_token_sets:
        return True
    title_tokens = _tokens(job.get("title"))
    return any(tokens & title_tokens for tokens in role_token_sets)


def run():
    """Scan known companies' ATS boards and persist matching postings as saved applications."""
    start = time.time()
    saved = 0
    skipped = 0
    errors = 0

    try:
        prompts = db.load_prompts()
    except Exception as exc:
        log.warning(f"[DISCOVERY] | load_prompts failed, matching all postings: {exc}")
        prompts = {}
    role_token_sets = _target_role_token_sets(prompts.get("target_roles"))
    if not role_token_sets:
        log.info("[DISCOVERY] | target_roles empty or unavailable, matching all postings")

    try:
        universe = _company_universe()
    except Exception as exc:
        log.warning(f"[DISCOVERY] | company universe build failed: {exc}")
        db.record_run("failure", 0, 0, 1, round(time.time() - start),
                       failure_reason=str(exc), source="job_discovery")
        return

    log.info(f"[DISCOVERY] | START | companies={len(universe)}")

    for company in universe:
        try:
            jobs = ats.fetch_jobs(company, max_jobs=config.ATS_DISCOVERY_MAX_JOBS)
        except Exception as exc:
            log.warning(f"[DISCOVERY] | {company} | fetch_jobs error: {exc}")
            errors += 1
            continue

        matched = [j for j in jobs if _matches_target_roles(j, role_token_sets)]
        for job in matched:
            try:
                result = db.create_job_application(
                    company=company,
                    role=job["title"],
                    job_url=job.get("url") or None,
                    source="ats_scan",
                    posting_snapshot=job,
                )
                if result is None:
                    skipped += 1
                else:
                    saved += 1
            except Exception as exc:
                log.warning(f"[DISCOVERY] | {company} | {job.get('title')} | persist error: {exc}")
                errors += 1

    log.info(f"[DISCOVERY] | DONE | saved={saved} | skipped={skipped} | errors={errors}")
    db.record_run("success", saved, skipped, errors, round(time.time() - start), source="job_discovery")


if __name__ == "__main__":
    logging.basicConfig(
        filename="job_discovery.log",
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M",
    )
    run()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_job_discovery.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full test suite**

Run: `python3 -m pytest`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add job_discovery.py tests/test_job_discovery.py
git commit -m "feat: add job_discovery.py to persist ATS postings into job_applications"
```

---

## Task 7: Docs — close out this plan

**Files:**
- Modify: `CLAUDE.md` (root)
- Modify: `docs/python/db-schema.md`
- Modify: `docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md`

**Interfaces:** none — documentation only.

- [ ] **Step 1: Update the root `CLAUDE.md` module layout list**

Add `job_discovery.py` to the module-layout code block (## Module layout), right before
`supabase/migrations/`.

- [ ] **Step 2: Add a `job_discovery.py` section to root `CLAUDE.md`**

Add a new `##` section (placed after the "Job application tracking" section) with this content:

```markdown
## Job discovery (full-fledged buildout, Phase 2 — ATS path)

`job_discovery.py` (manual, **not** in cron) scans the public ATS boards of every company already
known via `contacts` or `company_intel` (`db.get_all_company_intel_names()` flattens
`company_intel.raw_company_names`) using `ats.fetch_jobs(company, max_jobs=config.ATS_DISCOVERY_MAX_JOBS)`
— no `role` argument, since discovery wants every open posting in source order, not `ats.py`'s
single-best-match ranking. Results are filtered against the `target_roles` prompts key (any word
overlap with any target-role line counts as a match; an empty/missing key matches everything) and
persisted via `db.create_job_application(..., source='ats_scan')` at `stage='saved'`.

`db.create_job_application` is dedup-aware: it skips (returns `None`, callers must not treat that
as an error) any `job_url` that already exists on another row, backed by a partial unique index
(`idx_job_applications_job_url_unique`, `WHERE job_url IS NOT NULL`) as the database-level
race-condition backstop.

Every per-company and per-posting operation is independently `try/except`-wrapped — one company's
ATS failure or one posting's insert failure never stops the rest of the scan, same posture as
`visa_match_new.py`/`ingest_form_d.py`. Log marker `[DISCOVERY]`, own log file (`job_discovery.log`).

The JobRight puller (a second Phase 2 source, tagged `source='jobright'`) is a separate module and
plan — see `docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md`.
```

- [ ] **Step 3: Update `docs/python/db-schema.md`**

In the `job_applications` section, after the existing "Accessors in `db.py`" bullet, add:

```markdown
- `create_job_application` is dedup-aware as of Phase 2: it skips (returns `None`) when `job_url`
  is already present on another row, backed by a partial unique index
  (`idx_job_applications_job_url_unique`, `WHERE job_url IS NOT NULL`).
- `get_all_company_intel_names()` (defined alongside the other `company_intel` accessors) flattens
  every row's `raw_company_names` array into one list — used by `job_discovery.py` to build its
  company-scan universe alongside `contacts.company`.
```

- [ ] **Step 4: Update the spec's Phase 2 section to point at both plan files**

In `docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md`, change the line

```
**Plan:** `docs/superpowers/plans/2026-08-27-job-discovery.md`
```

to

```
**Plans:** `docs/superpowers/plans/2026-08-27-job-discovery.md` (ATS scan — this section) and
`docs/superpowers/plans/2026-08-27-jobright-puller.md` (JobRight puller — not yet written; needs
interactive reconnaissance of JobRight's actual endpoints before a TDD plan can be written against
real request/response shapes instead of guessed ones).
```

- [ ] **Step 5: Add a memory entry**

Write `~/.claude/projects/-Users-kishoretheeraj-Documents-cold-email-agent/memory/project-job-discovery-phase2.md`
following the existing memory file format (frontmatter with `name`, `description`,
`metadata.type: project`), summarizing: `job_discovery.py` shipped (date), reuses `ats.py`'s
existing cascade via a new `max_jobs` param rather than a new scraper, `target_roles` prompts key,
dedup-by-`job_url`, and that the JobRight puller was deliberately split into its own plan pending
reconnaissance. Add one line to `MEMORY.md`'s index.

(Skip this step entirely if running under `build-continue.yml` — the memory path doesn't exist in
that environment. Note in the commit message instead that memory needs updating in an interactive
session.)

- [ ] **Step 6: Run the full test suite one last time**

Run: `python3 -m pytest`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md docs/python/db-schema.md docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md
git commit -m "docs: close out Phase 2 ATS discovery plan"
```
