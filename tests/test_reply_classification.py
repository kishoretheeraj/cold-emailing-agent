"""Tests for _classify_reply in monitor.py."""

import json
import pytest
import monitor


def _contact():
    return {"id": 1, "name": "Alice", "company": "Acme", "stage": "first_touch_sent"}


def _prompts():
    return {}


# ── Auto-reply header bypass ───────────────────────────────────────────────────

def test_is_auto_reply_auto_submitted(mocker):
    msg = mocker.MagicMock()
    msg.get.side_effect = lambda h, d="": {
        "Auto-Submitted": "auto-replied",
        "X-Auto-Response-Suppress": "",
    }.get(h, d)
    assert monitor._is_auto_reply(msg) is True


def test_is_auto_reply_x_suppress(mocker):
    msg = mocker.MagicMock()
    msg.get.side_effect = lambda h, d="": {
        "Auto-Submitted": "no",
        "X-Auto-Response-Suppress": "All",
    }.get(h, d)
    assert monitor._is_auto_reply(msg) is True


def test_is_auto_reply_normal_email(mocker):
    msg = mocker.MagicMock()
    msg.get.side_effect = lambda h, d="": {"Auto-Submitted": "no"}.get(h, d)
    assert monitor._is_auto_reply(msg) is False


# ── _classify_reply ────────────────────────────────────────────────────────────

def test_classify_positive(mocker):
    mocker.patch.object(
        monitor, "_call_claude",
        return_value=json.dumps({"classifier_status": "positive_reply"}),
    )
    result = monitor._classify_reply("Sure, let's chat!", _contact(), _prompts())
    assert result == "positive_reply"


def test_classify_hard_no(mocker):
    mocker.patch.object(
        monitor, "_call_claude",
        return_value=json.dumps({"classifier_status": "hard_no"}),
    )
    result = monitor._classify_reply("Not interested.", _contact(), _prompts())
    assert result == "hard_no"


def test_classify_fallback_on_error(mocker):
    mocker.patch.object(monitor, "_call_claude", side_effect=Exception("API error"))
    result = monitor._classify_reply("Some reply", _contact(), _prompts())
    assert result == "unrelated"


def test_classify_fallback_on_bad_json(mocker):
    mocker.patch.object(monitor, "_call_claude", return_value="not json")
    result = monitor._classify_reply("Some reply", _contact(), _prompts())
    assert result == "unrelated"


def test_classify_uses_haiku_model(mocker):
    from config import REPLY_CLASSIFICATION_MODEL
    mock = mocker.patch.object(
        monitor, "_call_claude",
        return_value=json.dumps({"classifier_status": "soft_yes"}),
    )
    monitor._classify_reply("Maybe!", _contact(), _prompts())
    call_kwargs = mock.call_args
    assert call_kwargs[1].get("model") == REPLY_CLASSIFICATION_MODEL or \
           (len(call_kwargs[0]) > 1 and call_kwargs[0][1] == REPLY_CLASSIFICATION_MODEL)


def test_classify_strips_code_fences(mocker):
    mocker.patch.object(
        monitor, "_call_claude",
        return_value='```json\n{"classifier_status": "out_of_office"}\n```',
    )
    result = monitor._classify_reply("OOO until Monday", _contact(), _prompts())
    assert result == "out_of_office"


# ── _is_notification_sender ────────────────────────────────────────────────────

def test_is_notification_sender_mailsuite():
    assert monitor._is_notification_sender("Mailsuite Notification <notification@mailsuite.com>") is True


def test_is_notification_sender_mailtrack():
    assert monitor._is_notification_sender("Mailtrack <me@mailtrack.io>") is True


def test_is_notification_sender_real_email():
    assert monitor._is_notification_sender("George Allen <gkentonallen@gmail.com>") is False


def test_is_notification_sender_empty():
    assert monitor._is_notification_sender("") is False
