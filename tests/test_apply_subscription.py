"""Apply-side model calls on the Claude subscription (plan Phase C). claude_subscription is mocked;
no subprocess, browser or database is ever reached."""

from unittest.mock import MagicMock

import pytest

import apply_agent
import claude_subscription
import config


def _field(label, kind="textarea", required=True, filled=False, options=None):
    return {"key": f"#{label[:8]}", "selector": f"#{label[:8]}", "label": label, "kind": kind,
            "required": required, "filled": filled, "options": options or [], "option_selectors": []}


@pytest.fixture(autouse=True)
def _defaults(mocker):
    mocker.patch("apply_agent._form_inventory", return_value=[_field("Why do you want this role?")])
    mocker.patch("apply_agent.candidate_profile.profile_text", return_value="Associate PM at Protium.")
    mocker.patch("apply_agent.db.load_prompts", return_value={})
    mocker.patch("apply_agent.usage_tracking.log_usage")


def test_subscription_backend_uses_claude_cli_and_logs_zero_cost_usage(mocker):
    mocker.patch.object(config, "APPLY_CLAUDE_BACKEND", "subscription")
    complete = mocker.patch("apply_agent.claude_subscription.complete",
                            return_value=("  Grounded answer.  ", {"input_tokens": 120, "output_tokens": 30}))
    api = mocker.patch("apply_agent._call_claude")

    result = apply_agent._generate_screening_answers(MagicMock(), {"id": 7, "company": "Acme"})

    assert result == {"Why do you want this role?": "Grounded answer."}
    api.assert_not_called()
    prompt = complete.call_args[0][0]
    assert "Why do you want this role?" in prompt and "Associate PM at Protium." in prompt
    assert complete.call_args.kwargs["model"] == config.APPLY_MODEL
    apply_agent.usage_tracking.log_usage.assert_called_once_with(
        "apply_agent", "screening_question", config.APPLY_MODEL,
        {"input_tokens": 120, "output_tokens": 30}, job_application_id=7, billing="subscription")


def test_api_backend_keeps_the_existing_call(mocker):
    mocker.patch.object(config, "APPLY_CLAUDE_BACKEND", "api")
    complete = mocker.patch("apply_agent.claude_subscription.complete")
    api = mocker.patch("apply_agent._call_claude", return_value="Answer.")

    assert apply_agent._generate_screening_answers(MagicMock(), {"id": 7}) == {"Why do you want this role?": "Answer."}
    complete.assert_not_called()
    assert api.call_args.kwargs["module"] == "apply_agent"


def test_unknown_backend_fails_closed(mocker):
    mocker.patch.object(config, "APPLY_CLAUDE_BACKEND", "apikey")
    with pytest.raises(ValueError, match="APPLY_CLAUDE_BACKEND"):
        apply_agent._generate_screening_answers(MagicMock(), {"id": 7})


@pytest.mark.parametrize("exc", [
    claude_subscription.ClaudeUsageLimitError("usage limit reached, try again at 5pm"),
    claude_subscription.ClaudeSubscriptionError("CLAUDE_CODE_OAUTH_TOKEN is not set"),
])
def test_subscription_failures_propagate_instead_of_skipping_the_question(mocker, exc):
    # A skipped question would surface later as a misleading "required question blank".
    mocker.patch.object(config, "APPLY_CLAUDE_BACKEND", "subscription")
    mocker.patch("apply_agent.claude_subscription.complete", side_effect=exc)
    with pytest.raises(type(exc)):
        apply_agent._generate_screening_answers(MagicMock(), {"id": 7})


def test_ordinary_model_errors_still_skip_one_question(mocker):
    mocker.patch.object(config, "APPLY_CLAUDE_BACKEND", "api")
    mocker.patch("apply_agent._call_claude", side_effect=RuntimeError("bad gateway"))
    assert apply_agent._generate_screening_answers(MagicMock(), {"id": 7}) == {}


def _job(i):
    return {"id": i, "company": f"Co{i}", "role": "PM", "job_url": f"https://boards.greenhouse.io/co{i}/jobs/{i}",
            "resume_file_ref": "r.pdf", "cover_letter_file_ref": "c.pdf"}


@pytest.mark.parametrize("exc", [
    claude_subscription.ClaudeUsageLimitError("usage limit"),
    claude_subscription.ClaudeSubscriptionError("claude CLI failed"),
])
def test_run_preview_stops_on_a_subscription_failure(mocker, exc, caplog):
    mocker.patch("apply_agent.db.recover_stale_leases", return_value=0)
    mocker.patch("apply_agent.db.get_preview_candidates", return_value=[_job(1), _job(2)])
    process = mocker.patch("apply_agent._process_one_preview", side_effect=[exc, "filled"])

    apply_agent.run_preview()

    assert process.call_count == 1
    assert any("stopping" in r.message for r in caplog.records)


def test_preview_releases_a_usage_limited_row_as_retryable_with_a_clear_reason(mocker):
    mocker.patch("apply_agent.db.claim_application", return_value="lease-1")
    mocker.patch("apply_agent.db.heartbeat_application")
    release = mocker.patch("apply_agent.db.release_application", return_value=True)
    mocker.patch("apply_agent.ats_platform.classify", return_value="greenhouse")
    page = MagicMock()
    mocker.patch("apply_agent._launch_page", return_value=page)
    mocker.patch("apply_agent._close_page")
    mocker.patch("apply_agent._form_signature", return_value="sig")
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse",
                 return_value={"first_name": True, "last_name": True, "email": True})
    mocker.patch("apply_agent._attach_resume_and_cover_letter", return_value={"resume": True, "cover_letter": True})
    mocker.patch("apply_agent._fill_eligibility_answers")
    mocker.patch("apply_agent._generate_screening_answers",
                 side_effect=claude_subscription.ClaudeUsageLimitError("usage limit"))

    with pytest.raises(claude_subscription.ClaudeUsageLimitError):
        apply_agent._process_one_preview(_job(1))

    args = release.call_args[0]
    assert args[:3] == (1, "lease-1", "failed_retryable")
    assert "usage limit" in args[3].lower()
