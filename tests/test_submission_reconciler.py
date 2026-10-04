"""Tests for submission_reconciler: Gmail-receipt resolution of needs_confirmation rows."""

from datetime import datetime, timedelta, timezone

import pytest

import submission_reconciler as sr

T0 = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def _app(company="Acme", role="Product Manager", attempted=T0, reason=None, id=1):
    return {"id": id, "company": company, "role": role, "apply_blocked_reason": reason,
            "submit_attempted_at": attempted.isoformat() if attempted else None,
            "approved_at": None, "updated_at": T0.isoformat(), "apply_preview": {}}


def _msg(frm='"Acme Recruiting" <no-reply@us.greenhouse-mail.io>',
         subject="Thank you for applying to Acme", delta=timedelta(minutes=2),
         is_reply=False, mid="<m1@x>", body=None):
    m = {"num": b"1", "message_id": mid, "from": frm, "subject": subject,
         "date": T0 + delta, "is_reply": is_reply}
    if body is not None:
        m["body"] = body
    return m


def test_greenhouse_style_matches():
    ev = sr.match_receipt(_app(), [_msg()], T0 + timedelta(hours=1))
    assert ev["source"] == "gmail_receipt"
    assert ev["message_id"] == "<m1@x>"
    assert ev["subject"] == "Thank you for applying to Acme"


def test_ashby_style_matches():
    m = _msg(frm='"Acme" <no-reply@ashbyhq.com>', subject="Thanks for applying")
    assert sr.match_receipt(_app(), [m], T0) is not None


def test_lever_domain_style_matches_after_suffix_strip():
    m = _msg(frm='"Acme Corp" <no-reply@hire.lever.co>', subject="Thanks for submitting your application")
    assert sr.match_receipt(_app(company="Acme Inc"), [m], T0) is not None


def test_domain_label_match_without_display_name():
    m = _msg(frm="no-reply@acme.ashbyhq.com", subject="Thank you for applying")
    assert sr.match_receipt(_app(), [m], T0) is not None


def test_reply_shaped_message_never_matches():
    assert sr.match_receipt(_app(), [_msg(is_reply=True)], T0) is None


def test_message_73h_after_window_does_not_match():
    assert sr.match_receipt(_app(), [_msg(delta=timedelta(hours=73))], T0) is None


def test_message_before_window_does_not_match():
    assert sr.match_receipt(_app(), [_msg(delta=timedelta(minutes=-5))], T0) is None


def test_message_just_inside_two_minute_slack_matches():
    assert sr.match_receipt(_app(), [_msg(delta=timedelta(seconds=-90))], T0) is not None


def test_thank_you_for_interest_alone_is_not_a_receipt():
    m = _msg(subject="Thank you for your interest in Acme")
    assert sr.match_receipt(_app(), [m], T0) is None


def test_company_match_without_receipt_phrase_is_newsletter():
    m = _msg(subject="Acme weekly newsletter")
    assert sr.match_receipt(_app(), [m], T0) is None


def test_receipt_phrase_for_other_company_does_not_match():
    m = _msg(frm='"Globex" <no-reply@globex.com>', subject="Thank you for applying to Globex")
    assert sr.match_receipt(_app(), [m], T0) is None


def test_company_is_whole_word_not_prefix():
    m = _msg(frm='"Acmeville Bank" <no-reply@acmeville.com>', subject="Thank you for applying to Acmeville Bank")
    assert sr.match_receipt(_app(), [m], T0) is None


def test_receipt_phrase_found_in_body():
    m = _msg(subject="Acme update", body="Hi! We have received your application.")
    assert sr.match_receipt(_app(), [m], T0) is not None


def test_earliest_qualifying_message_wins():
    a = _msg(mid="<late@x>", delta=timedelta(hours=3))
    b = _msg(mid="<early@x>", delta=timedelta(minutes=5))
    assert sr.match_receipt(_app(), [a, b], T0)["message_id"] == "<early@x>"


def test_window_start_falls_back_to_approved_then_updated():
    app = _app(attempted=None)
    app["approved_at"] = (T0 - timedelta(hours=1)).isoformat()
    assert sr.match_receipt(app, [_msg(delta=timedelta(minutes=-30))], T0) is not None
    app["approved_at"] = None
    assert sr.match_receipt(app, [_msg(delta=timedelta(minutes=-30))], T0) is None


# ── run() ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def env(mocker):
    mocker.patch.object(sr.config, "RECEIPT_IMAP_ADDRESS", "receipts@gmail.com")
    mocker.patch.object(sr.config, "RECEIPT_IMAP_APP_PASSWORD", "pw")
    return {
        "rows": mocker.patch.object(sr.db, "get_applications_needing_confirmation", return_value=[]),
        "fetch": mocker.patch.object(sr.gmail, "fetch_inbox_since", return_value=[]),
        "evidence": mocker.patch.object(sr.db, "record_receipt_evidence", return_value=True),
        "blocked": mocker.patch.object(sr.db, "set_apply_blocked"),
    }


def test_unconfigured_mailbox_skips_without_imap_or_escalation(mocker, env):
    mocker.patch.object(sr.config, "RECEIPT_IMAP_ADDRESS", None)
    env["rows"].return_value = [_app()]
    out = sr.run(now=T0 + timedelta(hours=2))
    env["fetch"].assert_not_called()
    env["blocked"].assert_not_called()
    env["evidence"].assert_not_called()
    assert out["escalated"] == 0 and out["resolved"] == 0


def test_no_rows_never_opens_imap(env):
    out = sr.run(now=T0)
    env["fetch"].assert_not_called()
    assert out["checked"] == 0


def test_resolves_matching_row(env):
    env["rows"].return_value = [_app()]
    env["fetch"].return_value = [_msg()]
    out = sr.run(now=T0 + timedelta(minutes=30))
    assert out["resolved"] == 1
    args = env["evidence"].call_args[0]
    assert args[0] == 1 and args[1]["message_id"] == "<m1@x>"
    env["blocked"].assert_not_called()


def test_fetch_called_once_with_bounded_since_and_address(env):
    env["rows"].return_value = [_app(id=1), _app(id=2, company="Globex")]
    sr.run(now=T0 + timedelta(minutes=30))
    assert env["fetch"].call_count == 1
    args = env["fetch"].call_args[0]
    assert args[0] == T0.date() and args[1] == "receipts@gmail.com" and args[2] == "pw"


def test_two_rows_same_company_receipt_names_one_role(env):
    env["rows"].return_value = [_app(id=1, role="Product Manager"), _app(id=2, role="Data Engineer")]
    env["fetch"].return_value = [_msg(subject="Thank you for applying to Acme: Data Engineer")]
    out = sr.run(now=T0 + timedelta(minutes=5))
    assert out["resolved"] == 1
    assert env["evidence"].call_args[0][0] == 2


def test_two_rows_same_company_receipt_without_role_resolves_neither(env):
    env["rows"].return_value = [_app(id=1, role="Product Manager"), _app(id=2, role="Data Engineer")]
    env["fetch"].return_value = [_msg()]
    out = sr.run(now=T0 + timedelta(minutes=5))
    assert out["resolved"] == 0
    env["evidence"].assert_not_called()


def test_message_consumed_by_at_most_one_row(env):
    env["rows"].return_value = [_app(id=1, role="Product Manager"), _app(id=2, role="Product Manager")]
    env["fetch"].return_value = [_msg(subject="Thank you for applying to Acme: Product Manager")]
    out = sr.run(now=T0 + timedelta(minutes=5))
    assert out["resolved"] == 0


def test_escalates_once_after_15_minutes(env):
    env["rows"].return_value = [_app(reason="clicked, unsure")]
    out = sr.run(now=T0 + timedelta(minutes=16))
    assert out["escalated"] == 1
    rid, text = env["blocked"].call_args[0]
    assert rid == 1 and text.startswith("No receipt email")
    assert "clicked, unsure" in text
    env["blocked"].reset_mock()
    env["rows"].return_value = [_app(reason=text)]
    out = sr.run(now=T0 + timedelta(minutes=60))
    env["blocked"].assert_not_called()
    assert out["escalated"] == 0


def test_no_escalation_before_15_minutes(env):
    env["rows"].return_value = [_app()]
    sr.run(now=T0 + timedelta(minutes=10))
    env["blocked"].assert_not_called()


def test_late_receipt_still_resolves_an_escalated_row(env):
    env["rows"].return_value = [_app(reason="No receipt email found 15+ min after submit")]
    env["fetch"].return_value = [_msg(delta=timedelta(hours=1))]
    out = sr.run(now=T0 + timedelta(hours=2))
    assert out["resolved"] == 1


def test_row_older_than_72h_is_not_searched(env):
    env["rows"].return_value = [_app(attempted=T0 - timedelta(hours=100))]
    sr.run(now=T0)
    env["fetch"].assert_not_called()


def test_evidence_failure_for_one_row_does_not_stop_next(env):
    env["rows"].return_value = [_app(id=1, company="Acme"), _app(id=2, company="Globex")]
    env["fetch"].return_value = [
        _msg(mid="<a@x>"),
        _msg(frm='"Globex" <no-reply@globex.com>', subject="Thank you for applying to Globex", mid="<g@x>"),
    ]
    env["evidence"].side_effect = [RuntimeError("db"), True]
    out = sr.run(now=T0 + timedelta(minutes=5))
    assert out["resolved"] == 1 and out["errors"] == 1


def test_imap_failure_returns_error_without_raising_or_escalating(env):
    env["rows"].return_value = [_app()]
    env["fetch"].side_effect = OSError("imap down")
    out = sr.run(now=T0 + timedelta(hours=2))
    assert out["errors"] == 1
    env["blocked"].assert_not_called()


def test_db_failure_never_raises(env):
    env["rows"].side_effect = RuntimeError("db down")
    out = sr.run(now=T0)
    assert out["errors"] == 1


def test_want_body_only_for_company_matched_non_reply_messages(env):
    env["rows"].return_value = [_app()]
    sr.run(now=T0 + timedelta(minutes=5))
    want = env["fetch"].call_args.kwargs["want_body"]
    assert want(_msg()) is True
    assert want(_msg(frm='"Globex" <a@globex.com>', subject="hello")) is False
    assert want(_msg(is_reply=True)) is False
