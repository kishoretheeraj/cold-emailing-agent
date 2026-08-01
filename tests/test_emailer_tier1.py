"""Tests for generate_email Tier 1 critic gating and retry behavior."""

import pytest
import emailer


# ── Helpers ────────────────────────────────────────────────────────────────────


def _outreach_contact(**overrides):
    base = {
        "name": "Dana",
        "email": "dana@example.com",
        "company": "Clearbond",
        "role": "CEO",
        "detail": "customs bond SaaS",
        "tier": 1,
        "mode": "outreach",
    }
    base.update(overrides)
    return base


def _applied_contact(**overrides):
    base = {
        "name": "Sarah",
        "email": "sarah@stripe.com",
        "company": "Stripe",
        "role": "Group PM",
        "mode": "applied",
        "job_title": "Senior PM, Financial Infrastructure",
        "job_description": "Lead a small team building...",
        "applied_date": "2026-04-21",
        "tier": 1,
    }
    base.update(overrides)
    return base


# ── Part A: Critic gating ──────────────────────────────────────────────────────


@pytest.mark.parametrize("tier,action,expects_critic", [
    (1,    "send_first_touch",      True),
    (1,    "send_applied_intro",    True),
    (1,    "send_followup1",        False),
    (1,    "send_followup2",        False),
    (1,    "send_breakup",          False),
    (1,    "send_applied_followup", False),
    (2,    "send_first_touch",      False),
    (3,    "send_first_touch",      False),
    (None, "send_first_touch",      False),
])
def test_critic_gating(tier, action, expects_critic, mocker):
    # Build contact appropriate for the action's mode.
    # For tier=None we omit the key entirely so _generate_outreach falls back
    # to its default of 2 via contact.get("tier", 2) rather than crashing on
    # int("None").
    if action in ("send_applied_intro", "send_applied_followup"):
        contact = _applied_contact()
        if tier is None:
            contact.pop("tier", None)
        else:
            contact["tier"] = tier
    else:
        contact = _outreach_contact()
        if tier is None:
            contact.pop("tier", None)
        else:
            contact["tier"] = tier

    mocker.patch("preflight.check", return_value=[])
    mocker.patch("db.log_agent_event")
    mocker.patch("emailer.time.sleep")
    mocker.patch.object(
        emailer, "_call_claude",
        side_effect=["Email body text.", "quick subject"],
    )
    mock_critic = mocker.patch.object(
        emailer, "_run_critic",
        return_value={"verdict": "PASS", "score": 16, "rewrite_required": False,
                      "killed_by": [], "failed_soft_criteria": [], "feedback": ""},
    )

    # Follow-up actions need original_subject to avoid "Re: None" issues.
    followup_actions = {
        "send_followup1", "send_followup2", "send_breakup", "send_applied_followup"
    }
    kwargs = {}
    if action in followup_actions:
        kwargs["original_subject"] = "some subject"

    emailer.generate_email(contact, action, **kwargs)

    if expects_critic:
        assert mock_critic.called, (
            f"Expected _run_critic to be called for tier={tier}, action={action}"
        )
    else:
        assert not mock_critic.called, (
            f"Expected _run_critic NOT to be called for tier={tier}, action={action}"
        )


# ── Part B: Critic passes (score=7, no retry) ─────────────────────────────────


def test_generate_email_tier1_critic_passes_no_retry(mocker):
    contact = _outreach_contact(tier=1)

    mocker.patch("preflight.check", return_value=[])
    mocker.patch("db.log_agent_event")
    mocker.patch("emailer.time.sleep")
    mock_claude = mocker.patch.object(
        emailer, "_call_claude",
        side_effect=["First body.", "great subject"],
    )
    mocker.patch.object(
        emailer, "_run_critic",
        return_value={"verdict": "PASS", "score": 16, "rewrite_required": False,
                      "killed_by": [], "failed_soft_criteria": [], "feedback": ""},
    )

    subject, body = emailer.generate_email(contact, "send_first_touch")

    assert body == "First body."
    assert subject == "great subject"
    # Body call + subject call only — no regeneration.
    assert mock_claude.call_count == 2


# ── Part C: Critic fails, triggers retry (score=4) ────────────────────────────


def test_generate_email_tier1_critic_retries_on_low_score(mocker):
    contact = _outreach_contact(tier=1)

    mocker.patch("preflight.check", return_value=[])
    mocker.patch("db.log_agent_event")
    mocker.patch("emailer.time.sleep")
    mock_claude = mocker.patch.object(
        emailer, "_call_claude",
        side_effect=[
            "Original body.",    # call 1: body generation
            "original subject",  # call 2: subject generation
            "Revised body.",     # call 3: regenerated body (via extra_instruction)
            "revised subject",   # call 4: new subject for revised body
        ],
    )
    mocker.patch.object(
        emailer, "_run_critic",
        return_value={"verdict": "FAIL", "score": 12, "rewrite_required": True,
                      "killed_by": ["K3"], "failed_soft_criteria": [],
                      "feedback": "Make it more specific."},
    )

    subject, body = emailer.generate_email(contact, "send_first_touch")

    assert body == "Revised body."
    assert subject == "revised subject"
    assert mock_claude.call_count == 4


# ── Part D: Preflight retry raises (e.g. rate-limit) — fall through ───────────


def _networking_contact(**overrides):
    base = {
        "name": "Priya",
        "email": "priya@example.com",
        "company": "Northwind",
        "mode": "networking",
        "connection_context": "Fellow Tuck MEM",
        "tier": 2,
    }
    base.update(overrides)
    return base


def test_networking_preflight_retry_uses_networking_generator_not_applied_followup(mocker):
    """Regression: a networking first-touch that fails preflight must regenerate
    via _generate_networking, not silently fall through to _generate_applied_followup.
    (finalize_email's bare-else preflight-retry branch previously assumed
    'not outreach, not applied-intro' meant 'applied-followup'.)"""
    contact = _networking_contact()

    mocker.patch("db.log_agent_event")
    mocker.patch("emailer.time.sleep")
    mocker.patch("preflight.check", side_effect=[
        ["first_name_missing: 'Priya'"],
        [],
    ])
    mocker.patch.object(emailer, "_generate_subject", return_value="a subject")
    mock_networking = mocker.patch.object(
        emailer, "_generate_networking", return_value="Correct networking body."
    )
    mock_applied_followup = mocker.patch.object(
        emailer, "_generate_applied_followup", return_value="WRONG applied-followup body."
    )

    subject, body = emailer.finalize_email(
        contact, "send_networking_first_touch", "Bad original body.",
        original_subject=None, prompts={}, dart_instr="", research_block="",
    )

    assert body == "Correct networking body."
    mock_networking.assert_called_once()
    mock_applied_followup.assert_not_called()


def test_networking_critic_retry_uses_networking_generator_not_applied_intro(mocker):
    """Regression: a tier-1 networking first-touch that fails the critic must
    regenerate via _generate_networking, not silently fall through to
    _generate_applied_intro. (finalize_email's critic `regenerate` closure
    previously assumed 'not send_first_touch' meant 'send_applied_intro'.)"""
    contact = _networking_contact(tier=1)

    mocker.patch("preflight.check", return_value=[])
    mocker.patch("db.log_agent_event")
    mocker.patch("emailer.time.sleep")
    mocker.patch.object(emailer, "_generate_subject", return_value="a subject")
    mock_networking = mocker.patch.object(
        emailer, "_generate_networking", return_value="Revised networking body."
    )
    mock_applied_intro = mocker.patch.object(
        emailer, "_generate_applied_intro", return_value="WRONG applied-intro body."
    )
    mocker.patch.object(
        emailer, "_run_critic",
        return_value={"verdict": "FAIL", "score": 10, "rewrite_required": True,
                      "killed_by": [], "failed_soft_criteria": [], "feedback": "fix it"},
    )

    subject, body = emailer.finalize_email(
        contact, "send_networking_first_touch", "Original body.",
        original_subject=None, prompts={}, dart_instr="", research_block="",
    )

    assert body == "Revised networking body."
    mock_networking.assert_called_once()
    mock_applied_intro.assert_not_called()


def test_preflight_retry_exception_falls_through(mocker):
    """If the preflight retry call raises (e.g. rate-limit 429), clear failures
    and allow the draft rather than falsely blocking the contact."""
    contact = _outreach_contact(tier=1)

    mocker.patch("db.log_agent_event")
    mocker.patch("emailer.time.sleep")
    mock_claude = mocker.patch.object(
        emailer, "_call_claude",
        side_effect=[
            "Original body.",               # call 1: body generation
            Exception("429 too many requests"),  # call 2: retry body → raises
            "A subject",                    # call 3: subject generation
        ],
    )
    mocker.patch("preflight.check", side_effect=[
        ["first_name_missing: 'Dana'"],  # original body fails preflight
        # no second call — exception clears failures before the second check
    ])
    mocker.patch.object(
        emailer, "_run_critic",
        return_value={"verdict": "PASS", "score": 16, "rewrite_required": False,
                      "killed_by": [], "failed_soft_criteria": [], "feedback": ""},
    )

    subject, body = emailer.generate_email(contact, "send_first_touch")

    # Should not raise — fall through with the unrevised body
    assert body == "Original body."
    assert subject == "A subject"
    assert mock_claude.call_count == 3
