"""Reply store: classifier, correlation, status precedence, dedup, redaction, and the poller."""

import hashlib
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

import application_replies
import reply_repo

UTC = timezone.utc
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def _app(app_id, company="Acme", role="Product Manager", submitted_at=T0, thread_keys=(), tenant_domains=()):
    return {"id": str(app_id), "job_id": f"job-{app_id}", "company": company, "role": role,
            "submitted_at": submitted_at, "thread_keys": list(thread_keys),
            "tenant_domains": list(tenant_domains)}


def _msg(**over):
    base = {"message_id": "<m@x>", "in_reply_to": None, "references": [], "from_domain": "acme.com",
            "subject": "Update", "body": "", "date": T0 + timedelta(days=3)}
    base.update(over)
    return base


@pytest.fixture
def conn(tmp_path):
    c = reply_repo.connect(str(tmp_path / "pipeline.db"))
    yield c
    c.close()


def _record(conn, **over):
    args = dict(application_id="1", job_id="job-1", message_id="<a@x>", thread_key=None, source="imap",
                from_addr="hr@acme.com", from_domain="acme.com", subject="Hello",
                received_at="2026-10-02T10:00:00+00:00", snippet="body", kind="confirmation",
                confidence=0.9, match_method="domain", needs_review=False)
    args.update(over)
    return reply_repo.record_reply(conn, **args)


# ── classifier ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("subject,body,expected", [
    ("Your verification code", "Your code is 482913", None),
    ("Verify your email", "Click to confirm your email address", None),
    ("Application received", "Verification code: 123456. Application received.", None),
    ("Thanks for applying", "Thank you for applying to Acme. If your qualifications match, we will reach out "
                            "to schedule an interview.", "confirmation"),
    ("Your application", "We have received your application.", "confirmation"),
    ("Next step", "Please complete the online assessment on HackerRank.", "assessment"),
    ("Let's talk", "We would like to invite you to an interview. https://calendly.com/acme/30", "interview"),
    ("Update", "We have decided not to move forward with your candidacy.", "rejection"),
    ("Update", "We regret to inform you, although we would like to invite you to an interview someday.",
     "rejection"),
    ("Offer", "We are pleased to extend an offer of employment.", "offer"),
    ("Newsletter", "Here is our weekly digest.", "other"),
])
def test_classify_kind(subject, body, expected):
    assert application_replies.classify_kind(subject, body, "acme.com") == expected


# ── dedup, fallback id, redaction ──────────────────────────────────────────────

def test_dedup_on_repeated_message_id(conn):
    first = _record(conn)
    second = _record(conn, subject="different")
    assert first["deduped"] is False
    assert second["deduped"] is True
    assert second["id"] == first["id"]
    assert conn.execute("SELECT COUNT(*) FROM replies").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM application_status_events").fetchone()[0] == 1


def test_missing_message_id_uses_sha1_fallback(conn):
    row = _record(conn, message_id=None, from_addr="a@x.com", subject="Subj")
    expected = "noid:" + hashlib.sha1(b"a@x.com|Subj|2026-10-02T10:00:00+00:00").hexdigest()[:32]
    assert row["message_id"] == expected
    assert _record(conn, message_id="", from_addr="a@x.com", subject="Subj")["deduped"] is True


def test_redact_digit_runs():
    assert reply_repo.redact("code 123456 and 12345 and 1234567890") == "code [digits] and 12345 and [phone]"
    assert reply_repo.redact(None) == ""


def test_redact_emails_urls_and_phones():
    text = "mail me at jane.doe@example.com or call 555-123-4567, see https://example.com/reset?token=abc123"
    redacted = reply_repo.redact(text)
    assert "jane.doe@example.com" not in redacted
    assert "555-123-4567" not in redacted
    assert "token=abc123" not in redacted
    assert "[email]" in redacted and "[phone]" in redacted and "[url]" in redacted


def test_short_company_name_only_matches_domain():
    a = _app(1, company="As", tenant_domains=[])
    # "As" appears as an ordinary word in the subject; it must not identify the sender.
    msg = _msg(from_domain="mail.example.net", subject="As discussed, next steps", body="hello")
    assert reply_repo.correlate(msg, [a]) == (None, "none", False)
    # ...but it still matches via the domain label.
    msg2 = _msg(from_domain="careers.as.com", subject="Update", body="hello")
    assert reply_repo.correlate(msg2, [a]) == (a, "company", False)


def test_company_body_mention_does_not_identify():
    a = _app(1, company="Acme")
    msg = _msg(from_domain="mail.example.net", subject="Newsletter", body="Acme was mentioned in the news today")
    assert reply_repo.correlate(msg, [a]) == (None, "none", False)


def test_single_candidate_submitted_after_message_is_ineligible():
    a = _app(1, submitted_at=T0 + timedelta(days=10), tenant_domains=["acme.com"])
    assert reply_repo.correlate(_msg(), [a]) == (None, "domain", False)


def test_domain_multi_match_prefers_named_role():
    old = _app(1, submitted_at=T0, tenant_domains=["acme.com"])
    new = _app(2, role="Data Engineer", submitted_at=T0 + timedelta(days=1), tenant_domains=["acme.com"])
    msg = _msg(subject="Product Manager application update")
    assert reply_repo.correlate(msg, [old, new]) == (old, "domain_role", False)


def test_snippet_redacted_and_capped(conn):
    row = _record(conn, snippet="Your code is 482913 " + "x" * 400)
    assert "482913" not in row["snippet"]
    assert len(row["snippet"]) <= 200


# ── correlation ────────────────────────────────────────────────────────────────

def test_thread_match_beats_domain_and_company():
    a = _app(1, tenant_domains=["acme.com"])
    b = _app(2, role="Data Engineer", thread_keys=["<root@x>"], tenant_domains=["acme.com"])
    app, method, review = reply_repo.correlate(_msg(in_reply_to="<root@x>"), [a, b])
    assert (app, method, review) == (b, "thread", False)


def test_thread_match_via_references():
    a = _app(1, thread_keys=["<root@x>"])
    app, method, review = reply_repo.correlate(_msg(references=["<other@x>", "<root@x>"]), [a])
    assert (app, method, review) == (a, "thread", False)


def test_single_domain_candidate():
    a = _app(1, tenant_domains=["acme.com"])
    assert reply_repo.correlate(_msg(from_domain="jobs.acme.com"), [a]) == (a, "domain", False)


def test_domain_latest_submitted_wins():
    old = _app(1, submitted_at=T0, tenant_domains=["acme.com"])
    new = _app(2, role="Data Engineer", submitted_at=T0 + timedelta(days=1), tenant_domains=["acme.com"])
    assert reply_repo.correlate(_msg(), [old, new]) == (new, "domain_role", False)


def test_domain_ignores_applications_submitted_after_the_message():
    old = _app(1, submitted_at=T0, tenant_domains=["acme.com"])
    future = _app(2, submitted_at=T0 + timedelta(days=10), tenant_domains=["acme.com"])
    assert reply_repo.correlate(_msg(), [old, future]) == (old, "domain_role", False)


def test_domain_tie_needs_review():
    a = _app(1, tenant_domains=["acme.com"])
    b = _app(2, role="Data Engineer", tenant_domains=["acme.com"])
    assert reply_repo.correlate(_msg(), [a, b]) == (None, "domain", True)


def test_company_two_roles_disambiguated_by_role_phrase():
    pm = _app(1, role="Product Manager")
    de = _app(2, role="Data Engineer")
    msg = _msg(from_domain="mail.example.net", subject="Acme: Product Manager application", body="Acme here")
    assert reply_repo.correlate(msg, [pm, de]) == (pm, "company_role", False)


def test_company_both_roles_named_latest_wins():
    pm = _app(1, role="Product Manager", submitted_at=T0)
    de = _app(2, role="Data Engineer", submitted_at=T0 + timedelta(days=1))
    msg = _msg(from_domain="mail.example.net", subject="Acme: your Product Manager and Data Engineer applications")
    assert reply_repo.correlate(msg, [pm, de]) == (de, "company_role", False)


def test_company_tie_needs_review():
    pm = _app(1, role="Product Manager")
    de = _app(2, role="Data Engineer")
    msg = _msg(from_domain="mail.example.net", subject="Acme update", body="Product Manager and Data Engineer")
    assert reply_repo.correlate(msg, [pm, de]) == (None, "company", True)


def test_company_multiple_without_role_needs_review():
    pm = _app(1, role="Product Manager")
    de = _app(2, role="Data Engineer", submitted_at=T0 + timedelta(days=1))
    msg = _msg(from_domain="acme.com", body="We are reviewing your application")
    assert reply_repo.correlate(msg, [pm, de]) == (None, "company", True)


def test_no_match():
    assert reply_repo.correlate(_msg(from_domain="other.org", body="hello"), [_app(1)]) == (None, "none", False)


# ── advance / status precedence ────────────────────────────────────────────────

@pytest.mark.parametrize("current,kind,expected", [
    (None, "confirmation", "confirmed"),
    ("submitted", "confirmation", "confirmed"),
    ("confirmed", "confirmation", None),
    ("interview", "confirmation", None),
    ("confirmed", "assessment", "assessment"),
    ("interview", "assessment", None),
    ("assessment", "interview", "interview"),
    ("interview", "offer", "offer"),
    ("offer", "interview", None),
    ("submitted", "rejection", "rejected"),
    ("confirmed", "rejection", "rejected"),
    ("assessment", "rejection", "rejected"),
    ("interview", "rejection", None),
    ("offer", "rejection", None),
    ("rejected", "rejection", None),
    ("rejected", "interview", "interview"),
    ("rejected", "offer", "offer"),
    ("rejected", "confirmation", None),
    ("rejected", "assessment", None),
    ("submitted", "other", None),
    (None, "other", None),
    ("interview", "other", None),
])
def test_advance(current, kind, expected):
    assert reply_repo.advance(current, kind) == expected


def test_rejection_then_interview_overrides(conn):
    _record(conn, message_id="<r@x>", kind="rejection", received_at="2026-10-02T10:00:00+00:00")
    assert reply_repo.get_current_status(conn, "1") == "rejected"
    row = _record(conn, message_id="<i@x>", kind="interview", received_at="2026-10-05T10:00:00+00:00")
    assert row["status_after"] == "interview"
    assert reply_repo.get_current_status(conn, "1") == "interview"


def test_rejection_after_interview_changes_nothing(conn):
    _record(conn, message_id="<i@x>", kind="interview", received_at="2026-10-02T10:00:00+00:00")
    row = _record(conn, message_id="<r@x>", kind="rejection", received_at="2026-10-05T10:00:00+00:00")
    assert row["status_after"] is None
    assert reply_repo.get_current_status(conn, "1") == "interview"


def test_stale_interview_does_not_revive_newer_rejection(conn):
    _record(conn, message_id="<r@x>", kind="rejection", received_at="2026-10-05T10:00:00+00:00")
    row = _record(conn, message_id="<i@x>", kind="interview", received_at="2026-10-02T10:00:00+00:00")
    assert row["status_after"] is None
    assert reply_repo.get_current_status(conn, "1") == "rejected"


def test_repeat_rejection_extends_timeline(conn):
    _record(conn, message_id="<r1@x>", kind="rejection", received_at="2026-10-02T10:00:00+00:00")
    row = _record(conn, message_id="<r2@x>", kind="rejection", received_at="2026-10-05T10:00:00+00:00")
    assert row["status_after"] == "rejected"
    # The newer rejection is what the revival guard judges against: an interview dated Oct 4
    # (after the first rejection but before the second) must not revive the application.
    row = _record(conn, message_id="<i@x>", kind="interview", received_at="2026-10-04T10:00:00+00:00")
    assert row["status_after"] is None
    assert reply_repo.get_current_status(conn, "1") == "rejected"


def test_thread_match_respects_submission_date():
    a = _app(1, submitted_at=T0 + timedelta(days=10), thread_keys=["<root@x>"])
    msg = _msg(in_reply_to="<root@x>", date=T0 + timedelta(days=3))
    assert reply_repo.correlate(msg, [a]) == (None, "thread", False)


def test_role_must_match_as_phrase():
    pm = _app(1, role="Product Manager")
    de = _app(2, role="Data Engineer")
    msg = _msg(from_domain="mail.example.net", subject="Acme update",
               body="Our hiring manager will review your product experience")
    assert reply_repo.correlate(msg, [pm, de]) == (None, "company", True)


def test_header_fetch_is_bounded():
    assert application_replies._HEADER_FETCH_CAP <= 500
    assert application_replies._HEADER_FETCH_DEADLINE_S <= 300


def test_unattached_reply_has_no_status(conn):
    row = _record(conn, application_id=None, needs_review=True)
    assert row["status_after"] is None
    assert row["needs_review"] == 1
    assert conn.execute("SELECT COUNT(*) FROM application_status_events").fetchone()[0] == 0


def test_status_events_are_append_only():
    forbidden = ("update", "delete", "remove", "drop", "purge")
    public = [n for n in dir(reply_repo) if callable(getattr(reply_repo, n)) and not n.startswith("_")]
    assert not [n for n in public if any(n.lower().startswith(f) for f in forbidden)]


def test_connect_creates_private_file(tmp_path):
    path = tmp_path / "sub" / "pipeline.db"
    reply_repo.connect(str(path)).close()
    assert (path.stat().st_mode & 0o777) == 0o600


# ── poller ─────────────────────────────────────────────────────────────────────

def _setup_poller(monkeypatch, tmp_path, messages, rows=None):
    db_path = str(tmp_path / "pipeline.db")
    monkeypatch.setattr(reply_repo, "PIPELINE_DB_PATH", db_path)
    monkeypatch.setattr(application_replies.config, "RECEIPT_IMAP_ADDRESS", "me@example.com")
    monkeypatch.setattr(application_replies.config, "RECEIPT_IMAP_APP_PASSWORD", "pw")
    rows = rows if rows is not None else [{
        "id": 1, "company": "Acme", "role": "Product Manager", "stage": "applied",
        "applied_date": "2026-10-01", "submit_attempted_at": None, "outcome_evidence": None}]
    monkeypatch.setattr(application_replies.db, "get_open_applications_for_outcomes", lambda days: rows)
    monkeypatch.setattr(application_replies.gmail, "fetch_inbox_since",
                        lambda since, address, password, want_body=None: messages)
    monkeypatch.setattr(application_replies, "_fetch_threading_headers", lambda *a, **k: {})
    return db_path


def _interview_msg(**over):
    msg = {"num": b"1", "message_id": "<m1@acme.com>", "from": "Acme Recruiting <hr@acme.com>",
           "subject": "Interview invitation", "date": NOW - timedelta(days=1), "is_reply": False,
           "body": "We would like to invite you to an interview. https://calendly.com/acme/x"}
    msg.update(over)
    return msg


def test_run_end_to_end(monkeypatch, tmp_path):
    db_path = _setup_poller(monkeypatch, tmp_path, [
        _interview_msg(),
        _interview_msg(message_id="<v@acme.com>", subject="Your code", body="Your verification code is 123456"),
        _interview_msg(message_id="<n@elsewhere.org>", **{"from": "Bob <bob@elsewhere.org>"},
                       subject="Lunch?", body="Want to grab lunch"),
    ])
    nudges = []
    counts = application_replies.run(now=NOW, notify=lambda c, r, k: nudges.append((c, r, k)))
    assert counts == {"checked": 3, "stored": 1, "needs_review": 0, "errors": 0}
    assert nudges == [("Acme", "Product Manager", "interview")]
    conn = reply_repo.connect(db_path)
    row = conn.execute("SELECT * FROM replies").fetchone()
    assert row["application_id"] == "1" and row["kind"] == "interview" and row["status_after"] == "interview"
    assert row["source"] == "imap"
    assert reply_repo.get_current_status(conn, "1") == "interview"
    conn.close()
    again = application_replies.run(now=NOW)
    assert again["stored"] == 0 and again["errors"] == 0


def test_run_ambiguous_goes_to_review_without_nudge(monkeypatch, tmp_path):
    rows = [
        {"id": 1, "company": "Acme", "role": "Product Manager", "stage": "applied",
         "applied_date": "2026-10-01", "submit_attempted_at": None, "outcome_evidence": None},
        {"id": 2, "company": "Acme", "role": "Data Engineer", "stage": "applied",
         "applied_date": "2026-10-02", "submit_attempted_at": None, "outcome_evidence": None},
    ]
    db_path = _setup_poller(monkeypatch, tmp_path, [_interview_msg()], rows=rows)
    nudges = []
    counts = application_replies.run(now=NOW, notify=lambda *a: nudges.append(a))
    assert counts["stored"] == 1 and counts["needs_review"] == 1 and nudges == []
    conn = reply_repo.connect(db_path)
    row = conn.execute("SELECT * FROM replies").fetchone()
    assert row["application_id"] is None and row["needs_review"] == 1
    conn.close()


def test_run_skips_without_mailbox_config(monkeypatch, tmp_path):
    _setup_poller(monkeypatch, tmp_path, [])
    monkeypatch.setattr(application_replies.config, "RECEIPT_IMAP_ADDRESS", None)
    assert application_replies.run(now=NOW) == {"checked": 0, "stored": 0, "needs_review": 0, "errors": 0}


def test_run_never_raises_on_scan_failure(monkeypatch, tmp_path):
    _setup_poller(monkeypatch, tmp_path, [])

    def boom(*a, **k):
        raise RuntimeError("imap down")

    monkeypatch.setattr(application_replies.gmail, "fetch_inbox_since", boom)
    assert application_replies.run(now=NOW)["errors"] == 1


def test_failing_notify_does_not_stop_storage(monkeypatch, tmp_path):
    _setup_poller(monkeypatch, tmp_path, [_interview_msg()])

    def bad(*a):
        raise RuntimeError("push down")

    counts = application_replies.run(now=NOW, notify=bad)
    assert counts["stored"] == 1 and counts["errors"] == 0


def test_outbox_failure_is_best_effort(monkeypatch, tmp_path):
    _setup_poller(monkeypatch, tmp_path, [_interview_msg()])

    def failing_enqueue(*a, **k):
        raise RuntimeError("outbox broken")

    monkeypatch.setitem(sys.modules, "outbox", types.SimpleNamespace(enqueue=failing_enqueue))
    counts = application_replies.run(now=NOW)
    assert counts["stored"] == 1 and counts["errors"] == 0


def test_outbox_receives_payload(monkeypatch, tmp_path):
    _setup_poller(monkeypatch, tmp_path, [_interview_msg()])
    calls = []
    monkeypatch.setitem(sys.modules, "outbox", types.SimpleNamespace(
        enqueue=lambda conn, kind, key, op, payload, version=1: calls.append((kind, key, op, payload, version))))
    application_replies.run(now=NOW)
    assert len(calls) == 1
    kind, key, op, payload, version = calls[0]
    assert (kind, key, op, version) == ("reply", "<m1@acme.com>", "upsert", 1)
    assert payload["kind"] == "interview" and "snippet" not in payload
