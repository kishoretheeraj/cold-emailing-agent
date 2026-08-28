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
