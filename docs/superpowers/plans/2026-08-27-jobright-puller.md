# JobRight Puller (Phase 2 — JobRight source) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **For the hourly auto-continue workflow:** this plan needs `JOBRIGHT_EMAIL`/`JOBRIGHT_PASSWORD`
> set locally to smoke-test the login flow (Task 2, Step 5) — the unattended CI environment does
> not have these and cannot do that step. If you are `build-continue.yml`: implement Tasks 1-2 with
> mocked tests only (skip Step 5 of Task 2 — leave it unchecked with a `<!-- blocked: no
> interactive JobRight credentials in CI -->` note, per your own instructions on capability gaps),
> and leave Task 4 (GitHub Actions secrets) entirely alone — that step requires the user's own
> `gh secret set` invocation, which you cannot do either. Stop after Task 2 if you hit the blocked
> step; do not attempt Tasks 3-5.

**Goal:** Pull the user's personalized JobRight.ai job recommendations via the real (unofficial,
session-cookie) endpoints found through live reconnaissance, and persist them into
`job_applications` at `stage='saved'`, `source='jobright'` — the second Phase 2 discovery source,
alongside `job_discovery.py`'s ATS scan.

**Architecture:** One new manual-by-default script, `jobright.py`, using only the Python standard
library (`urllib.request` + `http.cookiejar`, no new dependency — same posture as `ats.py`). It
logs in via a real POST to JobRight's native email+password endpoint, verifies the resulting
session cookie, paginates the job-recommendation endpoint, normalizes each posting, and persists
matches through the same `db.create_job_application` dedup path `job_discovery.py` uses. Also
wired into a new scheduled workflow, `jobright_pull.yml` (daily), per the user's explicit override
of the original manual-only rule — see the spec's "JobRight scheduling" section.

**Endpoints (confirmed real via live reconnaissance, 2026-08-27 — not guessed):**
- `POST https://jobright.ai/swan/auth/login/pwd` — body `{"email": ..., "password": ...,
  "from": "homepage"}`, cookie-authenticates the session via the response's `Set-Cookie` header.
- `GET https://jobright.ai/swan/auth/newinfo` — returns `{"result": {"logined": true/false, ...}}`,
  used to verify the session before scanning.
- `GET https://jobright.ai/swan/recommend/list/jobs?refresh=true&sortCondition=0&position=<n>&count=<n>&syncRerank=false`
  — returns `{"success": bool, "result": {"jobList": [{"jobResult": {"jobTitle", "jobLocation",
  "workModel", "originalUrl", "applyLink", "jobSummary", ...}, "companyResult": {"companyName",
  ...}}]}}`.

**Tech Stack:** Python 3.11 / pytest / pytest-mock, stdlib `urllib`/`http.cookiejar` only. No
frontend changes (same reasoning as the ATS plan — `job_applications` rows show up on the existing
`/applications` page regardless of `source`).

**Spec:** `docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md` — the "JobRight
puller" and "JobRight scheduling" sections, and the 2026-08-27 correction notes recording the
reconnaissance findings.

## Global Constraints

- Python: no type annotations, no docstrings on `_prefixed` helpers, public functions get a
  one-line docstring, `f""` strings for log lines, `# ── Section ──...` banners.
- **Never log, print, persist, or commit `JOBRIGHT_EMAIL`/`JOBRIGHT_PASSWORD` values, session
  cookies, or any response field containing them.** Read via `os.environ.get(...)` only.
- `JOBRIGHT_EMAIL`/`JOBRIGHT_PASSWORD` are **soft-optional** in `config.py` (`os.environ.get`, not
  `os.environ[...]`) — unlike the five core secrets, these must not become a hard-required
  import-time lookup, or every other script (`agent.py`, `monitor.py`, the whole test suite) breaks
  wherever they're unset. `jobright.py` itself checks for their presence and no-ops (returns `[]`,
  logs, does not raise) when absent.
- `jobright.py` is manual by default (`python3 jobright.py`), same shape as `visa_match_new.py`:
  `logging.basicConfig` inside `if __name__ == "__main__":`, not at module top (standalone leaf
  script, never imported by `agent.py`/`monitor.py`, so the root-logger race invariant doesn't
  apply). New log marker: `[JOBRIGHT]`, own log file (`jobright.log`).
- Every per-posting persist operation gets its own `try/except` — one posting's insert failure must
  never stop the rest of the run. Same posture as `job_discovery.py`/`visa_match_new.py`.
- `fetch_recommended_jobs()` must never raise past its own boundary — login failure, session
  failure, HTTP failure, or malformed response all degrade to `[]` plus a warning log. Matches
  `ats.fetch_jobs`'s "enrichment must never cost a draft" contract, generalized to "a discovery
  script must never crash a scheduled workflow."
- Definition of done (root CLAUDE.md): every task ends with tests green (`python3 -m pytest`), then
  (on the last task) `CLAUDE.md` and memory updated, before the final commit.
- **Nobody — human or agent — ever types the real `JOBRIGHT_EMAIL`/`JOBRIGHT_PASSWORD` values into
  chat or a shell command visible to an agent.** Task 4's secret-setting step is the user's own
  `gh secret set` invocation, run interactively in their own terminal (prompts for the value without
  echoing it) — never something to paste into this conversation.

---

## Task 1: `config.py` — JobRight constants

**Files:**
- Modify: `config.py`

**Interfaces:**
- Produces: `config.JOBRIGHT_EMAIL`, `config.JOBRIGHT_PASSWORD` (both `str | None`),
  `config.JOBRIGHT_TIMEOUT_SECONDS`, `config.JOBRIGHT_MAX_ATTEMPTS`,
  `config.JOBRIGHT_RETRY_BACKOFF_SECONDS`, `config.JOBRIGHT_PAGE_SIZE`, `config.JOBRIGHT_MAX_JOBS`,
  `config.JOBRIGHT_PAGE_DELAY_SECONDS`. Consumed by Task 2's `jobright.py`.

- [x] **Step 1: Add the constants**

At the end of `config.py`, add a new section:

```python
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
```

- [x] **Step 2: Commit**

```bash
git add config.py
git commit -m "feat: add JobRight puller config constants"
```

---

## Task 2: `jobright.py` — puller module

**Files:**
- Create: `jobright.py`
- Test: `tests/test_jobright.py`

**Interfaces:**
- Consumes: `config.JOBRIGHT_EMAIL`, `config.JOBRIGHT_PASSWORD`, `config.JOBRIGHT_TIMEOUT_SECONDS`,
  `config.JOBRIGHT_MAX_ATTEMPTS`, `config.JOBRIGHT_RETRY_BACKOFF_SECONDS`,
  `config.JOBRIGHT_PAGE_SIZE`, `config.JOBRIGHT_MAX_JOBS`, `config.JOBRIGHT_PAGE_DELAY_SECONDS`
  (Task 1), `db.create_job_application(...)`, `db.record_run(...)` (existing).
- Produces: `fetch_recommended_jobs() -> list[dict]` (each dict:
  `{title, location, url, description, company, source}`, `source` always `"jobright"`), `run()`
  (the script's sole entry point, called from `__main__`).

- [x] **Step 1: Write the failing tests**

Create `tests/test_jobright.py`:

```python
"""Tests for jobright.py. All HTTP is mocked -- no real network calls, no real credentials."""

import json
import urllib.error
from unittest.mock import MagicMock

import pytest

import config
import db
import jobright


def _fake_response(body_dict):
    resp = MagicMock()
    resp.read.return_value = json.dumps(body_dict).encode("utf-8")
    ctx = MagicMock()
    ctx.__enter__.return_value = resp
    ctx.__exit__.return_value = False
    return ctx


@pytest.fixture(autouse=True)
def no_real_calls(mocker):
    mocker.patch.object(db, "record_run")
    mocker.patch.object(config, "JOBRIGHT_EMAIL", "test@example.com")
    mocker.patch.object(config, "JOBRIGHT_PASSWORD", "test-password")
    mocker.patch.object(config, "JOBRIGHT_RETRY_BACKOFF_SECONDS", 0)
    mocker.patch.object(config, "JOBRIGHT_PAGE_DELAY_SECONDS", 0)


# ── _job_from_result ──────────────────────────────────────────────────────────

def test_job_from_result_builds_expected_shape():
    entry = {
        "jobResult": {
            "jobTitle": "Product Manager",
            "jobLocation": "Remote",
            "originalUrl": "https://example.com/job/1",
            "applyLink": "https://example.com/apply/1",
            "jobSummary": "Own the roadmap.",
        },
        "companyResult": {"companyName": "Acme"},
    }
    job = jobright._job_from_result(entry)
    assert job == {
        "title": "Product Manager",
        "location": "Remote",
        "url": "https://example.com/job/1",
        "description": "Own the roadmap.",
        "company": "Acme",
        "source": "jobright",
    }


def test_job_from_result_falls_back_to_apply_link_when_no_original_url():
    entry = {
        "jobResult": {"jobTitle": "PM", "applyLink": "https://example.com/apply/2"},
        "companyResult": {},
    }
    job = jobright._job_from_result(entry)
    assert job["url"] == "https://example.com/apply/2"
    assert job["company"] == ""


def test_job_from_result_returns_none_when_title_missing():
    assert jobright._job_from_result({"jobResult": {}, "companyResult": {}}) is None
    assert jobright._job_from_result({}) is None


# ── _request retry/backoff ────────────────────────────────────────────────────

def test_request_retries_on_5xx_then_succeeds(mocker):
    opener = MagicMock()
    opener.open.side_effect = [
        urllib.error.HTTPError("http://x", 500, "Server Error", None, None),
        _fake_response({"ok": True}),
    ]
    result = jobright._request(opener, "GET", "/swan/auth/newinfo")
    assert result == {"ok": True}
    assert opener.open.call_count == 2


def test_request_does_not_retry_on_4xx(mocker):
    opener = MagicMock()
    opener.open.side_effect = urllib.error.HTTPError("http://x", 401, "Unauthorized", None, None)
    with pytest.raises(urllib.error.HTTPError):
        jobright._request(opener, "GET", "/swan/auth/newinfo")
    assert opener.open.call_count == 1


def test_request_raises_after_max_attempts_on_repeated_failure(mocker):
    mocker.patch.object(config, "JOBRIGHT_MAX_ATTEMPTS", 2)
    opener = MagicMock()
    opener.open.side_effect = urllib.error.URLError("dns failure")
    with pytest.raises(urllib.error.URLError):
        jobright._request(opener, "GET", "/swan/auth/newinfo")
    assert opener.open.call_count == 2


# ── _login / _session_is_valid ────────────────────────────────────────────────

def test_login_returns_true_on_success(mocker):
    mocker.patch.object(jobright, "_request", return_value={"success": True})
    assert jobright._login(MagicMock(), "e", "p") is True


def test_login_returns_false_on_unsuccessful_response(mocker):
    mocker.patch.object(jobright, "_request", return_value={"success": False})
    assert jobright._login(MagicMock(), "e", "p") is False


def test_session_is_valid_reads_logined_field(mocker):
    mocker.patch.object(jobright, "_request", return_value={"result": {"logined": True}})
    assert jobright._session_is_valid(MagicMock()) is True


def test_session_is_valid_false_on_missing_result(mocker):
    mocker.patch.object(jobright, "_request", return_value={})
    assert jobright._session_is_valid(MagicMock()) is False


# ── fetch_recommended_jobs ────────────────────────────────────────────────────

def test_fetch_recommended_jobs_returns_empty_when_credentials_missing(mocker):
    mocker.patch.object(config, "JOBRIGHT_EMAIL", None)
    assert jobright.fetch_recommended_jobs() == []


def test_fetch_recommended_jobs_returns_empty_when_login_fails(mocker):
    mocker.patch.object(jobright, "_login", return_value=False)
    assert jobright.fetch_recommended_jobs() == []


def test_fetch_recommended_jobs_returns_empty_when_session_check_fails(mocker):
    mocker.patch.object(jobright, "_login", return_value=True)
    mocker.patch.object(jobright, "_session_is_valid", return_value=False)
    assert jobright.fetch_recommended_jobs() == []


def test_fetch_recommended_jobs_paginates_until_short_page(mocker):
    mocker.patch.object(config, "JOBRIGHT_PAGE_SIZE", 2)
    mocker.patch.object(config, "JOBRIGHT_MAX_JOBS", 100)
    mocker.patch.object(jobright, "_login", return_value=True)
    mocker.patch.object(jobright, "_session_is_valid", return_value=True)
    full_page = [
        {"jobResult": {"jobTitle": "A", "originalUrl": "u1"}, "companyResult": {"companyName": "C1"}},
        {"jobResult": {"jobTitle": "B", "originalUrl": "u2"}, "companyResult": {"companyName": "C2"}},
    ]
    short_page = [
        {"jobResult": {"jobTitle": "C", "originalUrl": "u3"}, "companyResult": {"companyName": "C3"}},
    ]
    mocker.patch.object(jobright, "_request", side_effect=[
        {"success": True},                              # login
        {"result": {"logined": True}},                  # session check
        {"result": {"jobList": full_page}},              # page 1 (full -- keep paging)
        {"result": {"jobList": short_page}},              # page 2 (short -- stop)
    ])
    jobs = jobright.fetch_recommended_jobs()
    assert [j["title"] for j in jobs] == ["A", "B", "C"]


def test_fetch_recommended_jobs_stops_at_max_jobs_cap(mocker):
    mocker.patch.object(config, "JOBRIGHT_PAGE_SIZE", 2)
    mocker.patch.object(config, "JOBRIGHT_MAX_JOBS", 2)
    mocker.patch.object(jobright, "_login", return_value=True)
    mocker.patch.object(jobright, "_session_is_valid", return_value=True)
    full_page = [
        {"jobResult": {"jobTitle": "A", "originalUrl": "u1"}, "companyResult": {}},
        {"jobResult": {"jobTitle": "B", "originalUrl": "u2"}, "companyResult": {}},
    ]
    request = mocker.patch.object(jobright, "_request", side_effect=[
        {"success": True},
        {"result": {"logined": True}},
        {"result": {"jobList": full_page}},
    ])
    jobs = jobright.fetch_recommended_jobs()
    assert len(jobs) == 2
    assert request.call_count == 3   # login + session check + exactly one page, not a second


def test_fetch_recommended_jobs_never_raises_on_unexpected_error(mocker):
    mocker.patch.object(jobright, "_login", side_effect=RuntimeError("boom"))
    assert jobright.fetch_recommended_jobs() == []


# ── run() ──────────────────────────────────────────────────────────────────────

def test_run_persists_jobs_and_records_run(mocker):
    mocker.patch.object(jobright, "fetch_recommended_jobs", return_value=[
        {"title": "PM", "location": "Remote", "url": "https://x/1", "description": "", "company": "Acme", "source": "jobright"},
    ])
    create = mocker.patch.object(db, "create_job_application", return_value={"id": 1})
    jobright.run()
    assert create.call_count == 1
    _, kwargs = create.call_args
    assert kwargs["company"] == "Acme"
    assert kwargs["role"] == "PM"
    assert kwargs["job_url"] == "https://x/1"
    assert kwargs["source"] == "jobright"
    db.record_run.assert_called_once()
    assert db.record_run.call_args.kwargs["source"] == "jobright"


def test_run_falls_back_to_unknown_company_when_blank(mocker):
    mocker.patch.object(jobright, "fetch_recommended_jobs", return_value=[
        {"title": "PM", "location": "", "url": "https://x/1", "description": "", "company": "", "source": "jobright"},
    ])
    create = mocker.patch.object(db, "create_job_application", return_value={"id": 1})
    jobright.run()
    assert create.call_args.kwargs["company"] == "Unknown"


def test_run_counts_dedup_skip_separately_from_saved(mocker):
    mocker.patch.object(jobright, "fetch_recommended_jobs", return_value=[
        {"title": "PM", "location": "", "url": "https://x/1", "description": "", "company": "Acme", "source": "jobright"},
    ])
    mocker.patch.object(db, "create_job_application", return_value=None)
    jobright.run()
    args, kwargs = db.record_run.call_args
    assert args[0] == "success"
    assert args[1] == 0   # saved
    assert args[2] == 1   # skipped (dedup)
    assert args[3] == 0   # errors


def test_run_isolates_one_postings_persist_failure_from_the_rest(mocker):
    mocker.patch.object(jobright, "fetch_recommended_jobs", return_value=[
        {"title": "First", "location": "", "url": "https://x/1", "description": "", "company": "Acme", "source": "jobright"},
        {"title": "Second", "location": "", "url": "https://x/2", "description": "", "company": "Acme", "source": "jobright"},
    ])
    mocker.patch.object(db, "create_job_application", side_effect=[RuntimeError("boom"), {"id": 2}])
    jobright.run()
    args, kwargs = db.record_run.call_args
    assert args[1] == 1   # saved (Second succeeded)
    assert args[3] == 1   # errors (First's persist failure counted, did not stop Second)


def test_run_with_no_jobs_records_success_and_does_nothing(mocker):
    mocker.patch.object(jobright, "fetch_recommended_jobs", return_value=[])
    create = mocker.patch.object(db, "create_job_application")
    jobright.run()
    create.assert_not_called()
    args, kwargs = db.record_run.call_args
    assert args[0] == "success"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_jobright.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobright'` (file doesn't exist yet).

- [x] **Step 3: Implement**

Create `jobright.py`:

```python
"""
Pulls the user's personalized job recommendations from JobRight.ai's internal
(unofficial) API using a real session-cookie login, and persists them into
job_applications at stage='saved', source='jobright'.

Manual by default (`python3 jobright.py`); also wired into the daily
jobright_pull.yml GitHub Actions workflow per the user's explicit scheduling
override -- see docs/superpowers/specs/2026-08-26-full-fledged-job-platform-
buildout.md ("JobRight puller" and "JobRight scheduling" sections).

Best-effort: a login failure, schema drift, or rate limit returns without
persisting anything and logs a warning -- same posture as ats.py.
JOBRIGHT_EMAIL/JOBRIGHT_PASSWORD are read from os.environ only -- never
written to disk, logged, or passed to db.py.

Usage: python3 jobright.py
"""

import http.cookiejar
import json
import logging
import time
import urllib.error
import urllib.request

import config
import db

log = logging.getLogger(__name__)

_BASE_URL = "https://jobright.ai"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)


# ── HTTP ────────────────────────────────────────────────────────────────────────

def _build_opener():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _request(opener, method, path, body=None):
    url = f"{_BASE_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    last_exc = None
    for attempt in range(config.JOBRIGHT_MAX_ATTEMPTS):
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": _BASE_URL,
            "Referer": f"{_BASE_URL}/",
            "User-Agent": _USER_AGENT,
            "x-client-type": "web",
        })
        try:
            with opener.open(req, timeout=config.JOBRIGHT_TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == config.JOBRIGHT_MAX_ATTEMPTS - 1:
                raise
            last_exc = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == config.JOBRIGHT_MAX_ATTEMPTS - 1:
                raise
            last_exc = exc
        time.sleep(config.JOBRIGHT_RETRY_BACKOFF_SECONDS * (attempt + 1))
    raise last_exc


# ── Auth ────────────────────────────────────────────────────────────────────────

def _login(opener, email, password):
    body = _request(opener, "POST", "/swan/auth/login/pwd", {
        "email": email, "password": password, "from": "homepage",
    })
    return bool(body.get("success"))


def _session_is_valid(opener):
    body = _request(opener, "GET", "/swan/auth/newinfo")
    return bool((body.get("result") or {}).get("logined"))


# ── Posting parsing ───────────────────────────────────────────────────────────

def _job_from_result(job_entry):
    job_result = job_entry.get("jobResult") or {}
    company_result = job_entry.get("companyResult") or {}
    title = job_result.get("jobTitle")
    if not isinstance(title, str) or not title.strip():
        return None
    return {
        "title": title.strip(),
        "location": job_result.get("jobLocation") or "",
        "url": job_result.get("originalUrl") or job_result.get("applyLink") or "",
        "description": job_result.get("jobSummary") or "",
        "company": company_result.get("companyName") or "",
        "source": "jobright",
    }


def _fetch_page(opener, position, count):
    body = _request(
        opener, "GET",
        f"/swan/recommend/list/jobs?refresh=true&sortCondition=0&position={position}&count={count}&syncRerank=false",
    )
    entries = ((body.get("result") or {}).get("jobList")) or []
    jobs = [j for j in (_job_from_result(e) for e in entries) if j]
    return jobs, len(entries)


# ── Public entry point ─────────────────────────────────────────────────────────

def fetch_recommended_jobs():
    """Log in and return up to JOBRIGHT_MAX_JOBS recommended postings. Never raises -- returns [] on any failure."""
    try:
        if not config.JOBRIGHT_EMAIL or not config.JOBRIGHT_PASSWORD:
            log.info("[JOBRIGHT] | JOBRIGHT_EMAIL/JOBRIGHT_PASSWORD not set, skipping")
            return []

        opener = _build_opener()
        if not _login(opener, config.JOBRIGHT_EMAIL, config.JOBRIGHT_PASSWORD):
            log.warning("[JOBRIGHT] | login failed")
            return []
        if not _session_is_valid(opener):
            log.warning("[JOBRIGHT] | session check failed after login")
            return []

        jobs = []
        position = 0
        while len(jobs) < config.JOBRIGHT_MAX_JOBS:
            page_jobs, raw_count = _fetch_page(opener, position, config.JOBRIGHT_PAGE_SIZE)
            jobs.extend(page_jobs)
            if raw_count < config.JOBRIGHT_PAGE_SIZE:
                break
            position += config.JOBRIGHT_PAGE_SIZE
            time.sleep(config.JOBRIGHT_PAGE_DELAY_SECONDS)
        return jobs[:config.JOBRIGHT_MAX_JOBS]
    except Exception as exc:
        log.warning(f"[JOBRIGHT] | unexpected error: {exc}")
        return []


def run():
    """Pull recommended postings from JobRight and persist matches into job_applications."""
    start = time.time()
    saved = 0
    skipped = 0
    errors = 0

    jobs = fetch_recommended_jobs()
    log.info(f"[JOBRIGHT] | START | jobs_fetched={len(jobs)}")

    for job in jobs:
        try:
            result = db.create_job_application(
                company=job["company"] or "Unknown",
                role=job["title"],
                job_url=job.get("url") or None,
                source="jobright",
                posting_snapshot=job,
            )
            if result is None:
                skipped += 1
            else:
                saved += 1
        except Exception as exc:
            log.warning(f"[JOBRIGHT] | {job.get('title')} | persist error: {exc}")
            errors += 1

    log.info(f"[JOBRIGHT] | DONE | saved={saved} | skipped={skipped} | errors={errors}")
    db.record_run("success", saved, skipped, errors, round(time.time() - start), source="jobright")


if __name__ == "__main__":
    logging.basicConfig(
        filename="jobright.log",
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M",
    )
    run()
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_jobright.py -v`
Expected: all PASS.

- [x] **Step 5: Manual smoke test with real credentials (interactive session only, skip in CI)**

With `JOBRIGHT_EMAIL`/`JOBRIGHT_PASSWORD` exported in your own shell (never pasted into chat):

```bash
JOBRIGHT_EMAIL=... JOBRIGHT_PASSWORD=... python3 -c "
import logging; logging.basicConfig(level=logging.INFO)
import jobright
jobs = jobright.fetch_recommended_jobs()
print(f'fetched {len(jobs)} jobs')
print(jobs[0] if jobs else 'no jobs')
"
```

Expected: a real, non-empty job list (or a clean `[]` with a `[JOBRIGHT] | login failed` /
`session check failed` warning if the credentials are wrong) — confirms the real login flow works
end-to-end against production before this gets wired into a scheduled workflow. Do not commit
anything from this step; it's verification only.

- [x] **Step 6: Run the full test suite**

Run: `python3 -m pytest`
Expected: all green.

- [x] **Step 7: Commit**

```bash
git add jobright.py tests/test_jobright.py
git commit -m "feat: add jobright.py to pull JobRight recommendations into job_applications"
```

---

## Task 3: `jobright_pull.yml` — scheduled workflow

**Files:**
- Create: `.github/workflows/jobright_pull.yml`

**Interfaces:** none — GitHub Actions workflow only.

- [x] **Step 1: Write the workflow**

```yaml
name: JobRight Pull

# Manual-by-default JobRight puller (jobright.py), also run on a daily schedule per
# the user's explicit override of the original manual-only rule -- see
# docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md,
# "JobRight scheduling". Daily cadence: postings don't change fast enough to need
# more, and a lower frequency reduces JobRight account-flag risk relative to hourly.

on:
  schedule:
    - cron: '17 11 * * *'   # once daily, off the hour to avoid GHA's top-of-hour pile-up
  workflow_dispatch: {}

jobs:
  pull:
    runs-on: ubuntu-latest
    timeout-minutes: 20

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Pull JobRight recommendations
        run: python jobright.py
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GMAIL_ADDRESS: ${{ secrets.GMAIL_ADDRESS }}
          GMAIL_APP_PASSWORD: ${{ secrets.GMAIL_APP_PASSWORD }}
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_ANON_KEY: ${{ secrets.SUPABASE_ANON_KEY }}
          JOBRIGHT_EMAIL: ${{ secrets.JOBRIGHT_EMAIL }}
          JOBRIGHT_PASSWORD: ${{ secrets.JOBRIGHT_PASSWORD }}

      - name: Upload log artifact
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: jobright-pull-log-${{ github.run_id }}
          path: jobright.log
          retention-days: 30

      - name: Notify on failure
        if: failure()
        run: python notify_failure.py
        env:
          GMAIL_ADDRESS: ${{ secrets.GMAIL_ADDRESS }}
          GMAIL_APP_PASSWORD: ${{ secrets.GMAIL_APP_PASSWORD }}
```

(The four core secrets — `ANTHROPIC_API_KEY`/`GMAIL_ADDRESS`/`GMAIL_APP_PASSWORD`/`SUPABASE_URL`/
`SUPABASE_ANON_KEY` — are required because `config.py` hard-imports them at module load time for
every script, per the root CLAUDE.md's GitHub Actions section, even though `jobright.py` itself
only uses `SUPABASE_URL`/`SUPABASE_ANON_KEY`/`JOBRIGHT_EMAIL`/`JOBRIGHT_PASSWORD`.)

- [x] **Step 2: Commit**

```bash
git add .github/workflows/jobright_pull.yml
git commit -m "feat: add jobright_pull.yml scheduled workflow"
```

(Do not enable/trigger this workflow yet — Task 4 adds the secrets it needs first.)

---

## Task 4: Add `JOBRIGHT_EMAIL`/`JOBRIGHT_PASSWORD` GitHub Actions secrets

**This step is 100% manual, run by the user in their own terminal — never by an agent, and the
values are never pasted into any chat or file.**

- [ ] **Step 1: Set the secrets**

In a terminal you control (not this conversation):

```bash
gh secret set JOBRIGHT_EMAIL --repo kishoretheeraj/cold-emailing-agent
gh secret set JOBRIGHT_PASSWORD --repo kishoretheeraj/cold-emailing-agent
```

Each prompts interactively for the value without echoing it. (If you rotated the password after
the earlier exposure in this session, as recommended, use the new password here.)

- [ ] **Step 2: Verify (name only, never the value)**

```bash
gh secret list --repo kishoretheeraj/cold-emailing-agent | grep JOBRIGHT
```

Expected: both `JOBRIGHT_EMAIL` and `JOBRIGHT_PASSWORD` listed with an "Updated" timestamp.

- [ ] **Step 3: Manually trigger the workflow once to confirm it works end-to-end**

```bash
gh workflow run jobright_pull.yml --repo kishoretheeraj/cold-emailing-agent
```

Then check the run: `gh run list --workflow=jobright_pull.yml --repo kishoretheeraj/cold-emailing-agent --limit 1`
Expected: `success`. If it fails, download the `jobright-pull-log` artifact and check for a
`[JOBRIGHT] | login failed` line before debugging further (most likely cause: JobRight's login
flow changed, or the secrets were set incorrectly).

---

## Task 5: Docs — close out this plan

**Files:**
- Modify: `CLAUDE.md` (root)
- Modify: `docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md`

**Interfaces:** none — documentation only.

- [x] **Step 1: Update the root `CLAUDE.md` module layout list**

Add `jobright.py` to the module-layout code block, right after `job_discovery.py`.

- [x] **Step 2: Add a `jobright.py` section to root `CLAUDE.md`**

Add a new `##` section, right after the "Job discovery" section:

```markdown
## JobRight puller (full-fledged buildout, Phase 2 — JobRight source)

`jobright.py` (manual by default; also scheduled daily via `jobright_pull.yml` per an explicit
user override of the original manual-only rule) logs into JobRight.ai's unofficial internal API
using a real session-cookie login (`POST /swan/auth/login/pwd`, verified via
`GET /swan/auth/newinfo`), paginates `GET /swan/recommend/list/jobs`, and persists matches into
`job_applications` at `stage='saved'`, `source='jobright'` — the same dedup-by-`job_url` path
`job_discovery.py` uses.

`JOBRIGHT_EMAIL`/`JOBRIGHT_PASSWORD` are **soft-optional** in `config.py`
(`os.environ.get`, not `os.environ[...]`) — unlike the five core secrets, they must not become a
hard-required import-time lookup, since every other script must keep working without them set.
`fetch_recommended_jobs()` no-ops (returns `[]`) when they're absent, and never raises past its own
boundary on any other failure (login failure, session check failure, HTTP failure, malformed
response) — same "enrichment must never cost a draft" posture as `ats.py`. Log marker `[JOBRIGHT]`,
own log file (`jobright.log`).

**Never log, print, persist, or commit the credential values, session cookies, or any response
field containing them.** See
`docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md` for the full decision
record, including the live-reconnaissance findings that grounded the real endpoint shapes used
here (not guessed).
```

- [x] **Step 3: Update the spec's Phase 2 plan pointer**

In `docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md`, update the line
pointing at `docs/superpowers/plans/2026-08-27-jobright-puller.md` (currently says "not yet
written") to say "shipped 2026-08-27."

- [x] **Step 4: Add a memory entry**

Write `~/.claude/projects/-Users-kishoretheeraj-Documents-cold-email-agent/memory/project-jobright-puller.md`
following the existing memory file format, summarizing: `jobright.py` shipped (date), the real
endpoint shapes found via live reconnaissance, the Google-OIDC-vs-native-password finding and how
it resolved, the password-exposure incident and that the user was told to rotate it, and the
`jobright_pull.yml` daily schedule. Add one line to `MEMORY.md`'s index.

(Skip this step if running under `build-continue.yml` — the memory path doesn't exist in that
environment.)

- [x] **Step 5: Run the full test suite one last time**

Run: `python3 -m pytest`
Expected: all green.

- [x] **Step 6: Commit**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md
git commit -m "docs: close out JobRight puller plan"
```
