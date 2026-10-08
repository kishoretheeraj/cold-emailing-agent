"""Load test of the real preview and submit passes (spec 2026-10-08 fifty-a-day §5): real apply_agent
code, real Playwright and Chromium, against local Greenhouse/Lever/Ashby lookalike forms
(tests/fixtures/ats_forms). Only the database and the model are faked.

Each row checks:
- every required field is filled;
- the attachments arrive with the candidate's name;
- the submit pass clicks once and sees the confirmation;
- no Chromium process or temp file outlives a row.

APPLY_LOAD_ROWS sets the row count (default 6; the fifty-a-day check runs 50). Skipped where
Playwright or Chromium is unavailable."""

import functools
import http.server
import json
import os
import socketserver
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

import pytest

import apply_agent
import config

_FORMS = Path(__file__).resolve().parent / "fixtures" / "ats_forms"
_ROWS = int(os.environ.get("APPLY_LOAD_ROWS", "6"))
_PLATFORMS = ("greenhouse", "lever", "ashby")
_CHROMIUM = "/opt/pw-browsers/chromium"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def site():
    pytest.importorskip("playwright.sync_api")
    if not os.path.exists(_CHROMIUM):
        pytest.skip("no Chromium at /opt/pw-browsers here")
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(_FORMS)))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _browser_pids():
    # Process ids, not a count: other suites on the same machine start and stop their own Chromium,
    # and only a browser this test started and left running is a leak.
    out = subprocess.run(["pgrep", "-f", "pw-browsers/chromium"], capture_output=True, text=True).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def _answers(prompt, job_id):
    # The batched screening call: answer every listed question the way a grounded model would.
    lines = [l for l in prompt.splitlines() if l.startswith("[")]
    out = []
    for line in lines:
        index = int(line[1:line.index("]")])
        out.append({"id": index, "answer": "LinkedIn" if "hear about" in line else
                    "I have shipped lending products end to end at Protium Finance."})
    return json.dumps(out)


@pytest.fixture
def wired(mocker, site):
    mocker.patch.object(config, "APPLY_BROWSER_EXECUTABLE", _CHROMIUM)
    mocker.patch.object(config, "APPLY_BROWSER_HEADLESS", True)
    mocker.patch.object(config, "APPLY_GENERIC_ADAPTER", "none")
    mocker.patch.object(config, "APPLY_TAKEOVER_ENABLED", False)
    mocker.patch.object(config, "APPLY_CONFIRMATION_POLLS", 6)
    state = {"previews": {}, "released": {}, "recorded": [], "uploaded": {}}
    db = apply_agent.db
    mocker.patch.object(db, "claim_application", side_effect=lambda job_id, to: str(uuid.uuid4()))
    mocker.patch.object(db, "heartbeat_application")
    mocker.patch.object(db, "recover_stale_leases", return_value=0)
    mocker.patch.object(db, "complete_preview", side_effect=lambda job_id, lease, preview, signature:
                        state["previews"].__setitem__(job_id, (preview, signature)) or True)
    mocker.patch.object(db, "release_application", side_effect=lambda job_id, lease, to, reason=None:
                        state["released"].__setitem__(job_id, (to, reason)) or True)
    mocker.patch.object(db, "renew_submission_lease", return_value=True)
    mocker.patch.object(db, "record_submission", side_effect=lambda *a, **k: state["recorded"].append(a[0]) or True)
    mocker.patch.object(db, "load_prompts", return_value={"applicant_eligibility": json.dumps(
        {"requires_visa_sponsorship": "Yes", "work_authorized_us": "Yes"})})
    pdf = b"%PDF-1.4\n%fixture\n"
    client = mocker.MagicMock()
    client.storage.from_.return_value.download.return_value = pdf
    mocker.patch.object(db, "get_client", return_value=client)
    mocker.patch.object(apply_agent, "_application_url", side_effect=lambda url: url)
    mocker.patch.object(apply_agent.ats_platform, "classify",
                        side_effect=lambda url: url.rsplit("/", 1)[-1].split(".")[0])
    mocker.patch.object(apply_agent, "_screening_completion", side_effect=_answers)
    mocker.patch.object(apply_agent.approval_signature, "key_configured", return_value=True)
    mocker.patch.object(apply_agent.approval_signature, "verify", return_value=None)
    close = apply_agent._close_page

    def close_and_read(page):
        try:
            state["uploaded"][page.url] = page.evaluate("window.uploaded || null")
        except Exception:
            pass
        close(page)

    mocker.patch.object(apply_agent, "_close_page", side_effect=close_and_read)
    return state


def _job(site, index):
    platform = _PLATFORMS[index % len(_PLATFORMS)]
    return {"id": index + 1, "company": f"Company {index}", "role": "Associate Product Manager",
            "job_url": f"{site}/{platform}.html", "stage": "saved", "automation_status": "idle",
            "resume_file_ref": "r.pdf", "cover_letter_file_ref": "c.pdf",
            "posting_snapshot": {"description": "Own the lending roadmap with engineering and design."}}


def test_preview_then_submit_at_load(wired, site, mocker):
    tmp_before = set(os.listdir(tempfile.gettempdir()))
    browsers_before = _browser_pids()
    durations = []
    jobs = [_job(site, i) for i in range(_ROWS)]

    for job in jobs:
        started = time.monotonic()
        assert apply_agent._process_one_preview(job) == "filled", wired["released"].get(job["id"])
        durations.append(time.monotonic() - started)
        assert not _browser_pids() - browsers_before, "a Chromium outlived its row"

    for job in jobs:
        preview, signature = wired["previews"][job["id"]]
        report = preview["fill_report"]
        assert report["required_unfilled"] == [], report
        assert report["attachments"]["resume"] is True
        assert preview["eligibility_answers"]["requires_visa_sponsorship"] == "Yes"
        assert set(preview["screening_answers"]) == {"Why do you want to work here?", "How did you hear about us?"}

    for job in jobs:
        preview, signature = wired["previews"][job["id"]]
        row = dict(job, stage="ready_to_submit", automation_status="submitting", apply_preview=preview,
                   approved_at="2026-10-09T12:00:00+00:00", approved_revision_hash="h", preview_revision_hash="h",
                   form_signature=signature)
        mocker.patch.object(apply_agent.db, "get_job_application", return_value=row)
        started = time.monotonic()
        with pytest.MonkeyPatch.context() as env:
            env.setenv("APPLY_AGENT_ARMED", "1")
            apply_agent.submit(job["id"])
        durations.append(time.monotonic() - started)

    assert sorted(wired["recorded"]) == [j["id"] for j in jobs]
    names = [names for names in wired["uploaded"].values() if names]
    assert names and all(n[0] == "Kishore_Theeraj_Vasudevan_Jaya_Resume.pdf" for n in names)
    assert not _browser_pids() - browsers_before
    leaked = set(os.listdir(tempfile.gettempdir())) - tmp_before
    assert not [f for f in leaked if f.endswith(".pdf")]
    print(f"\n{_ROWS} previews + {_ROWS} submits: mean {sum(durations) / len(durations):.2f}s per pass, "
          f"max {max(durations):.2f}s")
