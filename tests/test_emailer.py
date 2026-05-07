"""Tests for emailer.py — Dartmouth detection, action mapping, prompt generation."""

import pytest

import emailer


# ── _is_dartmouth ────────────────────────────────────────────────────────────


def test_is_dartmouth_explicit_flag():
    assert emailer._is_dartmouth({"dartmouth": True}) is True


def test_is_dartmouth_explicit_false_with_keyword_in_detail():
    # The explicit flag falls through; detail keyword still matches.
    assert (
        emailer._is_dartmouth({"dartmouth": False, "detail": "Tuck MBA 2018"}) is True
    )


@pytest.mark.parametrize(
    "detail",
    [
        "Dartmouth alum",
        "graduated from TUCK",
        "Thayer School engineer",
        "Irving Institute fellow",
        "former Big Green athlete",
    ],
)
def test_is_dartmouth_keywords_in_detail(detail):
    assert emailer._is_dartmouth({"detail": detail}) is True


def test_is_dartmouth_no_match():
    assert emailer._is_dartmouth({"detail": "Stanford GSB MBA"}) is False


def test_is_dartmouth_missing_detail():
    assert emailer._is_dartmouth({}) is False


def test_is_dartmouth_none_detail():
    assert emailer._is_dartmouth({"detail": None}) is False


# ── ACTION_TO_TEMPLATE invariants ────────────────────────────────────────────


def test_action_to_template_covers_all_agent_actions():
    """Every action the agent dispatches must have a template mapping."""
    import agent

    assert set(emailer.ACTION_TO_TEMPLATE) == set(agent.NEXT_STAGE)


# ── generate_email ───────────────────────────────────────────────────────────


def _outreach_contact(**overrides):
    base = {
        "name": "Dana",
        "email": "dana@example.com",
        "company": "Clearbond",
        "role": "CEO",
        "detail": "customs bond SaaS",
        "tier": 2,
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
    }
    base.update(overrides)
    return base


def test_generate_email_outreach_returns_subject_and_body(mocker):
    # _call_claude is called twice: once for body, once for subject.
    mocker.patch.object(
        emailer,
        "_call_claude",
        side_effect=["Body text here.", "quick intro"],
    )

    subject, body = emailer.generate_email(_outreach_contact(), "send_first_touch")

    assert body == "Body text here."
    assert subject == "quick intro"


def test_generate_email_applied_intro(mocker):
    mocker.patch.object(
        emailer, "_call_claude", side_effect=["Email body.", "re: senior pm"]
    )
    subject, body = emailer.generate_email(_applied_contact(), "send_applied_intro")
    assert subject == "re: senior pm"
    assert body == "Email body."


def test_generate_email_applied_followup(mocker):
    mock_claude = mocker.patch.object(emailer, "_call_claude", return_value="Quick follow-up.")
    subject, body = emailer.generate_email(
        _applied_contact(), "send_applied_followup", original_subject="Senior PM Role"
    )
    assert subject == "Re: Senior PM Role"
    assert body == "Quick follow-up."
    # Only one Claude call (body) — subject is derived, not generated
    assert mock_claude.call_count == 1


def test_generate_email_unknown_action_raises():
    with pytest.raises(ValueError, match="Unknown action"):
        emailer.generate_email(_outreach_contact(), "send_unicorn_email")


def test_generate_email_strips_quotes_from_subject(mocker):
    mocker.patch.object(
        emailer, "_call_claude", side_effect=["body", '"quoted subject"']
    )
    subject, _ = emailer.generate_email(_outreach_contact(), "send_first_touch")
    assert subject == "quoted subject"


def test_generate_email_followup_uses_re_prefix_not_claude(mocker):
    mock_claude = mocker.patch.object(emailer, "_call_claude", return_value="follow-up body")
    subject, _ = emailer.generate_email(
        _outreach_contact(), "send_followup1", original_subject="quick intro"
    )
    assert subject == "Re: quick intro"
    assert mock_claude.call_count == 1  # body only, no subject call


def test_generate_email_breakup_also_uses_re_prefix(mocker):
    mocker.patch.object(emailer, "_call_claude", return_value="breakup body")
    subject, _ = emailer.generate_email(
        _outreach_contact(), "send_breakup", original_subject="quick intro"
    )
    assert subject == "Re: quick intro"


def test_generate_email_followup_empty_original_subject(mocker):
    mocker.patch.object(emailer, "_call_claude", return_value="body")
    subject, _ = emailer.generate_email(_outreach_contact(), "send_followup1")
    assert subject == "Re: "


def test_outreach_prompt_includes_dartmouth_when_alumni(mocker):
    captured_prompts = []

    def fake_claude(prompt):
        captured_prompts.append(prompt)
        return "fake response"

    mocker.patch.object(emailer, "_call_claude", side_effect=fake_claude)

    contact = _outreach_contact(detail="Dartmouth Tuck '20")
    emailer.generate_email(contact, "send_first_touch")

    # Body prompt is the first call
    body_prompt = captured_prompts[0]
    assert "ALUMNI CONNECTION DETECTED" in body_prompt


def test_outreach_prompt_omits_dartmouth_when_not_alumni(mocker):
    captured_prompts = []
    mocker.patch.object(
        emailer,
        "_call_claude",
        side_effect=lambda p: (captured_prompts.append(p), "x")[1],
    )

    contact = _outreach_contact(detail="Stanford GSB")
    emailer.generate_email(contact, "send_first_touch")

    assert "ALUMNI CONNECTION DETECTED" not in captured_prompts[0]


# ── Custom prompts override ───────────────────────────────────────────────────


def test_generate_email_uses_custom_outreach_prompt(mocker):
    captured = []
    mocker.patch.object(
        emailer, "_call_claude",
        side_effect=lambda p: (captured.append(p), "body")[1],
    )
    custom_tpl = (
        "CUSTOM {profile} {name} {company} {role} {detail} "
        "{tier} {tier_instruction} {template} {template_instruction} "
        "{dartmouth_instruction}"
    )
    emailer.generate_email(
        _outreach_contact(), "send_first_touch",
        prompts={"outreach_prompt": custom_tpl},
    )
    assert captured[0].startswith("CUSTOM")


def test_generate_email_uses_custom_sender_profile(mocker):
    captured = []
    mocker.patch.object(
        emailer, "_call_claude",
        side_effect=lambda p: (captured.append(p), "body")[1],
    )
    emailer.generate_email(
        _outreach_contact(), "send_first_touch",
        prompts={"sender_profile": "Name: Jane"},
    )
    assert "Name: Jane" in captured[0]


def test_generate_email_falls_back_to_config_when_prompts_empty(mocker):
    captured = []
    mocker.patch.object(
        emailer, "_call_claude",
        side_effect=lambda p: (captured.append(p), "body")[1],
    )
    emailer.generate_email(_outreach_contact(), "send_first_touch", prompts={})
    # Config default contains the OUTREACH_PROMPT rules text
    assert "Sound human" in captured[0]


def test_generate_email_custom_applied_intro_prompt(mocker):
    captured = []
    mocker.patch.object(
        emailer, "_call_claude",
        side_effect=lambda p: (captured.append(p), "body")[1],
    )
    custom_tpl = (
        "APPLIED {profile} {name} {role} {company} "
        "{job_title} {job_description} {applied_date} {dartmouth_instruction}"
    )
    emailer.generate_email(
        _applied_contact(), "send_applied_intro",
        prompts={"applied_intro_prompt": custom_tpl},
    )
    assert captured[0].startswith("APPLIED")


def test_generate_email_custom_subject_prompt(mocker):
    captured = []
    mocker.patch.object(
        emailer, "_call_claude",
        side_effect=lambda p: (captured.append(p), "subject line")[1],
    )
    custom_subj = "SUBJ {name} {company} {mode} {job_title} {body}"
    emailer.generate_email(
        _outreach_contact(), "send_first_touch",
        prompts={"subject_prompt": custom_subj},
    )
    subject_prompt = captured[1]  # second call is subject
    assert subject_prompt.startswith("SUBJ")
