"""Tests for monitor.detect_sent_drafts — auto-flips *_drafted to *_sent."""

from datetime import date, timedelta

import pytest

import monitor


MID = "<mid@mail.gmail.com>"


def _contact(**overrides):
    base = {
        "id": 42,
        "name": "Dana",
        "company": "Clearbond",
        "stage": "first_touch_drafted",
        "message_id": MID,
        "last_emailed": "2026-05-10",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("stage, expected_sent, mode, terminal", [
    ("first_touch_drafted",      "first_touch_sent",      "first_touch", False),
    ("followup1_drafted",        "followup1_sent",         "followup",    False),
    ("followup2_drafted",        "followup2_sent",         "followup",    False),
    ("breakup_drafted",          "breakup_sent",           "followup",    True),
    ("applied_intro_drafted",    "applied_intro_sent",     "first_touch", False),
    ("applied_followup_drafted", "applied_followup_sent",  "followup",    True),
])
def test_stage_flip(mocker, stage, expected_sent, mode, terminal):
    mocker.patch.object(monitor, "get_drafted_contacts",
                        return_value=[_contact(stage=stage)])
    find_sent = mocker.patch.object(monitor, "find_sent_for_thread", return_value=MID)
    update = mocker.patch.object(monitor, "update_contact")
    mocker.patch.object(monitor, "update_message_id")
    mocker.patch.object(monitor, "update_latest_message_id")

    monitor.detect_sent_drafts()

    update.assert_called_once()
    _, actual_stage = update.call_args.args[:2]
    kwargs = update.call_args.kwargs
    assert actual_stage == expected_sent

    if terminal:
        assert kwargs.get("clear_followup_date") is True
        assert kwargs.get("followup_days") is None
    else:
        assert isinstance(kwargs.get("followup_days"), int)
        assert kwargs["followup_days"] > 0

    assert find_sent.call_args.args[2] == mode


def test_no_match_does_not_update(mocker):
    mocker.patch.object(monitor, "get_drafted_contacts",
                        return_value=[_contact()])
    mocker.patch.object(monitor, "find_sent_for_thread", return_value=None)
    update = mocker.patch.object(monitor, "update_contact")

    monitor.detect_sent_drafts()

    update.assert_not_called()


def test_null_message_id_skips_imap_and_update(mocker):
    mocker.patch.object(monitor, "get_drafted_contacts",
                        return_value=[_contact(message_id=None)])
    find_sent = mocker.patch.object(monitor, "find_sent_for_thread")
    update = mocker.patch.object(monitor, "update_contact")

    monitor.detect_sent_drafts()

    find_sent.assert_not_called()
    update.assert_not_called()


def test_imap_failure_continues_to_next_contact(mocker):
    contacts = [
        _contact(id=1, message_id="<first@m>"),
        _contact(id=2, message_id="<second@m>"),
    ]
    mocker.patch.object(monitor, "get_drafted_contacts", return_value=contacts)
    mocker.patch.object(monitor, "find_sent_for_thread",
                        side_effect=[Exception("imap down"), "<second@m>"])
    update = mocker.patch.object(monitor, "update_contact")
    mocker.patch.object(monitor, "update_message_id")
    mocker.patch.object(monitor, "update_latest_message_id")

    monitor.detect_sent_drafts()

    update.assert_called_once()
    assert update.call_args.args[0] == 2


def test_db_update_failure_continues(mocker):
    mocker.patch.object(monitor, "get_drafted_contacts",
                        return_value=[_contact()])
    mocker.patch.object(monitor, "find_sent_for_thread", return_value=MID)
    mocker.patch.object(monitor, "update_contact",
                        side_effect=Exception("supabase timeout"))
    mocker.patch.object(monitor, "update_message_id")
    mocker.patch.object(monitor, "update_latest_message_id")

    monitor.detect_sent_drafts()


def test_unknown_stage_no_update(mocker):
    mocker.patch.object(monitor, "get_drafted_contacts",
                        return_value=[_contact(stage="mystery_drafted")])
    mocker.patch.object(monitor, "find_sent_for_thread", return_value=MID)
    update = mocker.patch.object(monitor, "update_contact")
    mocker.patch.object(monitor, "update_message_id")
    mocker.patch.object(monitor, "update_latest_message_id")

    monitor.detect_sent_drafts()

    update.assert_not_called()


def test_since_date_uses_last_emailed(mocker):
    mocker.patch.object(monitor, "get_drafted_contacts",
                        return_value=[_contact(last_emailed="2026-04-15")])
    find_sent = mocker.patch.object(monitor, "find_sent_for_thread", return_value=None)
    mocker.patch.object(monitor, "update_contact")

    monitor.detect_sent_drafts()

    _, since, _ = find_sent.call_args.args
    assert since == date(2026, 4, 15)


def test_since_date_fallback_when_last_emailed_missing(mocker):
    mocker.patch.object(monitor, "get_drafted_contacts",
                        return_value=[_contact(last_emailed=None)])
    find_sent = mocker.patch.object(monitor, "find_sent_for_thread", return_value=None)
    mocker.patch.object(monitor, "update_contact")

    monitor.detect_sent_drafts()

    _, since, _ = find_sent.call_args.args
    assert since == date.today() - timedelta(days=60)


def test_message_id_updated_when_gmail_rewrites_it(mocker):
    """When Gmail sends a draft with a different Message-ID, update it for threading."""
    original_mid = "<original@m>"
    rewritten_mid = "<rewritten-by-gmail@m>"
    mocker.patch.object(monitor, "get_drafted_contacts",
                        return_value=[_contact(stage="first_touch_drafted", message_id=original_mid)])
    mocker.patch.object(monitor, "find_sent_for_thread", return_value=rewritten_mid)
    mocker.patch.object(monitor, "update_contact")
    update_mid = mocker.patch.object(monitor, "update_message_id")
    mocker.patch.object(monitor, "update_latest_message_id")

    monitor.detect_sent_drafts()

    update_mid.assert_called_once_with(42, rewritten_mid)


def test_message_id_not_updated_when_unchanged(mocker):
    """No update_message_id call when Gmail preserves the original Message-ID."""
    mocker.patch.object(monitor, "get_drafted_contacts",
                        return_value=[_contact(stage="first_touch_drafted")])
    mocker.patch.object(monitor, "find_sent_for_thread", return_value=MID)
    mocker.patch.object(monitor, "update_contact")
    update_mid = mocker.patch.object(monitor, "update_message_id")
    mocker.patch.object(monitor, "update_latest_message_id")

    monitor.detect_sent_drafts()

    update_mid.assert_not_called()


def test_subject_fallback_used_when_message_id_not_found(mocker):
    """When Gmail rewrites the ID and primary search fails, subject fallback flips stage."""
    fallback_mid = "<found-by-subject@m>"
    mocker.patch.object(monitor, "get_drafted_contacts",
                        return_value=[_contact(stage="first_touch_drafted",
                                               original_subject="built insurance infra")])
    mocker.patch.object(monitor, "find_sent_for_thread", return_value=None)
    subj_search = mocker.patch.object(monitor, "find_sent_by_subject", return_value=fallback_mid)
    update = mocker.patch.object(monitor, "update_contact")
    update_mid = mocker.patch.object(monitor, "update_message_id")
    mocker.patch.object(monitor, "update_latest_message_id")

    monitor.detect_sent_drafts()

    subj_search.assert_called_once()
    update.assert_called_once()
    update_mid.assert_called_once_with(42, fallback_mid)


def test_subject_fallback_not_used_for_followup_mode(mocker):
    """Subject fallback only runs for first_touch mode, not followups."""
    mocker.patch.object(monitor, "get_drafted_contacts",
                        return_value=[_contact(stage="followup1_drafted",
                                               original_subject="built insurance infra")])
    mocker.patch.object(monitor, "find_sent_for_thread", return_value=None)
    subj_search = mocker.patch.object(monitor, "find_sent_by_subject")
    mocker.patch.object(monitor, "update_contact")

    monitor.detect_sent_drafts()

    subj_search.assert_not_called()


def test_thread_id_search_takes_priority_over_message_id(mocker):
    """When gmail_thread_id is stored, X-GM-THRID search fires first and skips Message-ID search."""
    mocker.patch.object(monitor, "get_drafted_contacts",
                        return_value=[_contact(stage="first_touch_drafted",
                                               gmail_thread_id=17850200168)])
    thrid_search = mocker.patch.object(monitor, "find_sent_by_thread_id", return_value=MID)
    mid_search = mocker.patch.object(monitor, "find_sent_for_thread")
    mocker.patch.object(monitor, "update_contact")
    mocker.patch.object(monitor, "update_message_id")
    mocker.patch.object(monitor, "update_latest_message_id")

    monitor.detect_sent_drafts()

    thrid_search.assert_called_once()
    mid_search.assert_not_called()


def test_thread_id_search_falls_through_to_message_id(mocker):
    """If X-GM-THRID search returns None, fall through to Message-ID search."""
    mocker.patch.object(monitor, "get_drafted_contacts",
                        return_value=[_contact(stage="first_touch_drafted",
                                               gmail_thread_id=17850200168)])
    mocker.patch.object(monitor, "find_sent_by_thread_id", return_value=None)
    mid_search = mocker.patch.object(monitor, "find_sent_for_thread", return_value=MID)
    mocker.patch.object(monitor, "update_contact")
    mocker.patch.object(monitor, "update_message_id")
    mocker.patch.object(monitor, "update_latest_message_id")

    monitor.detect_sent_drafts()

    mid_search.assert_called_once()


def test_message_id_not_updated_for_followup_mode(mocker):
    """message_id update only runs for first_touch mode, not followups."""
    mocker.patch.object(monitor, "get_drafted_contacts",
                        return_value=[_contact(stage="followup1_drafted", message_id="<orig@m>")])
    mocker.patch.object(monitor, "find_sent_for_thread", return_value="<different@m>")
    mocker.patch.object(monitor, "update_contact")
    update_mid = mocker.patch.object(monitor, "update_message_id")
    mocker.patch.object(monitor, "update_latest_message_id")

    monitor.detect_sent_drafts()

    update_mid.assert_not_called()


# ── latest_message_id updated for all sent detections ─────────────────────────

@pytest.mark.parametrize("stage", [
    "first_touch_drafted",
    "followup1_drafted",
    "followup2_drafted",
    "applied_intro_drafted",
])
def test_latest_message_id_updated_for_all_modes(mocker, stage):
    """update_latest_message_id is called after every successful sent detection."""
    actual_mid = "<actual-sent@mail.gmail.com>"
    mocker.patch.object(monitor, "get_drafted_contacts",
                        return_value=[_contact(stage=stage)])
    mocker.patch.object(monitor, "find_sent_for_thread", return_value=actual_mid)
    mocker.patch.object(monitor, "update_contact")
    mocker.patch.object(monitor, "update_message_id")
    update_latest = mocker.patch.object(monitor, "update_latest_message_id")

    monitor.detect_sent_drafts()

    update_latest.assert_called_once_with(42, actual_mid)


def test_latest_message_id_not_updated_when_not_detected(mocker):
    """update_latest_message_id is not called when no sent email is found."""
    mocker.patch.object(monitor, "get_drafted_contacts",
                        return_value=[_contact()])
    mocker.patch.object(monitor, "find_sent_for_thread", return_value=None)
    update_latest = mocker.patch.object(monitor, "update_latest_message_id")

    monitor.detect_sent_drafts()

    update_latest.assert_not_called()
