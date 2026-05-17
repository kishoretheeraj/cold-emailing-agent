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
    mocker.patch.object(
        emailer, "_call_claude",
        side_effect=["Email body text.", "quick subject"],
    )
    mock_critic = mocker.patch.object(
        emailer, "_run_critic",
        return_value={"score": 7, "failed_criteria": [], "feedback": ""},
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
    mock_claude = mocker.patch.object(
        emailer, "_call_claude",
        side_effect=["First body.", "great subject"],
    )
    mocker.patch.object(
        emailer, "_run_critic",
        return_value={"score": 7, "failed_criteria": [], "feedback": ""},
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
        return_value={
            "score": 4,
            "failed_criteria": [1, 3],
            "feedback": "Make it more specific.",
        },
    )

    subject, body = emailer.generate_email(contact, "send_first_touch")

    assert body == "Revised body."
    assert subject == "revised subject"
    assert mock_claude.call_count == 4
