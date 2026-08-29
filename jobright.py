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
        "responsibilities": job_result.get("coreResponsibilities") or [],
        "qualifications": job_result.get("skillSummaries") or [],
        "benefits": job_result.get("benefitsSummaries") or [],
        "company": company_result.get("companyName") or "",
        "source": "jobright",
    }


def _fetch_page(opener, position, count):
    body = _request(
        opener, "GET",
        f"/swan/recommend/list/jobs?refresh=true&sortCondition={config.JOBRIGHT_SORT_CONDITION}"
        f"&position={position}&count={count}&syncRerank=false",
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
