"""Warm paths (spec 2026-10-08-warm-paths-design §3.2): contacts linked to a job application.

I1: an unlinked contact decides exactly as before.
I2: an applied-mode linked contact is never drafted before the application is submitted.
I3: an applied-mode linked contact is never drafted into a rejected or withdrawn application.
I4: a failed or missing application lookup skips linked applied-mode contacts; nothing else.
"""

import itertools
from datetime import date

import pytest

import agent

TODAY = date(2026, 10, 9)
DUE = "2026-10-01"
LATER = "2026-10-20"

_MODES = ["outreach", "applied", "networking"]
_STAGES = sorted({s for stages in (agent.NEXT_STAGE.values(), agent.DRAFTED_TO_SENT.values()) for s in stages}
                 | {"new", "closed"})
_REPLIES = ["no_reply", "replied", "interested", "call_scheduled", "dead"]
_FOLLOWUPS = [None, DUE, LATER]


def _contact(mode, stage, reply="no_reply", followup=None, **extra):
    return dict({"id": 1, "name": "Jane Doe", "company": "Acme", "mode": mode, "stage": stage,
                 "reply_status": reply, "followup_date": followup}, **extra)


# ── I1: unlinked contacts are untouched ────────────────────────────────────────

@pytest.mark.parametrize("mode,stage,reply,followup", list(itertools.product(_MODES, _STAGES, _REPLIES, _FOLLOWUPS)))
def test_unlinked_contacts_decide_exactly_as_before(mode, stage, reply, followup):
    plain = _contact(mode, stage, reply, followup)
    expected = agent.decide_action(plain, TODAY)
    for linked_none in ({"job_application_id": None}, {"job_application_id": None, "relationship": None},
                        {"job_application_id": None, "_application": None, "_application_error": True}):
        assert agent.decide_action(_contact(mode, stage, reply, followup, **linked_none), TODAY) == expected


# ── I2 / I3: the application's stage gates applied-mode drafts ─────────────────

_SUBMITTED = ["applied", "phone_screen", "onsite", "offer", "accepted"]
_NOT_YET = ["saved", "ready_to_submit"]
_CLOSED = ["rejected", "withdrawn"]


def _linked(mode, stage, app_stage, followup=None):
    return _contact(mode, stage, followup=followup, job_application_id=7, relationship="hiring_manager",
                    _application={"id": 7, "stage": app_stage, "role": "APM", "company": "Acme",
                                  "applied_date": "2026-10-08"})


@pytest.mark.parametrize("app_stage", _NOT_YET)
def test_no_applied_intro_before_the_application_is_submitted(app_stage):
    contact = _linked("applied", "new", app_stage)
    assert agent.decide_action(contact, TODAY) == "skip"
    assert agent._skip_reason(contact, TODAY) == "application not submitted yet"


@pytest.mark.parametrize("app_stage", _SUBMITTED)
def test_applied_intro_once_submitted(app_stage):
    assert agent.decide_action(_linked("applied", "new", app_stage), TODAY) == "send_applied_intro"


@pytest.mark.parametrize("app_stage", _SUBMITTED)
def test_applied_followup_once_submitted(app_stage):
    contact = _linked("applied", "applied_intro_sent", app_stage, followup=DUE)
    assert agent.decide_action(contact, TODAY) == "send_applied_followup"


@pytest.mark.parametrize("stage,followup", [("new", None), ("applied_intro_sent", DUE)])
@pytest.mark.parametrize("app_stage", _CLOSED)
def test_nothing_is_drafted_into_a_closed_application(stage, followup, app_stage):
    contact = _linked("applied", stage, app_stage, followup=followup)
    assert agent.decide_action(contact, TODAY) == "skip"
    assert agent._skip_reason(contact, TODAY) == "application closed"


@pytest.mark.parametrize("app_stage", _NOT_YET + _SUBMITTED + _CLOSED)
@pytest.mark.parametrize("stage,followup,expected", [
    ("new", None, "send_networking_first_touch"),
    ("networking_sent", DUE, "send_networking_followup"),
])
def test_networking_contacts_ignore_the_application_stage(app_stage, stage, followup, expected):
    contact = _linked("networking", stage, app_stage, followup=followup)
    contact["relationship"] = "alum"
    assert agent.decide_action(contact, TODAY) == expected


def test_a_reply_still_wins_over_a_submitted_application():
    contact = _linked("applied", "applied_intro_sent", "applied", followup=DUE)
    contact["reply_status"] = "interested"
    assert agent.decide_action(contact, TODAY) == "skip"


# ── I4: fail closed on the link ────────────────────────────────────────────────

def test_a_failed_lookup_skips_linked_applied_contacts():
    contact = _contact("applied", "new", job_application_id=7, _application_error=True)
    assert agent.decide_action(contact, TODAY) == "skip"
    assert agent._skip_reason(contact, TODAY) == "linked application unreadable"


def test_a_missing_application_skips_linked_applied_contacts():
    contact = _contact("applied", "new", job_application_id=7, _application=None)
    assert agent.decide_action(contact, TODAY) == "skip"
    assert agent._skip_reason(contact, TODAY) == "linked application unreadable"


@pytest.mark.parametrize("mode,expected", [("outreach", "send_first_touch"),
                                           ("networking", "send_networking_first_touch")])
def test_a_failed_lookup_never_blocks_other_modes(mode, expected):
    contact = _contact(mode, "new", job_application_id=7, _application_error=True)
    assert agent.decide_action(contact, TODAY) == expected


# ── Attaching applications in run() ───────────────────────────────────────────

def test_attach_applications_marks_each_linked_contact(mocker):
    lookup = mocker.patch.object(agent, "get_application_states", return_value={
        7: {"id": 7, "stage": "applied", "role": "APM", "company": "Acme", "applied_date": "2026-10-08",
            "posting_description": "Own the roadmap."}})
    contacts = [_contact("applied", "new", job_application_id=7), _contact("applied", "new", job_application_id=8),
                _contact("outreach", "new")]
    agent._attach_applications(contacts)
    assert lookup.call_args.args[0] == [7, 8]
    assert contacts[0]["_application"]["stage"] == "applied"
    assert contacts[1]["_application"] is None
    assert "_application" not in contacts[2]


def test_attach_applications_fails_closed(mocker):
    mocker.patch.object(agent, "get_application_states", side_effect=RuntimeError("supabase down"))
    contacts = [_contact("applied", "new", job_application_id=7), _contact("outreach", "new")]
    agent._attach_applications(contacts)
    assert contacts[0]["_application_error"] is True
    assert "_application_error" not in contacts[1]


def test_attach_applications_skips_the_lookup_when_nothing_is_linked(mocker):
    lookup = mocker.patch.object(agent, "get_application_states")
    agent._attach_applications([_contact("outreach", "new")])
    lookup.assert_not_called()


# ── Prompt fields come from the application when the contact has none ─────────

def test_prompt_fields_are_filled_from_the_application():
    contact = _linked("applied", "new", "applied")
    contact["_application"]["posting_description"] = "x" * 3000
    filled = agent._with_application_fields(contact)
    assert filled["job_title"] == "APM"
    assert "company_applied" not in filled
    assert filled["applied_date"] == "2026-10-08"
    assert filled["job_description"] == "x" * agent.WARM_JOB_DESCRIPTION_CHARS
    assert "job_title" not in contact            # the stored contact is not changed


def test_the_contacts_own_fields_win():
    contact = _linked("applied", "new", "applied")
    contact.update(job_title="Product Manager, Lending", applied_date="2026-10-07", job_description="Own it.")
    filled = agent._with_application_fields(contact)
    assert filled["job_title"] == "Product Manager, Lending"
    assert filled["applied_date"] == "2026-10-07"
    assert filled["job_description"] == "Own it."


def test_unlinked_contacts_come_back_unchanged():
    contact = _contact("applied", "new")
    assert agent._with_application_fields(contact) is contact


@pytest.mark.parametrize("mode", ["outreach", "networking"])
def test_other_modes_are_never_filled(mode):
    contact = _linked(mode, "new", "applied")
    assert agent._with_application_fields(contact) is contact


@pytest.mark.parametrize("mode,action", [("outreach", "send_first_touch"), ("networking", "send_networking_first_touch")])
def test_linked_outreach_and_networking_prompts_are_byte_identical(mocker, mode, action):
    import emailer
    mocker.patch.object(emailer, "RESEARCH_TIERS", set())
    plain = _contact(mode, "new", email="j@acme.example", tier=3, role="Director of Product")
    linked = dict(plain, job_application_id=7, relationship="alum",
                  _application={"id": 7, "stage": "applied", "role": "APM", "company": "Acme",
                                "applied_date": "2026-10-08", "posting_description": "Own the roadmap."})
    a = emailer.prepare_email(agent._with_application_fields(plain), action, prompts={})[:2]
    b = emailer.prepare_email(agent._with_application_fields(linked), action, prompts={})[:2]
    assert a == b
