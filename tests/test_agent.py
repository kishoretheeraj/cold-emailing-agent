"""Tests for agent.py — decision logic, stage transitions, and run() orchestration."""

from datetime import date, timedelta

import pytest

import agent


# ── _parse_date ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, None),
        ("", None),
        (date(2026, 4, 21), date(2026, 4, 21)),
        ("2026-04-21", date(2026, 4, 21)),
        ("2026-04-21 08:00:00", date(2026, 4, 21)),  # truncates to first 10 chars
        ("not-a-date", None),
        ("2026-13-01", None),  # invalid month
    ],
)
def test_parse_date(value, expected):
    assert agent._parse_date(value) == expected


# ── decide_action — global skips ──────────────────────────────────────────────


@pytest.mark.parametrize("reply", ["replied", "interested", "call_scheduled", "dead"])
def test_decide_action_skips_when_replied(reply):
    contact = {"reply_status": reply, "stage": "first_touch_sent", "mode": "outreach"}
    assert agent.decide_action(contact, date.today()) == "skip"


def test_decide_action_skips_when_closed():
    contact = {"reply_status": "no_reply", "stage": "closed", "mode": "outreach"}
    assert agent.decide_action(contact, date.today()) == "skip"


def test_decide_action_routes_unknown_mode_to_skip():
    contact = {"reply_status": "no_reply", "stage": "new", "mode": "unicycle"}
    assert agent.decide_action(contact, date.today()) == "skip"


def test_decide_action_default_mode_is_outreach():
    # When `mode` key is absent, the default is "outreach".
    contact = {"reply_status": "no_reply", "stage": "new"}
    assert agent.decide_action(contact, date.today()) == "send_first_touch"


# ── _decide_outreach ──────────────────────────────────────────────────────────


TODAY = date(2026, 5, 6)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)


def test_outreach_new_contact():
    contact = {"mode": "outreach", "stage": "new", "reply_status": "no_reply"}
    assert agent.decide_action(contact, TODAY) == "send_first_touch"


@pytest.mark.parametrize(
    "stage",
    [
        "first_touch_drafted",
        "followup1_drafted",
        "followup2_drafted",
        "breakup_drafted",
    ],
)
def test_outreach_drafted_stages_skip(stage):
    contact = {"mode": "outreach", "stage": stage, "reply_status": "no_reply"}
    assert agent.decide_action(contact, TODAY) == "skip"


def test_outreach_breakup_sent_skips():
    contact = {
        "mode": "outreach",
        "stage": "breakup_sent",
        "reply_status": "no_reply",
        "followup_date": YESTERDAY,
    }
    assert agent.decide_action(contact, TODAY) == "skip"


@pytest.mark.parametrize(
    "stage, expected",
    [
        ("first_touch_sent", "send_followup1"),
        ("followup1_sent", "send_followup2"),
        ("followup2_sent", "send_breakup"),
    ],
)
def test_outreach_followup_due(stage, expected):
    contact = {
        "mode": "outreach",
        "stage": stage,
        "reply_status": "no_reply",
        "followup_date": str(YESTERDAY),
    }
    assert agent.decide_action(contact, TODAY) == expected


def test_outreach_followup_today_is_due():
    contact = {
        "mode": "outreach",
        "stage": "first_touch_sent",
        "reply_status": "no_reply",
        "followup_date": TODAY,
    }
    assert agent.decide_action(contact, TODAY) == "send_followup1"


def test_outreach_followup_not_yet_due_skips():
    contact = {
        "mode": "outreach",
        "stage": "first_touch_sent",
        "reply_status": "no_reply",
        "followup_date": str(TOMORROW),
    }
    assert agent.decide_action(contact, TODAY) == "skip"


def test_outreach_sent_with_no_followup_skips():
    # Edge: stage is _sent but followup_date is missing — should skip.
    contact = {
        "mode": "outreach",
        "stage": "first_touch_sent",
        "reply_status": "no_reply",
    }
    assert agent.decide_action(contact, TODAY) == "skip"


# ── _decide_applied ───────────────────────────────────────────────────────────


def test_applied_new_contact():
    contact = {"mode": "applied", "stage": "new", "reply_status": "no_reply"}
    assert agent.decide_action(contact, TODAY) == "send_applied_intro"


@pytest.mark.parametrize(
    "stage", ["applied_intro_drafted", "applied_followup_drafted"]
)
def test_applied_drafted_skips(stage):
    contact = {"mode": "applied", "stage": stage, "reply_status": "no_reply"}
    assert agent.decide_action(contact, TODAY) == "skip"


def test_applied_followup_sent_skips_terminal():
    contact = {
        "mode": "applied",
        "stage": "applied_followup_sent",
        "reply_status": "no_reply",
        "followup_date": YESTERDAY,
    }
    assert agent.decide_action(contact, TODAY) == "skip"


def test_applied_intro_sent_followup_due():
    contact = {
        "mode": "applied",
        "stage": "applied_intro_sent",
        "reply_status": "no_reply",
        "followup_date": str(YESTERDAY),
    }
    assert agent.decide_action(contact, TODAY) == "send_applied_followup"


def test_applied_intro_sent_followup_not_due():
    contact = {
        "mode": "applied",
        "stage": "applied_intro_sent",
        "reply_status": "no_reply",
        "followup_date": str(TOMORROW),
    }
    assert agent.decide_action(contact, TODAY) == "skip"


# ── _skip_reason ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "reply, expected",
    [
        ("replied", "replied (replied)"),
        ("interested", "replied (interested)"),
        ("call_scheduled", "replied (call_scheduled)"),
    ],
)
def test_skip_reason_replies(reply, expected):
    contact = {"reply_status": reply, "stage": "first_touch_sent"}
    assert agent._skip_reason(contact, TODAY) == expected


def test_skip_reason_dead():
    assert agent._skip_reason({"reply_status": "dead", "stage": "new"}, TODAY) == "marked dead"


def test_skip_reason_closed():
    assert (
        agent._skip_reason({"reply_status": "no_reply", "stage": "closed"}, TODAY)
        == "closed"
    )


def test_skip_reason_drafted():
    msg = agent._skip_reason(
        {"reply_status": "no_reply", "stage": "first_touch_drafted"}, TODAY
    )
    assert "draft pending" in msg


def test_skip_reason_future_followup():
    msg = agent._skip_reason(
        {
            "reply_status": "no_reply",
            "stage": "first_touch_sent",
            "followup_date": str(TOMORROW),
        },
        TODAY,
    )
    assert "followup not due" in msg
    assert str(TOMORROW) in msg


def test_skip_reason_default():
    assert (
        agent._skip_reason({"reply_status": "no_reply", "stage": "new"}, TODAY)
        == "no action needed"
    )


# ── Constant invariants ──────────────────────────────────────────────────────


def test_action_maps_have_consistent_keys():
    """Every action defined in NEXT_STAGE must also have a template and label."""
    assert set(agent.NEXT_STAGE) == set(agent.NEXT_TEMPLATE) == set(agent.ACTION_LABEL)


def test_action_label_format():
    """All labels are nested under 'Cold Outreach/' so they group in Gmail."""
    for action, label in agent.ACTION_LABEL.items():
        assert label.startswith("Cold Outreach/"), f"{action} -> {label}"


def test_next_stage_values_end_in_drafted():
    """Every action transitions a contact into a *_drafted stage."""
    for action, stage in agent.NEXT_STAGE.items():
        assert stage.endswith("_drafted"), f"{action} -> {stage}"


# ── run() integration ────────────────────────────────────────────────────────


def _build_contact(**overrides):
    contact = {
        "id": 1,
        "name": "Dana",
        "email": "dana@example.com",
        "company": "Clearbond",
        "mode": "outreach",
        "stage": "new",
        "reply_status": "no_reply",
        "tier": 2,
    }
    contact.update(overrides)
    return contact


def test_run_drafts_and_labels_a_new_contact(mocker):
    mocker.patch(
        "agent.get_all_contacts",
        return_value=[_build_contact()],
    )
    mocker.patch("agent.generate_email", return_value=("subj", "body"))
    create_draft = mocker.patch("agent.create_draft", return_value=("<mid@gmail.com>", 17850200168))
    apply_label = mocker.patch("agent.apply_label_to_latest_draft")
    update_contact = mocker.patch("agent.update_contact")
    save_thread_info = mocker.patch("agent.save_thread_info")
    mocker.patch("agent.insert_email_message")
    mocker.patch("agent.time.sleep")

    agent.run()

    # First-touch: no in_reply_to; contact_id and stage passed for idempotency
    args, kwargs = create_draft.call_args
    assert args == ("dana@example.com", "subj", "body")
    assert kwargs.get("contact_id") == 1
    assert kwargs.get("stage") == "new"
    apply_label.assert_called_once_with("Cold Outreach/First Touch")
    update_contact.assert_called_once()
    pos_args, kw_args = update_contact.call_args
    assert pos_args[0] == 1  # contact id
    assert pos_args[1] == "first_touch_drafted"
    assert kw_args.get("expected_stage") == "new"
    # Thread info saved after first touch
    save_thread_info.assert_called_once_with(1, "<mid@gmail.com>", "subj", gmail_thread_id=17850200168)



def test_run_does_not_block_on_label_failure(mocker):
    mocker.patch("agent.get_all_contacts", return_value=[_build_contact()])
    mocker.patch("agent.generate_email", return_value=("s", "b"))
    mocker.patch("agent.create_draft", return_value=("<mid@gmail.com>", 17850200168))
    mocker.patch(
        "agent.apply_label_to_latest_draft",
        side_effect=RuntimeError("imap down"),
    )
    update_contact = mocker.patch("agent.update_contact")
    mocker.patch("agent.save_thread_info")
    mocker.patch("agent.insert_email_message")
    mocker.patch("agent.time.sleep")

    # Must not raise — labeling is best-effort.
    agent.run()

    update_contact.assert_called_once()


def test_run_followup_passes_thread_headers(mocker):
    contact = _build_contact(
        stage="first_touch_sent",
        followup_date=str(date.today() - timedelta(days=1)),
        message_id="<orig@gmail.com>",
        original_subject="quick intro",
    )
    mocker.patch("agent.get_all_contacts", return_value=[contact])
    mocker.patch(
        "agent.get_thread_info",
        return_value={"message_id": "<orig@gmail.com>", "original_subject": "quick intro"},
    )
    mocker.patch("agent.generate_email", return_value=("Re: quick intro", "follow body"))
    create_draft = mocker.patch("agent.create_draft", return_value=("<fup@gmail.com>", None))
    mocker.patch("agent.apply_label_to_latest_draft")
    mocker.patch("agent.update_contact")
    save_thread_info = mocker.patch("agent.save_thread_info")
    mocker.patch("agent.insert_email_message")
    mocker.patch("agent.time.sleep")

    agent.run()

    # Follow-up must be sent with in_reply_to and idempotency kwargs
    args, kwargs = create_draft.call_args
    assert args == ("dana@example.com", "Re: quick intro", "follow body")
    assert kwargs.get("in_reply_to") == "<orig@gmail.com>"
    assert kwargs.get("contact_id") == 1
    assert kwargs.get("stage") == "first_touch_sent"
    # Thread info not re-saved for follow-ups
    save_thread_info.assert_not_called()


def test_run_skips_replied_contacts(mocker):
    mocker.patch(
        "agent.get_all_contacts",
        return_value=[_build_contact(reply_status="replied", stage="first_touch_sent")],
    )
    create_draft = mocker.patch("agent.create_draft")
    update_contact = mocker.patch("agent.update_contact")
    mocker.patch("agent.time.sleep")

    agent.run()

    create_draft.assert_not_called()
    update_contact.assert_not_called()


def test_run_exits_one_on_errors(mocker):
    mocker.patch("agent.get_all_contacts", return_value=[_build_contact()])
    mocker.patch("agent.generate_email", side_effect=RuntimeError("claude down"))
    mocker.patch("agent.time.sleep")

    with pytest.raises(SystemExit) as excinfo:
        agent.run()
    assert excinfo.value.code == 1


def test_run_skips_already_processed_today(mocker):
    contact = _build_contact(last_emailed=str(date.today()))
    mocker.patch("agent.get_all_contacts", return_value=[contact])
    create_draft = mocker.patch("agent.create_draft")
    update_contact = mocker.patch("agent.update_contact")
    mocker.patch("agent.time.sleep")

    agent.run()

    create_draft.assert_not_called()
    update_contact.assert_not_called()


def test_run_loads_prompts_and_passes_to_generate_email(mocker):
    mocker.patch("agent.get_all_contacts", return_value=[_build_contact()])
    mocker.patch("agent.load_prompts", return_value={"outreach_prompt": "LIVE"})
    generate_email = mocker.patch("agent.generate_email", return_value=("s", "b"))
    mocker.patch("agent.create_draft", return_value=("<mid@gmail.com>", 17850200168))
    mocker.patch("agent.apply_label_to_latest_draft")
    mocker.patch("agent.update_contact")
    mocker.patch("agent.save_thread_info")
    mocker.patch("agent.insert_email_message")
    mocker.patch("agent.time.sleep")

    agent.run()

    _, kwargs = generate_email.call_args
    assert kwargs.get("prompts") == {"outreach_prompt": "LIVE"}


def test_run_falls_back_to_empty_prompts_on_load_failure(mocker):
    mocker.patch("agent.get_all_contacts", return_value=[_build_contact()])
    mocker.patch("agent.load_prompts", side_effect=RuntimeError("db down"))
    generate_email = mocker.patch("agent.generate_email", return_value=("s", "b"))
    mocker.patch("agent.create_draft", return_value=("<mid@gmail.com>", 17850200168))
    mocker.patch("agent.apply_label_to_latest_draft")
    mocker.patch("agent.update_contact")
    mocker.patch("agent.save_thread_info")
    mocker.patch("agent.insert_email_message")
    mocker.patch("agent.time.sleep")

    agent.run()  # must not raise

    _, kwargs = generate_email.call_args
    assert kwargs.get("prompts") == {}


def test_run_skips_when_duplicate_draft_exists(mocker):
    mocker.patch("agent.get_all_contacts", return_value=[_build_contact()])
    mocker.patch("agent.generate_email", return_value=("subj", "body"))
    # create_draft returns None → duplicate found
    mocker.patch("agent.create_draft", return_value=(None, None))
    update_contact = mocker.patch("agent.update_contact")
    mocker.patch("agent.save_thread_info")
    mocker.patch("agent.time.sleep")

    agent.run()

    update_contact.assert_not_called()


def test_run_stores_first_touch_draft_in_email_messages(mocker):
    mocker.patch("agent.get_all_contacts", return_value=[_build_contact()])
    mocker.patch("agent.generate_email", return_value=("First Touch Subject", "Hello Dana"))
    mocker.patch("agent.create_draft", return_value=("<mid@gmail.com>", 17850200168))
    mocker.patch("agent.apply_label_to_latest_draft")
    mocker.patch("agent.update_contact")
    mocker.patch("agent.save_thread_info")
    insert_email_message = mocker.patch("agent.insert_email_message")
    mocker.patch("agent.time.sleep")

    agent.run()

    insert_email_message.assert_called_once()
    _, kwargs = insert_email_message.call_args
    assert kwargs["contact_id"] == 1
    assert kwargs["direction"] == "outgoing"
    assert kwargs["subject"] == "First Touch Subject"
    assert kwargs["body"] == "Hello Dana"
    assert kwargs["message_id"] == "<mid@gmail.com>"
    assert kwargs["in_reply_to"] is None      # first-touch has no thread to reply to
    assert kwargs["stage_at_send"] == "new"   # stage before the draft was created


def test_run_stores_followup_draft_with_in_reply_to(mocker):
    contact = _build_contact(
        stage="first_touch_sent",
        followup_date=str(date.today() - timedelta(days=1)),
        message_id="<orig@gmail.com>",
        original_subject="quick intro",
    )
    mocker.patch("agent.get_all_contacts", return_value=[contact])
    mocker.patch(
        "agent.get_thread_info",
        return_value={"message_id": "<orig@gmail.com>", "original_subject": "quick intro"},
    )
    mocker.patch("agent.generate_email", return_value=("Re: quick intro", "Follow-up body"))
    mocker.patch("agent.create_draft", return_value=("<fup@gmail.com>", None))
    mocker.patch("agent.apply_label_to_latest_draft")
    mocker.patch("agent.update_contact")
    mocker.patch("agent.save_thread_info")
    insert_email_message = mocker.patch("agent.insert_email_message")
    mocker.patch("agent.time.sleep")

    agent.run()

    insert_email_message.assert_called_once()
    _, kwargs = insert_email_message.call_args
    assert kwargs["direction"] == "outgoing"
    assert kwargs["message_id"] == "<fup@gmail.com>"
    assert kwargs["in_reply_to"] == "<orig@gmail.com>"
    assert kwargs["stage_at_send"] == "first_touch_sent"


def test_run_duplicate_draft_does_not_store_email_message(mocker):
    mocker.patch("agent.get_all_contacts", return_value=[_build_contact()])
    mocker.patch("agent.generate_email", return_value=("subj", "body"))
    mocker.patch("agent.create_draft", return_value=(None, None))
    mocker.patch("agent.update_contact")
    mocker.patch("agent.save_thread_info")
    insert_email_message = mocker.patch("agent.insert_email_message")
    mocker.patch("agent.time.sleep")

    agent.run()

    insert_email_message.assert_not_called()


# ── _validate_prompts ──────────────────────────────────────────────────────────

def test_validate_prompts_clean_returns_empty():
    assert agent._validate_prompts({}) == []


def test_validate_prompts_unknown_placeholder_flagged():
    problems = agent._validate_prompts({"subject_prompt": "Hello {First Name} from {name}"})
    assert len(problems) == 1
    assert "subject_prompt" in problems[0]
    assert "First Name" in problems[0]


def test_validate_prompts_valid_keys_not_flagged():
    assert agent._validate_prompts({"subject_prompt": "Hello {name} at {company}"}) == []


def test_validate_prompts_escaped_braces_not_flagged():
    # {{First Name}} is a literal in Python format strings, not a placeholder
    assert agent._validate_prompts({"subject_prompt": "read {{First Name}} as a tell, use {name}"}) == []


def test_validate_prompts_multiple_prompts_all_checked():
    problems = agent._validate_prompts({
        "subject_prompt": "{name} — good",
        "outreach_prompt": "{bad_key} in outreach",
    })
    assert len(problems) == 1
    assert "outreach_prompt" in problems[0]


def test_run_aborts_on_invalid_prompt(mocker):
    mocker.patch("agent.load_prompts", return_value={"subject_prompt": "text with {First Name}"})
    mocker.patch("agent.record_run")
    get_contacts = mocker.patch("agent.get_all_contacts")

    import pytest
    with pytest.raises(ValueError, match="Prompt validation failed"):
        agent.run()

    get_contacts.assert_not_called()
