"""apply_agent behaviour the Beelink worker relies on (first-ten-applications Phase D): the preview
queue, browser launch settings, the no-generic-adapter gate, and the takeover points. Browser,
database and takeover are mocked."""

import sys
import types
from unittest.mock import MagicMock

import pytest

import apply_agent
import config
import takeover

_OK_FIELDS = {"first_name": True, "last_name": True, "email": True}
# Captured before the autouse fixture below replaces it, for the test of the real function.
_REAL_FILL_SCREENING = apply_agent._fill_screening_questions
_OK_ATTACH = {"resume": True, "cover_letter": True}


@pytest.fixture(autouse=True)
def _defaults(mocker):
    mocker.patch("apply_agent.db.recover_stale_leases", return_value=0)
    mocker.patch("apply_agent.db.claim_application", return_value="lease-1")
    mocker.patch("apply_agent.db.heartbeat_application")
    mocker.patch("apply_agent.db.renew_submission_lease", return_value=True)
    mocker.patch("apply_agent.db.release_application", return_value=True)
    mocker.patch("apply_agent.db.mark_unsupported")
    mocker.patch("apply_agent.db.complete_preview", return_value=True)
    mocker.patch("apply_agent.db.record_submission", return_value=True)
    mocker.patch("apply_agent.db.load_prompts", return_value={})
    mocker.patch("apply_agent._form_inventory", return_value=[])
    mocker.patch("apply_agent._form_signature", return_value="sig")
    mocker.patch("apply_agent._close_page")
    mocker.patch("apply_agent._generate_screening_answers", return_value={})
    mocker.patch("apply_agent._fill_screening_questions", return_value={})
    mocker.patch("apply_agent._fill_eligibility_answers")
    mocker.patch("apply_agent._attach_resume_and_cover_letter", return_value=dict(_OK_ATTACH))
    mocker.patch("apply_agent.ats_fillers.fill_greenhouse", return_value=dict(_OK_FIELDS))
    mocker.patch("apply_agent.approval_signature.key_configured", return_value=True)
    mocker.patch("apply_agent.approval_signature.verify", return_value=None)
    mocker.patch.object(config, "APPLY_TAKEOVER_ENABLED", True)
    mocker.patch.object(apply_agent.takeover, "challenge_present", return_value=False)
    mocker.patch.object(apply_agent.takeover, "await_human", return_value=True)


def _job(i=1, url="https://boards.greenhouse.io/acme/jobs/1", **kw):
    row = {"id": i, "company": "Acme", "role": "PM", "job_url": url, "stage": "saved",
           "automation_status": "idle", "resume_file_ref": "r.pdf", "cover_letter_file_ref": "c.pdf"}
    row.update(kw)
    return row


# ── preview queue ──────────────────────────────────────────────────────────────

def test_preview_candidates_need_both_documents_and_a_claimable_status(mocker):
    mocker.patch("apply_agent.db.get_job_applications", return_value=[
        _job(1), _job(2, resume_file_ref=None), _job(3, cover_letter_file_ref=None),
        _job(4, automation_status="failed_retryable"), _job(5, automation_status="needs_input"),
        _job(6, automation_status=None)])
    assert [j["id"] for j in apply_agent.preview_candidates()] == [1, 4, 6]
    apply_agent.db.get_job_applications.assert_called_once_with(stage="saved")


# ── browser launch ─────────────────────────────────────────────────────────────

@pytest.fixture
def fake_playwright(mocker):
    pw = MagicMock()
    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = lambda: MagicMock(start=lambda: pw)
    mocker.patch.dict(sys.modules, {"playwright": types.ModuleType("playwright"), "playwright.sync_api": module})
    mocker.patch("apply_agent._free_local_port", return_value=54321)
    return pw


def test_launch_defaults_to_headless_bundled_chromium(mocker, fake_playwright):
    mocker.patch.object(config, "APPLY_BROWSER_HEADLESS", True)
    mocker.patch.object(config, "APPLY_BROWSER_CHANNEL", None)
    page = apply_agent._launch_page("https://x/apply")
    kwargs = fake_playwright.chromium.launch.call_args.kwargs
    assert kwargs["headless"] is True and kwargs["channel"] is None
    assert page is fake_playwright.chromium.launch.return_value.new_page.return_value


def test_launch_on_the_beelink_is_headful_real_chrome(mocker, fake_playwright):
    mocker.patch.object(config, "APPLY_BROWSER_HEADLESS", False)
    mocker.patch.object(config, "APPLY_BROWSER_CHANNEL", "chrome")
    apply_agent._launch_page("https://x/apply")
    kwargs = fake_playwright.chromium.launch.call_args.kwargs
    assert kwargs["headless"] is False and kwargs["channel"] == "chrome"


# ── no generic adapter on this host ────────────────────────────────────────────

def test_preview_leaves_generic_rows_untouched_when_no_adapter_is_configured(mocker):
    mocker.patch.object(config, "APPLY_GENERIC_ADAPTER", "none")
    launch = mocker.patch("apply_agent._launch_page")
    assert apply_agent._process_one_preview(_job(url="https://careers.acme.example/apply/1")) == "skipped"
    apply_agent.db.claim_application.assert_not_called()
    apply_agent.db.mark_unsupported.assert_not_called()
    launch.assert_not_called()


def test_submit_refuses_a_generic_row_before_claiming_when_no_adapter_is_configured(mocker):
    mocker.patch.object(config, "APPLY_GENERIC_ADAPTER", "none")
    mocker.patch("apply_agent.db.get_job_application",
                 return_value=_job(url="https://careers.acme.example/apply/1"))
    with pytest.raises(ValueError, match="no adapter"):
        apply_agent.submit(1)
    apply_agent.db.claim_application.assert_not_called()


# ── takeover in the preview pass ───────────────────────────────────────────────

def test_preview_waits_for_a_human_on_a_challenge_then_continues(mocker):
    mocker.patch("apply_agent._launch_page", return_value=MagicMock())
    apply_agent.takeover.challenge_present.side_effect = [True, False]
    assert apply_agent._process_one_preview(_job()) == "filled"
    apply_agent.takeover.await_human.assert_called_once()
    args = apply_agent.takeover.await_human.call_args[0]
    assert args[:3] == (1, "lease-1", "captcha")
    apply_agent.db.complete_preview.assert_called_once()


def test_preview_challenge_left_unsolved_goes_to_needs_input(mocker):
    mocker.patch("apply_agent._launch_page", return_value=MagicMock())
    apply_agent.takeover.challenge_present.return_value = True
    apply_agent.takeover.await_human.return_value = False
    with pytest.raises(apply_agent.TakeoverTimeout):
        apply_agent._process_one_preview(_job())
    args = apply_agent.db.release_application.call_args[0]
    assert args[:3] == (1, "lease-1", "needs_input")
    assert "CAPTCHA" in args[3]
    apply_agent.db.complete_preview.assert_not_called()


def test_preview_lost_lease_during_takeover_releases_nothing(mocker):
    mocker.patch("apply_agent._launch_page", return_value=MagicMock())
    apply_agent.takeover.challenge_present.return_value = True
    apply_agent.takeover.await_human.side_effect = takeover.TakeoverLost("gone")
    with pytest.raises(takeover.TakeoverLost):
        apply_agent._process_one_preview(_job())
    apply_agent.db.release_application.assert_not_called()


def test_no_takeover_where_it_is_disabled(mocker):
    mocker.patch.object(config, "APPLY_TAKEOVER_ENABLED", False)
    mocker.patch("apply_agent._launch_page", return_value=MagicMock())
    apply_agent.takeover.challenge_present.return_value = True
    assert apply_agent._process_one_preview(_job()) == "filled"
    apply_agent.takeover.await_human.assert_not_called()


# ── takeover in the submit pass ────────────────────────────────────────────────

def _approved():
    return _job(stage="ready_to_submit", automation_status="submitting", approved_at="2026-10-08T03:00:00Z",
                apply_preview={"platform": "greenhouse", "field_values": {}, "screening_answers": {},
                               "eligibility_answers": {}},
                preview_revision_hash="h1", approved_revision_hash="h1")


@pytest.fixture
def armed(mocker):
    mocker.patch.dict("os.environ", {"APPLY_AGENT_ARMED": "1"})
    mocker.patch("apply_agent.db.get_job_application", return_value=_approved())
    page = MagicMock()
    mocker.patch("apply_agent._launch_page", return_value=page)
    return page


def test_submit_challenge_before_the_click_unsolved_is_retryable_and_never_clicks(armed, mocker):
    apply_agent.takeover.challenge_present.return_value = True
    apply_agent.takeover.await_human.return_value = False
    with pytest.raises(apply_agent.TakeoverTimeout):
        apply_agent.submit(1)
    armed.get_by_role.return_value.click.assert_not_called()
    assert apply_agent.db.release_application.call_args[0][2] == "failed_retryable"


def test_submit_challenge_after_the_click_waits_then_confirms_without_clicking_again(armed, mocker):
    # Clear before the click, challenge after it, then solved by the human.
    apply_agent.takeover.challenge_present.side_effect = [False, False, True]
    confirmed = mocker.patch("apply_agent._submission_confirmed", side_effect=[False, True])
    apply_agent.submit(1)
    assert armed.get_by_role.return_value.click.call_count == 1
    assert confirmed.call_count == 2
    apply_agent.db.record_submission.assert_called_once()
    reason = apply_agent.takeover.await_human.call_args[0][3]
    assert "after Submit" in reason


def test_submit_challenge_after_the_click_unsolved_is_needs_confirmation(armed, mocker):
    apply_agent.takeover.challenge_present.side_effect = [False, False, True]
    apply_agent.takeover.await_human.return_value = False
    mocker.patch("apply_agent._submission_confirmed", return_value=False)
    with pytest.raises(apply_agent.TakeoverTimeout):
        apply_agent.submit(1)
    assert armed.get_by_role.return_value.click.call_count == 1
    assert apply_agent.db.release_application.call_args[0][2] == "needs_confirmation"
    apply_agent.db.record_submission.assert_not_called()


def test_launch_opens_no_debugging_port_without_browser_use(mocker, fake_playwright):
    # The port exists only for browser-use's CDP bridge; on the Beelink it would let any local
    # process attach to the armed browser.
    mocker.patch.object(config, "APPLY_GENERIC_ADAPTER", "none")
    page = apply_agent._launch_page("https://x/apply")
    assert fake_playwright.chromium.launch.call_args.kwargs["args"] == []
    assert id(page) not in apply_agent._CDP_PORTS


def test_launch_keeps_the_debugging_port_for_browser_use(mocker, fake_playwright):
    mocker.patch.object(config, "APPLY_GENERIC_ADAPTER", "browser_use")
    apply_agent._launch_page("https://x/apply")
    assert fake_playwright.chromium.launch.call_args.kwargs["args"] == ["--remote-debugging-port=54321"]


def test_screening_replay_can_skip_questions_not_on_this_step(mocker):
    # Workday shows one step at a time; an answer for another step must not wait out a label
    # search here (nor land on a look-alike question).
    apply_agent._form_inventory.return_value = [
        {"label": "Why us?", "kind": "textarea", "selector": "#why", "required": True, "filled": False}]
    fill = mocker.patch("apply_agent._fill_field", return_value=True)
    by_label = mocker.patch("apply_agent._set_field_by_label", return_value=True)
    report = _REAL_FILL_SCREENING(MagicMock(), {"Why us?": "Because.", "Salary?": "100k"}, only_present=True)
    assert report == {"Why us?": True}
    fill.assert_called_once()
    by_label.assert_not_called()
