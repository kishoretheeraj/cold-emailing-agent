"""Tests for research injection gating and behavior in emailer.generate_email."""

import pytest

import config
import emailer
import research


@pytest.fixture(autouse=True)
def _patch_preflight_and_db(mocker):
    mocker.patch("preflight.check", return_value=[])
    mocker.patch("db.log_agent_event")
    mocker.patch("emailer.time.sleep")


def _outreach_contact(**overrides):
    base = {
        "name": "Dana",
        "email": "dana@example.com",
        "company": "Clearbond",
        "role": "CEO",
        "detail": "customs bond SaaS",
        "notes": "",
        "tier": 1,
        "mode": "outreach",
        "dartmouth": False,
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
        "job_title": "Senior PM",
        "job_description": "Lead a team...",
        "applied_date": "2026-04-21",
        "tier": 1,
        "notes": "",
        "dartmouth": False,
    }
    base.update(overrides)
    return base


def _networking_contact(**overrides):
    base = {
        "name": "Priya",
        "email": "priya@example.com",
        "company": "Northwind",
        "mode": "networking",
        "connection_context": "Fellow Tuck MEM",
        "tier": 1,
        "notes": "",
        "dartmouth": False,
    }
    base.update(overrides)
    return base


# ── Research gating ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tier,action,expects_research", [
    (1,    "send_first_touch",      True),
    (1,    "send_applied_intro",    True),
    (1,    "send_followup1",        False),
    (1,    "send_followup2",        False),
    (1,    "send_breakup",          False),
    (1,    "send_applied_followup", False),
    (2,    "send_first_touch",      True),
    (3,    "send_first_touch",      False),
    (None, "send_first_touch",      False),
    (1,    "send_networking_first_touch", True),
    (2,    "send_networking_first_touch", True),
    (3,    "send_networking_first_touch", False),
    (1,    "send_networking_followup",    False),
])
def test_research_gating(tier, action, expects_research, mocker):
    if action in ("send_applied_intro", "send_applied_followup"):
        contact = _applied_contact()
    elif action in ("send_networking_first_touch", "send_networking_followup"):
        contact = _networking_contact()
    else:
        contact = _outreach_contact()

    if tier is None:
        contact.pop("tier", None)
    else:
        contact["tier"] = tier

    mocker.patch.object(
        emailer, "_call_claude",
        side_effect=["Email body text.", "quick subject"],
    )
    mocker.patch.object(
        emailer, "_run_critic",
        return_value={"score": 7, "failed_criteria": [], "feedback": ""},
    )
    mock_research = mocker.patch.object(
        research, "get_research_brief",
        return_value="",
    )

    followup_actions = {
        "send_followup1", "send_followup2", "send_breakup",
        "send_applied_followup", "send_networking_followup",
    }
    kwargs = {}
    if action in followup_actions:
        kwargs["original_subject"] = "some subject"

    emailer.generate_email(contact, action, **kwargs)

    if expects_research:
        assert mock_research.called, (
            f"Expected get_research_brief called for tier={tier}, action={action}"
        )
    else:
        assert not mock_research.called, (
            f"Expected get_research_brief NOT called for tier={tier}, action={action}"
        )


# ── Brief injection behavior ───────────────────────────────────────────────────


def test_tier1_first_touch_with_brief_injects_brief_into_prompt(mocker):
    contact = _outreach_contact(tier=1)
    mocker.patch.object(research, "get_research_brief", return_value="Person:\n- Spoke at SaaStr 2026")
    captured = []

    def capture_call(prompt, **kwargs):
        captured.append(prompt)
        return "email body text here"

    mocker.patch.object(emailer, "_call_claude", side_effect=capture_call)
    mocker.patch.object(
        emailer, "_run_critic",
        return_value={"score": 7, "failed_criteria": [], "feedback": ""},
    )

    emailer.generate_email(contact, "send_first_touch")

    first_prompt = captured[0]
    assert "SaaStr 2026" in first_prompt


def test_tier1_first_touch_with_empty_brief_has_no_injection_block(mocker):
    contact = _outreach_contact(tier=1)
    mocker.patch.object(research, "get_research_brief", return_value="")
    captured = []

    def capture_call(prompt, **kwargs):
        captured.append(prompt)
        return "email body text here"

    mocker.patch.object(emailer, "_call_claude", side_effect=capture_call)
    mocker.patch.object(
        emailer, "_run_critic",
        return_value={"score": 7, "failed_criteria": [], "feedback": ""},
    )

    emailer.generate_email(contact, "send_first_touch")

    first_prompt = captured[0]
    assert "RECENT WEB CONTEXT" not in first_prompt


def test_tier1_first_touch_where_research_raises_continues_without_brief(mocker):
    contact = _outreach_contact(tier=1)
    mocker.patch.object(research, "get_research_brief", side_effect=Exception("pipeline exploded"))
    mocker.patch.object(
        emailer, "_call_claude",
        side_effect=["Email body.", "subject line"],
    )
    mocker.patch.object(
        emailer, "_run_critic",
        return_value={"score": 7, "failed_criteria": [], "feedback": ""},
    )
    mock_log = mocker.patch.object(emailer, "log")

    subject, body = emailer.generate_email(contact, "send_first_touch")

    assert body == "Email body."
    mock_log.warning.assert_called()


def test_injection_template_missing_placeholder_falls_back_to_base_message(mocker):
    contact = _outreach_contact(tier=1)
    mocker.patch.object(research, "get_research_brief", return_value="some brief text")
    captured = []

    def capture_call(prompt, **kwargs):
        captured.append(prompt)
        return "email body text"

    mocker.patch.object(emailer, "_call_claude", side_effect=capture_call)
    mocker.patch.object(
        emailer, "_run_critic",
        return_value={"score": 7, "failed_criteria": [], "feedback": ""},
    )
    mock_log = mocker.patch.object(emailer, "log")

    prompts = {"research_injection": "BROKEN TEMPLATE {nonexistent_key}"}
    emailer.generate_email(contact, "send_first_touch", prompts=prompts)

    warning_messages = [str(c) for c in mock_log.warning.call_args_list]
    assert any("injection template format failed" in m for m in warning_messages)
