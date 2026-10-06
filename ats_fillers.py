"""
Hand-mapped Playwright fillers for Greenhouse, Ashby, and Lever application forms.
Fields are located by id/name selector first, with a label regex as the fallback, and each
filler returns {field_key: True|False} for every field it was given a value for, so
apply_agent.py can refuse a preview/submit whose required fields were never filled. A per-field
timeout (config.APPLY_AGENT_FIELD_TIMEOUT_MS) keeps a missing field from stalling a job for
Playwright's 30s default. Selectors are not yet live-verified against real forms. See
docs/superpowers/specs/2026-08-30-phase2.5-auto-apply-design.md.
"""

import logging
import re

import config

log = logging.getLogger(__name__)


def _fill_first(page, candidates, value):
    if not value:
        return None
    for candidate in candidates:
        try:
            if isinstance(candidate, str):
                locator = page.locator(candidate).first
            else:
                locator = page.get_by_label(candidate).first
            if locator.count() == 0:
                continue
            locator.fill(value, timeout=config.APPLY_AGENT_FIELD_TIMEOUT_MS)
            return True
        except Exception as exc:
            log.info(f"[ATS-FILL] | candidate={candidate} | not fillable: {exc}")
    return False


def _fill_all(page, field_values, candidate_map):
    report = {}
    for key, candidates in candidate_map.items():
        result = _fill_first(page, candidates, field_values.get(key))
        if result is not None:
            report[key] = result
    return report


_LINKEDIN = [re.compile(r"linkedin", re.I)]

_GREENHOUSE = {
    "first_name": ["#first_name", "input[name='first_name']", re.compile(r"^first name", re.I)],
    "last_name": ["#last_name", "input[name='last_name']", re.compile(r"^last name", re.I)],
    "email": ["#email", "input[name='email']", "input[type='email']"],
    "phone": ["#phone", "input[name='phone']", "input[type='tel']"],
    "linkedin": _LINKEDIN,
}

_LEVER = {
    "name": ["input[name='name']", re.compile(r"^full name", re.I)],
    "email": ["input[name='email']", "input[type='email']"],
    "phone": ["input[name='phone']", "input[type='tel']"],
    "location": ["input[name='location']", re.compile(r"current location", re.I)],
    "linkedin": ["input[name='urls[LinkedIn]']", re.compile(r"linkedin", re.I)],
}

_ASHBY = {
    "name": ["#_systemfield_name", "input[name='_systemfield_name']", re.compile(r"^name", re.I)],
    "email": ["#_systemfield_email", "input[name='_systemfield_email']", "input[type='email']"],
    "phone": ["#_systemfield_phone", "input[name='_systemfield_phone']", "input[type='tel']"],
    "location": ["#_systemfield_location", re.compile(r"location", re.I)],
    "linkedin": _LINKEDIN,
}


def fill_ashby(page, field_values):
    return _fill_all(page, field_values, _ASHBY)


def fill_greenhouse(page, field_values):
    return _fill_all(page, field_values, _GREENHOUSE)


def fill_lever(page, field_values):
    return _fill_all(page, field_values, _LEVER)
