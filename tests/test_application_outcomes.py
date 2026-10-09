"""application_outcomes: rejections and interview invites read from the receipt inbox move a
submitted application's stage, with the email kept as evidence. Precision over recall: anything
uncertain changes nothing."""

from datetime import datetime, timedelta, timezone

import pytest

import application_outcomes as ao
import config

NOW = datetime(2026, 10, 20, 15, tzinfo=timezone.utc)
APPLIED = "2026-10-10"


def _msg(subject, body, sender="Acme Recruiting <no-reply@acme.com>", days_ago=1, mid=None, is_reply=False):
    return {"message_id": mid or f"<{abs(hash((subject, body, sender)))}@x>", "from": sender, "subject": subject,
            "body": body, "date": NOW - timedelta(days=days_ago), "is_reply": is_reply}


def _app(aid=1, company="Acme", role="Associate Product Manager", stage="applied", **over):
    return dict({"id": aid, "company": company, "role": role, "stage": stage, "applied_date": APPLIED,
                 "submit_attempted_at": None, "outcome_evidence": None}, **over)


# ── classify ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Unfortunately, we have decided not to move forward with your application at this time.",
    "After careful consideration, we have decided to pursue other candidates whose experience more closely matches.",
    "We regret to inform you that you were not selected for the Associate Product Manager role.",
    "We will not be moving forward with your candidacy.",
    "The position has been filled. We wish you the best in your search.",
    "Your application is no longer under consideration.",
    "We've decided to move forward with other candidates for this role.",
])
def test_rejections(text):
    assert ao.classify(text) == "rejection"


@pytest.mark.parametrize("text", [
    "We'd like to invite you to a phone screen with our hiring manager.",
    "Please share your availability for a 30 minute interview next week.",
    "Next step in our process is an initial interview. Book a time here: https://calendly.com/acme/apm",
    "We would like to schedule a call to discuss the Associate Product Manager role.",
    "Pick a slot for your first-round interview: https://app.goodtime.io/x",
])
def test_interview_invites(text):
    assert ao.classify(text) == "interview"


@pytest.mark.parametrize("text", [
    "Thank you for applying to Acme! We received your application and will review it.",
    "Thank you for your interest in Acme.",
    "5 interview tips for product managers",
    "Unfortunately the link above expired; here is a new one to view your application status.",
    "",
])
def test_everything_else_is_left_alone(text):
    assert ao.classify(text) is None


@pytest.mark.parametrize("text", [
    "Thank you for applying! If your qualifications match, we will reach out to schedule an interview.",
    "We have received your application. If selected, we'll contact you to schedule a call.",
])
def test_a_receipt_promising_a_future_interview_is_not_an_invite(text):
    assert ao.classify(text) is None


def test_a_receipt_with_a_booking_link_is_an_invite():
    assert ao.classify("Thank you for applying! Book your interview: https://calendly.com/acme/apm") == "interview"


def test_a_rejection_that_mentions_interviews_is_a_rejection():
    text = "Thank you for interviewing with us. Unfortunately, we have decided not to move forward."
    assert ao.classify(text) == "rejection"


# ── matching ─────────────────────────────────────────────────────────────────

def _decide(apps, messages):
    return ao.decide(apps, messages, now=NOW)


def test_a_rejection_moves_an_applied_row_to_rejected():
    msg = _msg("Your application to Acme", "Unfortunately, we have decided not to move forward with your application.")
    [(app, kind, evidence)] = _decide([_app()], [msg])
    assert (app["id"], kind) == (1, "rejection")
    assert evidence == {"source": "gmail_outcome", "kind": "rejection", "message_id": msg["message_id"],
                        "from": msg["from"], "subject": msg["subject"], "date": msg["date"].isoformat(),
                        "previous_stage": "applied"}


@pytest.mark.parametrize("stage,expected", [("applied", "interview"), ("phone_screen", None), ("onsite", None)])
def test_interview_invites_only_move_applied_rows(stage, expected):
    msg = _msg("Next steps", "We'd like to invite you to a phone screen for the Associate Product Manager role.")
    decisions = _decide([_app(stage=stage)], [msg])
    assert [d[1] for d in decisions] == ([expected] if expected else [])


@pytest.mark.parametrize("stage", ["applied", "phone_screen", "onsite"])
def test_rejections_apply_at_any_open_stage(stage):
    msg = _msg("Update", "We regret to inform you that you were not selected.")
    assert [d[1] for d in _decide([_app(stage=stage)], [msg])] == ["rejection"]


@pytest.mark.parametrize("sender", [
    "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
    "Indeed <alert@indeed.com>",
    "Glassdoor <noreply@glassdoor.com>",
    "JobRight <hello@jobright.ai>",
])
def test_job_board_mail_never_counts(sender):
    msg = _msg("Acme: we decided to move forward with other candidates", "Unfortunately... other candidates", sender=sender)
    assert _decide([_app()], [msg]) == []


def test_mail_from_another_company_never_counts():
    msg = _msg("Update", "We regret to inform you that you were not selected.", sender="Beta <talent@beta.io>")
    assert _decide([_app()], [msg]) == []


def test_mail_from_the_company_ats_counts_when_it_names_the_company():
    msg = _msg("Acme | Associate Product Manager", "We regret to inform you that you were not selected.",
               sender="Acme Hiring Team <no-reply@us.greenhouse-mail.io>")
    assert [d[1] for d in _decide([_app()], [msg])] == ["rejection"]


def test_mail_before_the_application_never_counts():
    msg = _msg("Update", "We regret to inform you that you were not selected.", days_ago=30)
    assert _decide([_app()], [msg]) == []


def test_replies_in_human_threads_are_skipped():
    msg = _msg("Re: coffee chat", "Unfortunately I can't make Tuesday; can we schedule a call instead?", is_reply=True)
    assert _decide([_app()], [msg]) == []


def test_two_open_applications_at_one_company_need_the_role_named():
    apps = [_app(1, role="Associate Product Manager"), _app(2, role="Product Analyst, Growth")]
    vague = _msg("Update", "We regret to inform you that you were not selected.")
    assert _decide(apps, [vague]) == []
    named = _msg("Update on Product Analyst, Growth", "We regret to inform you that you were not selected.", mid="<b@x>")
    assert [(d[0]["id"], d[1]) for d in _decide(apps, [named])] == [(2, "rejection")]


def test_a_message_already_acted_on_is_not_used_again():
    msg = _msg("Update", "We regret to inform you that you were not selected.", mid="<seen@x>")
    app = _app(outcome_evidence={"message_id": "<seen@x>", "kind": "rejection"})
    assert _decide([app], [msg]) == []


def test_the_latest_outcome_wins_for_one_row():
    invite = _msg("Next steps", "Please share your availability for an interview.", days_ago=3, mid="<i@x>")
    reject = _msg("Update", "We have decided to move forward with other candidates.", days_ago=1, mid="<r@x>")
    [(app, kind, evidence)] = _decide([_app()], [invite, reject])
    assert kind == "rejection" and evidence["message_id"] == "<r@x>"


def test_submit_attempted_at_bounds_the_window_when_present():
    msg = _msg("Update", "We regret to inform you that you were not selected.", days_ago=5)
    app = _app(submit_attempted_at=(NOW - timedelta(days=2)).isoformat())
    assert _decide([app], [msg]) == []


# ── run ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def mailbox(mocker):
    mocker.patch.object(config, "RECEIPT_IMAP_ADDRESS", "me@example.com")
    mocker.patch.object(config, "RECEIPT_IMAP_APP_PASSWORD", "pw")
    mocker.patch.object(ao.db, "log_agent_event")
    return mocker


def test_run_records_each_outcome_with_the_allowed_source_stages(mailbox):
    apps = [_app(1), _app(2, company="Beta", role="Product Analyst")]
    mailbox.patch.object(ao.db, "get_open_applications_for_outcomes", return_value=apps)
    fetch = mailbox.patch.object(ao.gmail, "fetch_inbox_since", return_value=[
        _msg("Update", "We regret to inform you that you were not selected."),
        _msg("Beta interview", "We'd like to schedule a call to discuss the Product Analyst role.",
             sender="Beta Talent <talent@beta.com>"),
    ])
    record = mailbox.patch.object(ao.db, "record_application_outcome", return_value={"id": 1})
    counts = ao.run(now=NOW)
    assert counts == {"checked": 2, "rejected": 1, "interviews": 1, "errors": 0}
    events = [c.kwargs["metadata"]["kind"] for c in ao.db.log_agent_event.call_args_list]
    assert sorted(events) == ["interview", "rejection"]
    assert all(c.kwargs["status"] == "success" for c in ao.db.log_agent_event.call_args_list)
    calls = {c.args[0]: c.args[1:3] for c in record.call_args_list}
    assert calls[1] == ("rejected", ("applied", "phone_screen", "onsite"))
    assert calls[2] == ("phone_screen", ("applied",))
    since = fetch.call_args.args[0]
    assert since == (NOW - timedelta(days=config.APPLY_OUTCOME_LOOKBACK_DAYS)).date()


def test_run_only_fetches_bodies_for_mail_from_tracked_companies(mailbox):
    mailbox.patch.object(ao.db, "get_open_applications_for_outcomes", return_value=[_app()])
    fetch = mailbox.patch.object(ao.gmail, "fetch_inbox_since", return_value=[])
    ao.run(now=NOW)
    want_body = fetch.call_args.kwargs["want_body"]
    assert want_body({"from": "Acme <a@acme.com>", "subject": "x", "is_reply": False})
    assert not want_body({"from": "Other <a@other.com>", "subject": "x", "is_reply": False})
    assert not want_body({"from": "LinkedIn <a@linkedin.com>", "subject": "Acme is hiring", "is_reply": False})


def test_run_without_a_mailbox_does_nothing(mocker):
    mocker.patch.object(config, "RECEIPT_IMAP_ADDRESS", None)
    get = mocker.patch.object(ao.db, "get_open_applications_for_outcomes")
    assert ao.run(now=NOW)["checked"] == 0
    get.assert_not_called()


def test_run_with_nothing_open_never_opens_the_mailbox(mailbox):
    mailbox.patch.object(ao.db, "get_open_applications_for_outcomes", return_value=[])
    fetch = mailbox.patch.object(ao.gmail, "fetch_inbox_since")
    ao.run(now=NOW)
    fetch.assert_not_called()


@pytest.mark.parametrize("failure", ["db", "imap", "record"])
def test_run_never_raises(mailbox, failure):
    get = mailbox.patch.object(ao.db, "get_open_applications_for_outcomes", return_value=[_app()])
    fetch = mailbox.patch.object(ao.gmail, "fetch_inbox_since", return_value=[
        _msg("Update", "We regret to inform you that you were not selected.")])
    record = mailbox.patch.object(ao.db, "record_application_outcome", return_value={"id": 1})
    {"db": get, "imap": fetch, "record": record}[failure].side_effect = RuntimeError("down")
    counts = ao.run(now=NOW)
    assert counts["errors"] >= 1


def test_a_lost_race_is_not_counted(mailbox):
    mailbox.patch.object(ao.db, "get_open_applications_for_outcomes", return_value=[_app()])
    mailbox.patch.object(ao.gmail, "fetch_inbox_since", return_value=[
        _msg("Update", "We regret to inform you that you were not selected.")])
    mailbox.patch.object(ao.db, "record_application_outcome", return_value=None)
    assert ao.run(now=NOW)["rejected"] == 0
