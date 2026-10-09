"""apply_agent's Workday path end to end against a real browser and the fixture tenant
(tests/fixtures/workday_tenant.html): prepare reaches the wizard, fills every step, and stops at
Review; submit replays the reviewed answers and presses Submit only there. Database, storage,
the inbox and the model are mocked; the browser and the page are real."""

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet

import apply_agent
import config
import credential_vault

FIXTURE = (Path(__file__).resolve().parent / "fixtures" / "workday_tenant.html").as_uri()
JOB_URL = "https://fixture.wd5.myworkdayjobs.com/en-US/External/job/Remote/PM_R-1"
TENANT = "fixture.wd5.myworkdayjobs.com"
ELIGIBILITY = {
    "work_authorized_us": "Yes",
    "requires_visa_sponsorship": "No",
    "gender": "I do not wish to answer",
    "veteran_status": "I am not a protected veteran",
    "how did you hear about us": "LinkedIn",
    "i have read and agree": "Yes",
}


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    executable = "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(executable_path=executable)
        except Exception as exc:
            pytest.skip(f"no launchable Chromium here: {exc}")
        yield b
        b.close()


@pytest.fixture
def tenant(browser, mocker, tmp_path):
    """Opens the fixture for each _launch_page call with the scenario in `tenant.scenario`."""
    state = {"scenario": {}, "pages": [], "storage_states": [], "logs": [], "positions": []}

    def launch(url, storage_state=None, init_script=None):
        state["storage_states"].append(storage_state)
        context = browser.new_context(storage_state=storage_state) if storage_state else browser.new_context()
        if init_script:
            context.add_init_script(init_script)
        page = context.new_page()
        page.add_init_script(f"window.SCENARIO = {json.dumps(state['scenario'])};")
        page.goto(FIXTURE)
        state["pages"].append(page)
        return page

    mocker.patch.object(apply_agent, "_launch_page", side_effect=launch)
    def close(page):
        # Keep what the fixture recorded; the context is gone after this.
        state["logs"].append(page.evaluate("window.LOG"))
        state["positions"].append(apply_agent.workday_adapter.progress(page))
        page.context.close()

    mocker.patch.object(apply_agent, "_close_page", side_effect=close)
    mocker.patch.object(config, "APPLY_WORKDAY_ENABLED", True)
    mocker.patch.object(config, "APPLY_TAKEOVER_ENABLED", False)
    mocker.patch.object(config, "VAULT_KEY", Fernet.generate_key().decode())
    mocker.patch.object(config, "APPLY_VAULT_PATH", str(tmp_path / "vault.bin"))
    mocker.patch.object(config, "APPLY_SESSIONS_DIR", str(tmp_path / "sessions"))
    mocker.patch.object(apply_agent.email_verification, "wait_for_verification", return_value={"code": "482913"})
    mocker.patch.object(apply_agent, "_screening_completion",
                        return_value="I build products for small-business finance, which is this team's work.")
    mocker.patch.object(apply_agent.db, "load_prompts",
                        return_value={"applicant_eligibility": json.dumps(ELIGIBILITY)})
    storage = MagicMock()
    storage.storage.from_.return_value.download.return_value = b"%PDF-1.4 resume"
    mocker.patch.object(apply_agent.db, "get_client", return_value=storage)
    for name, value in (("claim_application", "lease-1"), ("heartbeat_application", None),
                        ("release_application", True), ("complete_preview", True),
                        ("renew_submission_lease", True), ("record_submission", True),
                        ("mark_unsupported", True), ("recover_stale_leases", 0)):
        mocker.patch.object(apply_agent.db, name, return_value=value)
    return state




def _job(**kw):
    row = {"id": 31, "company": "Fixture Tenant", "role": "Product Manager", "job_url": JOB_URL,
           "stage": "saved", "automation_status": "idle",
           "resume_file_ref": "31/resume.pdf", "cover_letter_file_ref": "31/cover.pdf"}
    row.update(kw)
    return row


def _vault():
    return credential_vault.Vault(config.APPLY_VAULT_PATH, config.VAULT_KEY)


# ── prepare ────────────────────────────────────────────────────────────────────

def test_prepare_signs_up_fills_every_step_and_stops_at_review(tenant):
    assert apply_agent._process_one_preview(_job()) == "filled"
    log = tenant["logs"][0]
    assert log[:3] == ["applyManually", "createAccount:kishoretheerajvj@gmail.com", "verify:482913"]
    assert "SUBMIT" not in log
    assert not any(e.startswith("blocked:") for e in log)
    assert tenant["positions"][0] == (4, 5)

    args = apply_agent.db.complete_preview.call_args[0]
    assert args[:2] == (31, "lease-1")
    preview = args[2]
    assert preview["platform"] == "workday"
    assert preview["screening_answers"] == {
        "Why do you want to work at Fixture Tenant?": "I build products for small-business finance, which is this team's work."}
    assert preview["eligibility_answers"] == ELIGIBILITY
    assert preview["workday_steps"] == ["My Information", "My Experience", "Application Questions",
                                        "Voluntary Disclosures"]
    assert _vault().get(TENANT)["status"] == "active"


def test_prepare_saves_the_tenant_session_and_reuses_it(tenant):
    apply_agent._process_one_preview(_job())
    apply_agent._process_one_preview(_job(id=32))
    assert tenant["storage_states"][0] is None
    assert tenant["storage_states"][1] is not None  # second application starts signed in


def test_prepare_with_a_question_it_cannot_answer_goes_to_needs_input(tenant, mocker):
    answers = dict(ELIGIBILITY)
    del answers["how did you hear about us"]
    apply_agent.db.load_prompts.return_value = {"applicant_eligibility": json.dumps(answers)}
    mocker.patch.object(apply_agent, "_screening_completion", return_value="NEEDS HUMAN REVIEW: not in the facts")
    assert apply_agent._process_one_preview(_job()) == "blocked"
    args = apply_agent.db.release_application.call_args[0]
    assert args[:3] == (31, "lease-1", "needs_input")
    assert "How Did You Hear About Us?" in args[3]
    apply_agent.db.complete_preview.assert_not_called()
    assert "SUBMIT" not in tenant["logs"][0]


def test_prepare_stops_on_an_existing_account_it_has_no_password_for(tenant):
    tenant["scenario"] = {"existingAccount": True}
    with pytest.raises(apply_agent.workday_adapter.WorkdayStop):
        apply_agent._process_one_preview(_job())
    args = apply_agent.db.release_application.call_args[0]
    assert args[2] == "needs_input"
    assert "already exists" in args[3]


def test_prepare_already_applied_goes_to_needs_input(tenant):
    tenant["scenario"] = {"alreadyApplied": True}
    with pytest.raises(apply_agent.workday_adapter.WorkdayStop):
        apply_agent._process_one_preview(_job())
    assert "already applied" in apply_agent.db.release_application.call_args[0][3]


def test_workday_stays_unsupported_where_it_is_not_enabled(tenant, mocker):
    mocker.patch.object(config, "APPLY_WORKDAY_ENABLED", False)
    assert apply_agent._process_one_preview(_job()) == "blocked"
    apply_agent.db.mark_unsupported.assert_called_once()
    assert tenant["pages"] == []


def test_workday_without_a_vault_key_is_left_idle(tenant, mocker):
    mocker.patch.object(config, "VAULT_KEY", None)
    assert apply_agent._process_one_preview(_job()) == "skipped"
    apply_agent.db.claim_application.assert_not_called()


# ── submit ─────────────────────────────────────────────────────────────────────

_DIGEST = hashlib.sha256(b"%PDF-1.4 resume").hexdigest()


def _approved_from(preview):
    return _job(stage="ready_to_submit", automation_status="submitting",
                approved_at="2026-10-08T03:00:00Z", apply_preview=preview,
                preview_revision_hash="h1", approved_revision_hash="h1", form_signature=None,
                resume_sha256=_DIGEST, cover_letter_sha256=_DIGEST)


def test_submit_replays_the_reviewed_answers_and_presses_submit_only_on_review(tenant, mocker):
    apply_agent._process_one_preview(_job())
    preview = apply_agent.db.complete_preview.call_args[0][2]
    mocker.patch.dict("os.environ", {"APPLY_AGENT_ARMED": "1"})
    mocker.patch.object(apply_agent.approval_signature, "key_configured", return_value=True)
    mocker.patch.object(apply_agent.approval_signature, "verify", return_value=None)
    mocker.patch.object(apply_agent.db, "get_job_application", return_value=_approved_from(preview))
    regenerate = mocker.patch.object(apply_agent, "_screening_completion")

    apply_agent.submit(31)

    log = tenant["logs"][-1]
    assert log.count("SUBMIT") == 1
    assert log[-1] == "SUBMIT"
    assert not any(e.startswith("blocked:") for e in log)
    assert "createAccount" not in " ".join(log)  # signed in with the saved session/account
    regenerate.assert_not_called()  # reviewed answers are replayed, never regenerated
    apply_agent.db.record_submission.assert_called_once()
    apply_agent.db.release_application.assert_not_called()


def test_submit_never_clicks_when_a_step_cannot_be_completed(tenant, mocker):
    apply_agent._process_one_preview(_job())
    preview = dict(apply_agent.db.complete_preview.call_args[0][2])
    preview["screening_answers"] = {}  # the reviewed answer is gone: the step cannot pass
    mocker.patch.dict("os.environ", {"APPLY_AGENT_ARMED": "1"})
    mocker.patch.object(apply_agent.approval_signature, "key_configured", return_value=True)
    mocker.patch.object(apply_agent.approval_signature, "verify", return_value=None)
    mocker.patch.object(apply_agent.db, "get_job_application", return_value=_approved_from(preview))
    with pytest.raises(Exception):
        apply_agent.submit(31)
    assert "SUBMIT" not in tenant["logs"][-1]
    assert apply_agent.db.release_application.call_args[0][2] in ("failed_retryable", "needs_input")
    apply_agent.db.renew_submission_lease.assert_not_called()
