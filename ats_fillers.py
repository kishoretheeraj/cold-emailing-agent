"""
Hand-mapped Playwright fillers for Greenhouse, Ashby, and Lever application
forms -- label-based locators, deterministic. Each function fills whatever
standard fields it finds and silently skips anything it can't locate; an
unrecognized/missing field is apply_agent.py's concern at the orchestration
level, not this module's. See
docs/superpowers/specs/2026-08-30-phase2.5-auto-apply-design.md.
"""

import logging

log = logging.getLogger(__name__)


def _try_fill(page, label, value):
    if not value:
        return
    try:
        page.get_by_label(label).fill(value)
    except Exception as exc:
        log.info(f"[ATS-FILL] | label={label} | not found or not fillable: {exc}")


def fill_ashby(page, field_values):
    _try_fill(page, "Name", field_values.get("name"))
    _try_fill(page, "Email", field_values.get("email"))
    _try_fill(page, "Phone", field_values.get("phone"))
    _try_fill(page, "Location", field_values.get("location"))
    _try_fill(page, "LinkedIn URL", field_values.get("linkedin"))


def fill_greenhouse(page, field_values):
    _try_fill(page, "Full Name", field_values.get("name"))
    _try_fill(page, "Email", field_values.get("email"))
    _try_fill(page, "Phone", field_values.get("phone"))
    _try_fill(page, "LinkedIn Profile", field_values.get("linkedin"))


def fill_lever(page, field_values):
    _try_fill(page, "Full name", field_values.get("name"))
    _try_fill(page, "Email", field_values.get("email"))
    _try_fill(page, "Phone", field_values.get("phone"))
    _try_fill(page, "Current location", field_values.get("location"))
