"""
Fills real job application forms unattended, stops before Submit. Routes by
platform: Greenhouse/Ashby/Lever get the hand-mapped ats_fillers.py, anything
else with a real application page gets browser-use, Workday and aggregator
links are never attempted. Best-effort per row in the preview pass -- one
job's failure never blocks the batch. See
docs/superpowers/specs/2026-08-30-phase2.5-auto-apply-design.md.
"""

import json
import logging

import ats_fillers
import ats_platform
import config
import db
from emailer import _call_claude

log = logging.getLogger(__name__)

_MODE_TAGS = {"preview": "[APPLY-PREVIEW]", "submit": "[APPLY-SUBMIT]"}


# ── Field-value sources ────────────────────────────────────────────────────────

def _standard_field_values(job):
    return {
        "name": "Kishore Theeraj Vasudevan Jaya",
        "email": "kishoretheeraj@gmail.com",
        "phone": "+1 603-322-0535",
        "location": "Hanover, NH",
        "linkedin": "linkedin.com/in/kishoretheeraj",
    }


def _eligibility_answers():
    """Sensitive fixed-answer fields (work auth, sponsorship, EEO) -- never LLM-generated per
    application. Missing key degrades to an empty dict, never a guessed answer."""
    raw = db.load_prompts().get("applicant_eligibility")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception as exc:
        log.warning(f"[APPLY-AGENT] | applicant_eligibility failed to parse: {exc}")
        return {}


def _answer_screening_questions(page, job):
    """Reads free-text screening questions off the page and answers them via Claude, grounded
    only in the real profile. Stub for this task -- Task 10 fills in the real implementation
    (question-detection + generation) once the browser-use integration exists to compare against."""
    return {}


def _attach_resume_and_cover_letter(page, job):
    """Downloads job['resume_file_ref']/['cover_letter_file_ref'] from Storage and attaches them
    via Playwright's headless setInputFiles-equivalent. Stub for this task -- filled in by Task 10
    alongside the browser-use integration."""


# ── Browser lifecycle (real Playwright launch -- mocked in every test) ───────────

def _launch_page(job_url):
    from playwright.sync_api import sync_playwright
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(job_url)
    return page


def _fill_generic_via_browser_use(page, job, field_values):
    """Generic-page filler for anything not Greenhouse/Ashby/Lever/Workday/aggregator. Stub for
    this task -- Task 10 fills in the real browser-use integration."""


# ── Preview pass ───────────────────────────────────────────────────────────────

def _process_one_preview(job):
    job_id = job["id"]
    platform = ats_platform.classify(job.get("job_url"))

    if platform == "workday":
        db.set_apply_blocked(job_id, "workday -- permanently excluded, see spec's Rejected section")
        return
    if platform == "aggregator":
        db.set_apply_blocked(job_id, "aggregator/listing link, not a real application page")
        return

    page = _launch_page(job.get("job_url"))
    field_values = _standard_field_values(job)

    if platform in config.APPLY_AGENT_HAND_MAPPED_PLATFORMS:
        {"greenhouse": ats_fillers.fill_greenhouse,
         "ashby": ats_fillers.fill_ashby,
         "lever": ats_fillers.fill_lever}[platform](page, field_values)
    else:
        _fill_generic_via_browser_use(page, job, field_values)

    _attach_resume_and_cover_letter(page, job)
    screening_answers = _answer_screening_questions(page, job)

    preview = {
        "platform": platform,
        "field_values": field_values,
        "eligibility_answers": _eligibility_answers(),
        "screening_answers": screening_answers,
    }
    db.set_apply_preview(job_id, preview)


def run_preview():
    jobs = [j for j in db.get_job_applications(stage="saved")
            if j.get("resume_file_ref") and j.get("cover_letter_file_ref")]
    log.info(f"[APPLY-PREVIEW] | START | eligible_jobs={len(jobs)}")
    filled = 0
    blocked = 0
    errors = 0

    for job in jobs:
        try:
            before_blocked = job.get("apply_blocked_reason")
            _process_one_preview(job)
            filled += 1
        except Exception as exc:
            log.warning(f"[APPLY-PREVIEW] | {job.get('company')} | error: {exc}")
            try:
                db.set_apply_blocked(job["id"], f"preview pass error: {exc}")
            except Exception:
                pass
            errors += 1

    log.info(f"[APPLY-PREVIEW] | DONE | filled={filled} | errors={errors}")
