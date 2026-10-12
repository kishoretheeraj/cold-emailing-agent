"""Regression tests for the pipeline state-machine findings (plan item 0):

1. "magic_link_open" must be a valid phase (mark() calls it; validate_state rejects
   unknown phases, which used to halt the pipeline with StateCorrupt).
2. "submitted_unconfirmed", "needs_review" and "skipped" must be valid state statuses
   (finish_job writes them via STATE_FOR_STATUS).
3. Live mode must apply the same dry_run_missing bar before submit: gaps end the job
   as needs_review without a click.
4. Work-auth sponsorship questions are auto-answered truthfully (user-approved):
   authorized in the US via OPT -> Yes; requires sponsorship now/future (H-1B after
   OPT) -> Yes; can work without sponsorship -> No. Ambiguous/compound phrasing
   stays review-only, as do current-scoped "without sponsorship" asks (the operator
   is eligible to work on OPT right now).

Pure tests: no network, no browser. playwright is stubbed because it is not
installed in the dev environment and apply_pipeline imports it at module level.
"""

import sys
from unittest.mock import MagicMock

_pw = MagicMock()
_pw_sync = MagicMock()
_pw_sync.Error = Exception
_pw_sync.TimeoutError = Exception
sys.modules.setdefault("playwright", _pw)
sys.modules.setdefault("playwright.sync_api", _pw_sync)

import apply_pipeline as ap  # noqa: E402


# ---------------------------------------------------------------- helpers

def _rec(status="in_progress", phases=None):
    rec = {"status": status, "attempts": 0}
    if phases is not None:
        rec["phases"] = phases
    return {"j1": rec}


def _facts(**over):
    facts = {"fields_filled": 3, "required_unfilled": 0, "required_total": 5, "resume_uploaded": 1,
             "unanswered_questions": 0, "for_review": 0, "verify_required": 0, "submit_candidates": 1}
    facts.update(over)
    return facts


# ---------------------------------------------------------------- phases

def test_phases_contains_magic_link_open():
    assert "magic_link_open" in ap.PHASES
    assert (ap.PHASES.index("otp_requested") < ap.PHASES.index("magic_link_open")
            < ap.PHASES.index("otp_entered"))


def test_validate_state_accepts_magic_link_phase():
    phases = ["started", "navigating", "otp_requested", "magic_link_open"]
    assert ap.validate_state(_rec(phases=phases)) is None


def test_validate_state_rejects_unknown_phase():
    phases = ["started", "navigating", "otp_requested", "bogus_phase"]
    assert ap.validate_state(_rec(phases=phases)) == "bad_phases"


# ---------------------------------------------------------------- statuses

def test_state_statuses_include_new():
    for status in ("submitted_unconfirmed", "needs_review", "skipped"):
        assert status in ap.STATE_STATUSES
        assert ap.validate_state(_rec(status=status)) is None


def test_state_for_status_mapping():
    assert ap.STATE_FOR_STATUS.get("submitted_unconfirmed", "submitted_unconfirmed") == \
        "submitted_unconfirmed"  # maps to itself
    assert ap.STATE_FOR_STATUS.get("dry_run_ok", "dry_run_ok") == "done"


# ---------------------------------------------------------------- dry_run_missing / live_preconditions

def test_dry_run_missing_flags_gaps():
    assert ap.dry_run_missing(_facts(required_unfilled=1)) == ["required_unfilled"]
    assert "for_review" in ap.dry_run_missing(_facts(for_review=1))
    assert "unanswered_questions" in ap.dry_run_missing(_facts(unanswered_questions=2))
    assert ap.dry_run_missing(_facts()) == []


def test_live_preconditions_same_bar_as_dry_run():
    for kw in ({}, {"required_unfilled": 1}, {"for_review": 1}, {"unanswered_questions": 2},
               {"resume_uploaded": 0}, {"submit_candidates": 2}):
        assert ap.live_preconditions(_facts(**kw)) == ap.dry_run_missing(_facts(**kw))


def test_live_submit_decision_logic():
    # The guard lives inside _run_job (needs a browser), so pin its decision rule here.
    missing = ap.live_preconditions(_facts(required_unfilled=1, for_review=1))
    assert missing and "required_unfilled" in missing and "for_review" in missing
    status = "needs_review" if missing else "submitted"
    assert status == "needs_review"
    assert ap.live_preconditions(_facts()) == []  # complete facts -> submit path


# ---------------------------------------------------------------- classify_question (sponsorship auto-answer)

def test_classify_work_authorization_yes():
    assert ap.classify_question("Are you legally authorized to work in the United States?") == (
        "answer", "work_auth", "Yes", "")


def test_classify_sponsorship_required_yes():
    assert ap.classify_question("Will you now or in the future require sponsorship?") == (
        "answer", "sponsorship", "Yes", "")


def test_classify_without_sponsorship_no():
    # User-approved truthful answer: needs H-1B after OPT, so cannot work without sponsorship.
    assert ap.classify_question("Can you work without sponsorship?") == (
        "answer", "sponsorship", "No", "")


def test_classify_without_sponsorship_current_stays_review():
    # Operator is eligible to work on OPT right now; only the future H-1B need differs.
    assert ap.classify_question("Can you currently work without sponsorship?")[0] == "review"
    assert ap.classify_question("Can you work in the US without sponsorship now?")[0] == "review"


def test_classify_compound_stays_review():
    act = ap.classify_question("Are you authorized to work in the US without sponsorship?")[0]
    assert act == "review"


def test_classify_extra_condition_stays_review():
    act = ap.classify_question("Do you require sponsorship, and are you willing to relocate?")[0]
    assert act == "review"
