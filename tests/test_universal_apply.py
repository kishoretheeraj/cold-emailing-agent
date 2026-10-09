"""The universal filler end to end in a real browser against fixture pages served over HTTP by a
local server that counts every POST (a real submission). Preview fills and stops; an unarmed
submit fills and presses nothing; an armed submit sends exactly once. Database, storage and the
model are mocked; the browser, the pages and the server are real. These fixtures are built from
platform shapes, not live recon (spec 2026-10-09 §5): they prove the safety rules, not a live site."""

import hashlib
import json
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import apply_agent
import config

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "universal"
ELIGIBILITY = {"work_authorized_us": "Yes", "requires_visa_sponsorship": "No"}
PDF = b"%PDF-1.4 resume"
# The submit pass re-hashes every document it uploads against the digest recorded at build time.
DIGESTS = {"resume_sha256": hashlib.sha256(PDF).hexdigest(), "cover_letter_sha256": hashlib.sha256(PDF).hexdigest()}


@pytest.fixture(scope="module")
def server():
    posts = []

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(FIXTURES), **kw)

        def log_message(self, *a):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            posts.append(self.path)
            body = b"<html><body><h1>Thank you for applying! Your application has been submitted.</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield {"base": f"http://127.0.0.1:{httpd.server_address[1]}", "posts": posts}
    httpd.shutdown()


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
def env(browser, server, mocker, tmp_path):
    server["posts"].clear()
    state = {"pages": [], "launch": [], "closed": []}

    def launch(url, storage_state=None, init_script=None):
        state["launch"].append({"url": url, "init_script": bool(init_script)})
        context = browser.new_context(storage_state=storage_state) if storage_state else browser.new_context()
        if init_script:
            context.add_init_script(init_script)
        page = context.new_page()
        page.goto(url)
        state["pages"].append(page)
        return page

    def close(page):
        state["closed"].append(page)   # contexts close at teardown, so tests can inspect the page

    mocker.patch.object(apply_agent, "_launch_page", side_effect=launch)
    mocker.patch.object(apply_agent, "_close_page", side_effect=close)
    mocker.patch.object(config, "APPLY_UNIVERSAL_ENABLED", True)
    mocker.patch.object(config, "APPLY_UNIVERSAL_PLATFORMS", ("generic",))
    mocker.patch.object(config, "APPLY_GENERIC_ADAPTER", "none")
    mocker.patch.object(config, "APPLY_TAKEOVER_ENABLED", False)
    mocker.patch.object(config, "APPLY_CONFIRMATION_POLLS", 4)
    mocker.patch.object(config, "APPLY_SESSIONS_DIR", str(tmp_path / "sessions"))
    mocker.patch.object(apply_agent, "_screening_completion", return_value="LinkedIn")
    mocker.patch.object(apply_agent.db, "load_prompts", return_value={"applicant_eligibility": json.dumps(ELIGIBILITY)})
    storage = MagicMock()
    storage.storage.from_.return_value.download.return_value = PDF
    mocker.patch.object(apply_agent.db, "get_client", return_value=storage)
    for name, value in (("claim_application", "lease-1"), ("heartbeat_application", None),
                        ("release_application", True), ("complete_preview", True),
                        ("renew_submission_lease", True), ("record_submission", True),
                        ("mark_unsupported", True), ("recover_stale_leases", 0)):
        mocker.patch.object(apply_agent.db, name, return_value=value)
    yield state
    for page in state["pages"]:
        page.context.close()


def _job(server, page, **kw):
    row = {"id": 41, "company": "Fixtureco", "role": "Associate Product Manager",
           "job_url": f"{server['base']}/{page}", "stage": "saved", "automation_status": "idle",
           "resume_file_ref": "41/resume.pdf", "cover_letter_file_ref": "41/cover.pdf"}
    row.update(kw)
    return row


def _released():
    return apply_agent.db.release_application.call_args[0]


def _preview():
    return apply_agent.db.complete_preview.call_args[0][2]


# ── preview ────────────────────────────────────────────────────────────────────

def test_preview_from_a_posting_fills_the_form_and_sends_nothing(env, server):
    assert apply_agent._process_one_preview(_job(server, "posting.html")) == "filled"
    assert server["posts"] == []
    preview = _preview()
    assert preview["universal"]["final_label"] == "Submit application"
    assert preview["universal"]["contact_fields"] == {
        "First name": "first_name", "Last name": "last_name", "Email": "email", "Phone": "phone",
        "LinkedIn profile": "linkedin"}
    assert preview["fill_report"]["attachments"]["resume"] is True
    assert preview["fill_report"]["required_unfilled"] == []
    assert preview["screening_answers"]["Why do you want to work at Fixtureco?"] == "LinkedIn"
    assert env["launch"][0]["init_script"] is True
    # The LinkedIn button was never pressed and the referrer's email stayed empty.
    posting, form = env["pages"][0], env["pages"][0].context.pages[-1]
    assert form is not posting
    assert posting.evaluate("window.__thirdParty || 0") == 0
    assert form.input_value("#ref") == ""
    assert form.input_value("#em") == "kishoretheerajvj@gmail.com"


def test_preview_of_a_native_form_answers_the_combobox_without_enter(env, server):
    assert apply_agent._process_one_preview(_job(server, "native_form.html")) == "filled"
    assert server["posts"] == []
    page = env["pages"][0]
    assert page.input_value("#src") == "LinkedIn"
    assert page.input_value("#zip") == ""
    assert page.input_value("#city") == "Hanover"
    assert page.evaluate("window.__ufBlocked") == 0


def test_preview_scopes_to_the_application_form(env, server):
    assert apply_agent._process_one_preview(_job(server, "job_alert.html")) == "filled"
    page = env["pages"][0]
    assert page.input_value("#ae") == ""            # the job-alert signup is left alone
    assert page.input_value("#re") == ""            # so is the referrer's email
    assert page.input_value("#em") == page.input_value("#em2") == "kishoretheerajvj@gmail.com"
    assert page.evaluate("document.getElementById('tr').files.length") == 0
    assert page.evaluate("document.getElementById('cv').files.length") == 1
    assert server["posts"] == []


def test_a_continue_submit_button_is_a_multi_step_stop_and_never_pressed(env, server):
    assert apply_agent._process_one_preview(_job(server, "continue_form.html")) == "blocked"
    assert _released()[2] == "needs_input"
    assert "multi-step" in _released()[3]
    assert server["posts"] == []
    apply_agent.db.complete_preview.assert_not_called()


def test_a_wizard_is_a_multi_step_stop(env, server):
    assert apply_agent._process_one_preview(_job(server, "wizard.html")) == "blocked"
    assert "multi-step" in _released()[3]
    assert env["pages"][0].evaluate("window.__steps") == [1]


def test_an_apply_link_to_another_host_types_nothing(env, server):
    assert apply_agent._process_one_preview(_job(server, "other_site.html")) == "blocked"
    assert _released()[2] == "needs_input"
    assert "nothing was typed" in _released()[3]
    other = env["pages"][0].context.pages[-1]
    assert other.input_value("#em") == ""
    assert server["posts"] == []


def test_apply_is_pressed_once_never_again_on_a_review_page(env, server):
    assert apply_agent._process_one_preview(_job(server, "to_review.html")) == "blocked"
    assert "did not open an application form" in _released()[3]
    assert server["posts"] == []


def test_a_password_wall_without_takeover_asks_for_a_human(env, server):
    assert apply_agent._process_one_preview(_job(server, "password_wall.html")) == "blocked"
    assert _released()[2] == "needs_input"
    assert "sign in" in _released()[3]
    assert env["pages"][0].evaluate("window.__submitted") == 0


def test_a_form_inside_an_iframe_is_filled_in_the_frame(env, server):
    assert apply_agent._process_one_preview(_job(server, "iframe.html")) == "filled"
    frame = env["pages"][0].frames[1]
    assert frame.input_value("#fn") == "Kishore Theeraj"
    assert server["posts"] == []


def test_enter_in_a_field_is_stopped_by_the_guard(env, server):
    apply_agent._process_one_preview(_job(server, "native_form.html"))
    page = env["pages"][0]
    page.focus("#name")
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    assert page.evaluate("window.__ufBlocked") == 1
    assert server["posts"] == []


def test_a_form_that_submits_itself_while_filling_is_not_previewed(env, server, mocker):
    real = apply_agent._fill_eligibility_answers

    def sneaky(view, answers):
        view.evaluate("() => document.getElementById('apply').requestSubmit()")
        return real(view, answers)

    mocker.patch.object(apply_agent, "_fill_eligibility_answers", side_effect=sneaky)
    assert apply_agent._process_one_preview(_job(server, "native_form.html")) == "blocked"
    assert "tried to submit itself" in _released()[3]
    assert server["posts"] == []


# ── submit ─────────────────────────────────────────────────────────────────────

def _approved(server, page, preview):
    return _job(server, page, stage="ready_to_submit", automation_status="submitting",
                approved_at="2026-10-09T03:00:00Z", apply_preview=preview,
                preview_revision_hash="h1", approved_revision_hash="h1", form_signature=None, **DIGESTS)


@pytest.fixture
def approved(env, server, mocker):
    def _make(page):
        apply_agent._process_one_preview(_job(server, page))
        preview = _preview()
        mocker.patch.object(apply_agent.approval_signature, "key_configured", return_value=True)
        mocker.patch.object(apply_agent.approval_signature, "verify", return_value=None)
        mocker.patch.object(apply_agent.db, "get_job_application", return_value=_approved(server, page, preview))
        server["posts"].clear()
        return preview
    return _make


def test_unarmed_submit_fills_and_presses_nothing(approved, server, mocker):
    approved("posting.html")
    mocker.patch.dict("os.environ", {}, clear=False)
    os.environ.pop("APPLY_AGENT_ARMED", None)
    apply_agent.submit(41)
    assert server["posts"] == []
    apply_agent.db.record_submission.assert_not_called()


@pytest.mark.parametrize("page", ["posting.html", "native_form.html", "job_alert.html", "iframe.html"])
def test_armed_submit_sends_exactly_once_and_records_it(approved, server, mocker, page):
    approved(page)
    regenerate = mocker.patch.object(apply_agent, "_screening_completion")
    mocker.patch.dict("os.environ", {"APPLY_AGENT_ARMED": "1"})
    apply_agent.submit(41)
    assert server["posts"] == ["/received"]
    regenerate.assert_not_called()
    apply_agent.db.record_submission.assert_called_once()


def test_submit_refuses_when_the_submit_button_changed(approved, server, mocker):
    preview = approved("native_form.html")
    preview["universal"]["final_label"] = "Submit application"     # what the operator approved
    mocker.patch.dict("os.environ", {"APPLY_AGENT_ARMED": "1"})
    with pytest.raises(apply_agent.FormChangedError):
        apply_agent.submit(41)
    assert server["posts"] == []
    assert _released()[2] == "needs_input"


def test_universal_off_leaves_generic_rows_idle(env, server, mocker):
    mocker.patch.object(config, "APPLY_UNIVERSAL_ENABLED", False)
    assert apply_agent._process_one_preview(_job(server, "posting.html")) == "skipped"
    assert env["pages"] == []


def test_a_platform_not_on_the_allow_list_stays_idle(env, server, mocker):
    mocker.patch.object(config, "APPLY_UNIVERSAL_PLATFORMS", ("workable",))
    assert apply_agent._process_one_preview(_job(server, "posting.html")) == "skipped"


# ── sign-in handover (the Beelink) ─────────────────────────────────────────────

def test_a_password_wall_hands_over_once_and_remembers_the_site(env, server, mocker):
    mocker.patch.object(config, "APPLY_TAKEOVER_ENABLED", True)

    def human(job_id, lease, kind, reason):
        assert kind == "login" and "127.0.0.1" in reason
        page = env["pages"][0]
        page.context.add_cookies([{"name": "sid", "value": "signed-in", "url": server["base"]}])
        page.goto(f"{server['base']}/single_page.html")
        return True

    wait = mocker.patch.object(apply_agent.takeover, "await_human", side_effect=human)
    assert apply_agent._process_one_preview(_job(server, "password_wall.html")) == "filled"
    wait.assert_called_once()
    assert apply_agent.ats_sessions.state_path("127.0.0.1") is not None
    assert server["posts"] == []


def test_a_wall_still_there_after_the_takeover_stops(env, server, mocker):
    mocker.patch.object(config, "APPLY_TAKEOVER_ENABLED", True)
    wait = mocker.patch.object(apply_agent.takeover, "await_human", return_value=True)
    assert apply_agent._process_one_preview(_job(server, "password_wall.html")) == "blocked"
    wait.assert_called_once()
    assert "still asks to sign in" in _released()[3]


def test_nobody_taking_over_leaves_the_row_for_a_human(env, server, mocker):
    mocker.patch.object(config, "APPLY_TAKEOVER_ENABLED", True)
    mocker.patch.object(apply_agent.takeover, "await_human", return_value=False)
    with pytest.raises(apply_agent.TakeoverTimeout):
        apply_agent._process_one_preview(_job(server, "password_wall.html"))
    assert _released()[2] == "needs_input"


def test_a_hidden_thank_you_panel_is_not_a_confirmation(env, server):
    apply_agent._process_one_preview(_job(server, "posting.html"))
    form = env["pages"][0].context.pages[-1]
    assert form.locator("#done").is_hidden()
    assert apply_agent._submission_state(form) == "pending"


def test_armed_submit_refuses_documents_that_do_not_match_the_approved_digest(approved, server, mocker):
    approved("posting.html")
    row = apply_agent.db.get_job_application.return_value
    row["resume_sha256"] = hashlib.sha256(b"a different resume").hexdigest()
    mocker.patch.dict("os.environ", {"APPLY_AGENT_ARMED": "1"})
    with pytest.raises(apply_agent.DigestMismatch):
        apply_agent.submit(41)
    assert server["posts"] == []
    assert _released()[2] == "failed_retryable"


# ── network sends: blocked in the preview, allowed when the approved submit needs them ──

def test_preview_blocks_network_sends_without_failing_the_preview(env, server):
    assert apply_agent._process_one_preview(_job(server, "upload_on_select.html")) == "filled"
    page = env["pages"][0]
    assert server["posts"] == []                                   # no beacon, no upload, no application
    assert apply_agent.universal_filler.network_count(page) >= 2   # the beacon and the upload were stopped
    assert apply_agent.universal_filler.guard_count(page) == 0


def test_armed_submit_lets_the_upload_through_and_sends_once(approved, server, mocker):
    approved("upload_on_select.html")
    mocker.patch.dict("os.environ", {"APPLY_AGENT_ARMED": "1"})
    apply_agent.submit(41)
    assert server["posts"].count("/received") == 1
    assert "/upload" in server["posts"]
    assert server["posts"].index("/upload") < server["posts"].index("/received")


@pytest.mark.parametrize("job_url,for_submit,blocks", [
    ("https://careers.fixtureco.com/jobs/1", False, True),
    ("https://careers.fixtureco.com/jobs/1", True, False),
    ("https://fixture.wd5.myworkdayjobs.com/en-US/External/job/X_R-1", False, False),
    ("https://fixture.wd5.myworkdayjobs.com/en-US/External/job/X_R-1", True, False),
    ("https://boards.greenhouse.io/fixtureco/jobs/123", False, True),
    ("https://boards.greenhouse.io/fixtureco/jobs/123", True, False),
])
def test_which_launches_block_network_sends(mocker, job_url, for_submit, blocks):
    mocker.patch.object(config, "APPLY_UNIVERSAL_ENABLED", True)
    mocker.patch.object(config, "APPLY_UNIVERSAL_PLATFORMS", ("generic",))
    launch = mocker.patch.object(apply_agent, "_launch_page")
    platform = apply_agent.ats_platform.classify(job_url)
    apply_agent._launch_for({"job_url": job_url}, platform, for_submit=for_submit)
    script = launch.call_args.kwargs["init_script"]
    assert script == apply_agent.universal_filler.submit_guard(block_network=blocks)
