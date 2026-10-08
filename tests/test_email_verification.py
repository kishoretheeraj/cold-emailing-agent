"""email_verification.py: a verification code or link from the receipt inbox. IMAP is mocked."""

from datetime import datetime, timedelta, timezone

import pytest

import config
import email_verification

SINCE = datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc)


def _msg(sender, body, minutes_after=1, subject="Verify your account", is_reply=False):
    return {"num": b"1", "message_id": f"<{sender}{minutes_after}@x>", "from": sender,
            "subject": subject, "date": SINCE + timedelta(minutes=minutes_after),
            "is_reply": is_reply, "body": body}


@pytest.fixture(autouse=True)
def _inbox(mocker):
    mocker.patch.object(config, "RECEIPT_IMAP_ADDRESS", "me@example.com")
    mocker.patch.object(config, "RECEIPT_IMAP_APP_PASSWORD", "app-password")
    now = {"t": 0.0}
    mocker.patch.object(email_verification.time, "monotonic", side_effect=lambda: now["t"])
    mocker.patch.object(email_verification.time, "sleep",
                        side_effect=lambda s: now.__setitem__("t", now["t"] + s))
    return mocker.patch.object(email_verification.gmail, "fetch_inbox_since", return_value=[])


def _wait(**kw):
    args = dict(sender_domains=("myworkday.com",), since=SINCE, timeout_seconds=30, poll_seconds=10)
    args.update(kw)
    return email_verification.wait_for_verification(**args)


@pytest.mark.parametrize("body,code", [
    ("Your verification code is 482913. It expires in 10 minutes.", "482913"),
    ("Use code: 7Q4K9Z to verify your email address.", "7Q4K9Z"),
    ("Verification Code\n\n  305 112  \n", "305112"),
])
def test_finds_a_code(_inbox, body, code):
    _inbox.return_value = [_msg("Acme Careers <acme@myworkday.com>", body)]
    assert _wait() == {"code": code}


def test_finds_a_verification_link_on_an_allowed_host(_inbox):
    body = ("Click to verify: https://acme.wd5.myworkdayjobs.com/External/activate/abc123?x=1 "
            "or visit https://tracking.example.net/click?u=1")
    _inbox.return_value = [_msg("acme@myworkday.com", body)]
    got = _wait(allowed_link_hosts=("acme.wd5.myworkdayjobs.com",))
    assert got == {"link": "https://acme.wd5.myworkdayjobs.com/External/activate/abc123?x=1"}


@pytest.mark.parametrize("body", [
    "Please verify your email address to continue.",
    "Verify here: https://acme.wd5.myworkdayjobs.com/activate/x7k2m9q4",
    "Your verification is complete. Reference: abcdef12",
])
def test_no_code_is_invented_from_ordinary_text_or_links(_inbox, body):
    _inbox.return_value = [_msg("acme@myworkday.com", body)]
    assert _wait() is None


def test_never_returns_a_link_on_another_host(_inbox):
    _inbox.return_value = [_msg("acme@myworkday.com", "Verify: https://evil.example/activate/abc")]
    assert _wait(allowed_link_hosts=("acme.wd5.myworkdayjobs.com",)) is None


def test_lookalike_hosts_are_not_allowed(_inbox):
    _inbox.return_value = [_msg("acme@myworkday.com",
                                "Verify: https://acme.wd5.myworkdayjobs.com.evil.example/activate/a")]
    assert _wait(allowed_link_hosts=("acme.wd5.myworkdayjobs.com",)) is None


@pytest.mark.parametrize("sender", [
    "someone@gmail.com", "acme@myworkday.com.evil.example", "acme@notmyworkday.com",
])
def test_ignores_other_senders(_inbox, sender):
    _inbox.return_value = [_msg(sender, "Your verification code is 482913.")]
    assert _wait() is None


def test_accepts_a_subdomain_of_the_sender_domain(_inbox):
    _inbox.return_value = [_msg("no-reply@acme.myworkday.com", "Your verification code is 482913.")]
    assert _wait() == {"code": "482913"}


def test_ignores_mail_from_before_the_action(_inbox):
    _inbox.return_value = [_msg("acme@myworkday.com", "Your verification code is 111111.", minutes_after=-5)]
    assert _wait() is None


def test_ignores_replies(_inbox):
    _inbox.return_value = [_msg("acme@myworkday.com", "code 482913", is_reply=True)]
    assert _wait() is None


def test_newest_matching_message_wins(_inbox):
    _inbox.return_value = [_msg("acme@myworkday.com", "Your verification code is 111111.", minutes_after=1),
                           _msg("acme@myworkday.com", "Your verification code is 222222.", minutes_after=3)]
    assert _wait() == {"code": "222222"}


def test_polls_until_the_mail_arrives(_inbox):
    _inbox.side_effect = [[], [_msg("acme@myworkday.com", "Your verification code is 482913.")]]
    assert _wait() == {"code": "482913"}
    assert _inbox.call_count == 2


def test_times_out_with_none(_inbox):
    assert _wait(timeout_seconds=30, poll_seconds=10) is None
    assert _inbox.call_count == 4  # at 0, 10, 20 and 30 seconds


def test_a_failing_inbox_is_retried_then_gives_up(_inbox):
    _inbox.side_effect = RuntimeError("IMAP down")
    assert _wait(timeout_seconds=20, poll_seconds=10) is None


def test_unconfigured_inbox_returns_none_without_connecting(_inbox, mocker):
    mocker.patch.object(config, "RECEIPT_IMAP_ADDRESS", None)
    assert _wait() is None
    _inbox.assert_not_called()


def test_only_bodies_from_matching_senders_are_fetched(_inbox):
    _wait(timeout_seconds=10, poll_seconds=10)
    want_body = _inbox.call_args.kwargs["want_body"]
    assert want_body({"from": "acme@myworkday.com", "is_reply": False}) is True
    assert want_body({"from": "friend@gmail.com", "is_reply": False}) is False
