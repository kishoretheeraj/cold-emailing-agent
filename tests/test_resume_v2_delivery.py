import sys
import types

import pytest

from resume.v2 import delivery


def test_upload_pdf_uses_content_addressed_path(tmp_path, mocker):
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")
    upload = mocker.patch.object(delivery.db, "upload_resume_file", return_value=("resumes/7/resume-abc.pdf", "abc"))
    assert delivery.upload_pdf(7, str(pdf), "resume") == ("resumes/7/resume-abc.pdf", "abc")
    upload.assert_called_once_with("resumes/7/resume.pdf", b"%PDF-1.4 x", "application/pdf")


def test_upload_pdf_rejects_unknown_kind(tmp_path):
    with pytest.raises(ValueError):
        delivery.upload_pdf(1, str(tmp_path / "x.pdf"), "poster")


def test_upload_pdf_scrubs_metadata_only_when_titled(tmp_path, mocker):
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"x")
    mocker.patch.object(delivery.db, "upload_resume_file", return_value=("p", "s"))
    scrub = types.SimpleNamespace(calls=[])
    scrub.scrub_pdf_metadata = lambda path, title, keywords: scrub.calls.append((path, title))
    mocker.patch.dict(sys.modules, {"resume_scrub": scrub})
    delivery.upload_pdf(1, str(pdf), "resume")
    assert scrub.calls == []
    delivery.upload_pdf(1, str(pdf), "resume", title="Acme - Resume")
    assert scrub.calls == [(str(pdf), "Acme - Resume")]


def _fake_agent(mocker, replies, lint):
    replies = iter(replies)
    module = types.SimpleNamespace(
        _COVER_LETTER_PROMPT="{company}|{role}|{angle}|{gaps}",
        _call_claude=lambda prompt: (next(replies), {}),
        _lint_cover_letter=lambda text, resume_text: lint(text))
    mocker.patch.dict(sys.modules, {"resume_agent": module})


CONTENT = {"headline": "APM", "summary": "Ships AI tooling.", "experience": [{"role": "R", "bullets": ["did a thing"]}]}


def test_cover_letter_retries_once_then_passes(mocker):
    _fake_agent(mocker, ["bad", "good"], lambda t: ["too long"] if t == "bad" else [])
    assert delivery.generate_cover_letter({"company": "Acme", "role": "PM"}, CONTENT) == "good"


def test_cover_letter_raises_when_lint_keeps_failing(mocker):
    _fake_agent(mocker, ["bad", "bad"], lambda t: ["nope"])
    with pytest.raises(ValueError):
        delivery.generate_cover_letter({"company": "Acme", "role": "PM"}, CONTENT)


def test_summarize_runs_counts_and_collects_failures():
    runs = [{"kind": "prepare", "outcome": "ready", "application_id": 1},
            {"kind": "prepare", "outcome": "failed_retryable", "application_id": 2, "error_class": "Timeout"},
            {"kind": "submit", "outcome": "submitted", "application_id": 3}]
    summary = delivery.summarize_runs(runs)
    assert summary["total"] == 3 and summary["by_kind"] == {"prepare": 2, "submit": 1}
    assert summary["failures"] == [(2, "Timeout")]
    assert delivery.summarize_runs(None)["total"] == 0


def test_emit_run_digest_is_loudly_unimplemented():
    with pytest.raises(NotImplementedError):
        delivery.emit_run_digest()
