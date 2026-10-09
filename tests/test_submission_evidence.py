"""Submission evidence: after a confirmed submit, the confirmation page's URL, a text excerpt and
a screenshot (private bucket) are recorded with the submission. Best effort throughout: capturing
proof must never turn a real submission into an unconfirmed one."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import apply_agent
import db

FIXTURE = (Path(__file__).resolve().parent / "fixtures" / "workday_tenant.html").as_uri()


# ── db.upload_evidence ─────────────────────────────────────────────────────────

def test_upload_evidence_writes_to_the_private_bucket_without_upsert(mocker):
    client = MagicMock()
    mocker.patch.object(db, "get_client", return_value=client)
    assert db.upload_evidence("31/run/confirmation.png", b"png", "image/png") == "31/run/confirmation.png"
    client.storage.from_.assert_called_once_with("application-evidence")
    client.storage.from_.return_value.upload.assert_called_once_with(
        "31/run/confirmation.png", b"png", {"content-type": "image/png", "upsert": "false"})


# ── _capture_evidence ──────────────────────────────────────────────────────────

def _page(url="https://jobs.lever.co/acme/1/thanks", text="Header\nThank you for applying to Acme!\nFooter",
          screenshot=b"\x89PNG"):
    page = MagicMock()
    page.url = url
    page.inner_text.return_value = text
    page.screenshot.return_value = screenshot
    return page


def test_capture_records_url_excerpt_and_screenshot(mocker):
    upload = mocker.patch.object(apply_agent.db, "upload_evidence", side_effect=lambda path, *a: path)
    evidence = apply_agent._capture_evidence(_page(), 31)
    assert evidence["url"] == "https://jobs.lever.co/acme/1/thanks"
    assert "Thank you for applying to Acme!" in evidence["text"]
    assert evidence["screenshot"].startswith("31/") and evidence["screenshot"].endswith("/confirmation.png")
    assert upload.call_args[0][1:] == (b"\x89PNG", "image/png")


def test_capture_trims_the_excerpt():
    page = _page(text="x" * 5000)
    evidence = apply_agent._capture_evidence(page, 31, upload=False)
    assert len(evidence["text"]) <= 1000


def test_capture_survives_a_failed_screenshot_or_upload(mocker):
    page = _page()
    page.screenshot.side_effect = RuntimeError("page closed")
    evidence = apply_agent._capture_evidence(page, 31)
    assert evidence["url"] and "screenshot" not in evidence
    page = _page()
    mocker.patch.object(apply_agent.db, "upload_evidence", side_effect=RuntimeError("storage down"))
    assert "screenshot" not in apply_agent._capture_evidence(page, 31)


def test_capture_never_raises():
    page = MagicMock()
    type(page).url = property(lambda self: (_ for _ in ()).throw(RuntimeError("gone")))
    page.inner_text.side_effect = RuntimeError("gone")
    page.screenshot.side_effect = RuntimeError("gone")
    assert apply_agent._capture_evidence(page, 31) == {}


# ── record with evidence, falling back for an older database ──────────────────

def test_record_with_evidence_falls_back_without_it(mocker):
    record = mocker.patch.object(apply_agent.db, "record_submission",
                                 side_effect=[RuntimeError("function record_submission(...) does not exist"), True])
    assert apply_agent._record_submission(31, "lease-1", "lever", "2026-10-08", {"url": "u"}) is True
    first, second = record.call_args_list
    assert first.kwargs == {"evidence": {"url": "u"}}
    assert second.kwargs == {}


def test_record_without_evidence_does_not_retry(mocker):
    record = mocker.patch.object(apply_agent.db, "record_submission", side_effect=RuntimeError("db down"))
    with pytest.raises(RuntimeError):
        apply_agent._record_submission(31, "lease-1", "lever", "2026-10-08", {})
    assert record.call_count == 1


# ── real page ──────────────────────────────────────────────────────────────────

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


def test_capture_on_a_real_confirmation_page(browser, mocker):
    uploads = {}
    mocker.patch.object(apply_agent.db, "upload_evidence",
                        side_effect=lambda path, data, ctype: uploads.setdefault(path, (data, ctype)) and path)
    context = browser.new_context()
    page = context.new_page()
    page.add_init_script(f"window.SCENARIO = {json.dumps({'start': 'wizard', 'fields': [], 'steps': ['Review']})};")
    page.goto(FIXTURE)
    page.evaluate("submitApp()")
    evidence = apply_agent._capture_evidence(page, 31)
    context.close()
    assert "Thank you for applying" in evidence["text"]
    data, ctype = uploads[evidence["screenshot"]]
    assert ctype == "image/png" and data[:4] == b"\x89PNG"
