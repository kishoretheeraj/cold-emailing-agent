"""Reply drafts carry the application (spec 2026-10-08-warm-paths-design §3.7): referrals come from
the people who reply, so a positive reply from a linked contact gets one concrete ask: a referral
before submission, a word to the hiring manager after it, and nothing once the application closed.
Unlinked contacts get the exact prompt they got before."""

import pytest

import reply_drafter
from gmail import DraftResult


def _contact(**over):
    return dict({"id": 1, "name": "Alice Smith", "email": "alice@acme.com", "company": "Acme", "role": "Director",
                 "classifier_status": "positive_reply", "stage": "networking_sent", "message_id": "<m@x>",
                 "original_subject": "Hello"}, **over)


def _state(stage, applied_date=None):
    return {7: {"id": 7, "stage": stage, "role": "Associate Product Manager", "company": "Acme",
                "applied_date": applied_date, "posting_description": "", "job_url": "https://jobs.acme/1"}}


@pytest.fixture
def wired(mocker):
    model = mocker.patch.object(reply_drafter, "_call_claude", return_value="Hi Alice, thanks.")
    mocker.patch("preflight.check", return_value=[])
    mocker.patch("reply_drafter.create_draft", return_value=DraftResult("<new@x>", None, None))
    for name in ("apply_label_to_latest_draft", "update_contact", "insert_email_message", "log_agent_event",
                 "log_drafted_email"):
        mocker.patch(f"reply_drafter.{name}")
    return model


def _prompt(model, call=0):
    return model.call_args_list[call].args[0]


def test_unlinked_contacts_get_the_same_prompt_as_before(wired, mocker):
    lookup = mocker.patch.object(reply_drafter, "get_application_states")
    reply_drafter.draft_reply(_contact(), "Happy to chat", {})
    lookup.assert_not_called()
    assert "APPLICATION CONTEXT" not in _prompt(wired)


@pytest.mark.parametrize("stage,ask", [
    ("ready_to_submit", "referring"),
    ("saved", "referring"),
    ("applied", "hiring manager"),
    ("phone_screen", "hiring manager"),
])
def test_a_linked_contact_gets_one_concrete_ask(wired, mocker, stage, ask):
    mocker.patch.object(reply_drafter, "get_application_states", return_value=_state(stage, "2026-10-08"))
    reply_drafter.draft_reply(_contact(job_application_id=7), "Happy to chat", {})
    prompt = _prompt(wired)
    assert "APPLICATION CONTEXT" in prompt
    assert "Associate Product Manager at Acme" in prompt and "https://jobs.acme/1" in prompt
    assert ask in prompt
    assert "—" not in prompt.split("APPLICATION CONTEXT")[1]


@pytest.mark.parametrize("stage", ["rejected", "withdrawn"])
def test_a_closed_application_makes_no_ask(wired, mocker, stage):
    mocker.patch.object(reply_drafter, "get_application_states", return_value=_state(stage))
    reply_drafter.draft_reply(_contact(job_application_id=7), "Happy to chat", {})
    block = _prompt(wired).split("APPLICATION CONTEXT")[1]
    assert "Do not mention the application" in block
    assert "referring" not in block and "hiring manager" not in block


@pytest.mark.parametrize("states", [RuntimeError("down"), {}])
def test_an_unreadable_application_only_drops_the_block(wired, mocker, states):
    kwargs = {"side_effect": states} if isinstance(states, Exception) else {"return_value": states}
    mocker.patch.object(reply_drafter, "get_application_states", **kwargs)
    reply_drafter.draft_reply(_contact(job_application_id=7), "Happy to chat", {})
    assert "APPLICATION CONTEXT" not in _prompt(wired)
    reply_drafter.create_draft.assert_called_once()


def test_the_preflight_retry_keeps_the_ask(wired, mocker):
    mocker.patch.object(reply_drafter, "get_application_states", return_value=_state("ready_to_submit"))
    mocker.patch("preflight.check", side_effect=[["first_name_missing: 'Alice'"], []])
    reply_drafter.draft_reply(_contact(job_application_id=7), "Happy to chat", {})
    assert wired.call_count == 2
    retry = _prompt(wired, 1)
    assert "APPLICATION CONTEXT" in retry and "referring" in retry
    assert retry.index("APPLICATION CONTEXT") < retry.index("REVISION INSTRUCTION")
