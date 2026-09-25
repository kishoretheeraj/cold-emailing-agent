"""Tests for cu_linkedin.run() and the module's import hygiene."""

import ast
import os

import pytest

import config
import cu_linkedin
import db


@pytest.fixture(autouse=True)
def no_real_calls(mocker):
    mocker.patch.object(db, "record_run")
    mocker.patch.object(db, "create_job_application", return_value={"id": 1})
    mocker.patch.object(db, "get_pause_scope", return_value="none")


# ── Import hygiene ─────────────────────────────────────────────────────────────

def test_cu_linkedin_never_imports_sentence_transformers_or_torch():
    # Checked statically, not via sys.modules: the suite already imports job_pick, so a
    # sys.modules check would pass vacuously. torch must never be co-resident with a
    # long-lived browser-agent process -- that is job_pick.py's oneshot unit's job.
    source = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "cu_linkedin.py")
    with open(source) as f:
        tree = ast.parse(f.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "sentence_transformers" not in imported
    assert "torch" not in imported
    assert "job_pick" not in imported


def test_cu_linkedin_holds_no_linkedin_credential_constants():
    assert not hasattr(config, "CU_LINKEDIN_EMAIL")
    assert not hasattr(config, "CU_LINKEDIN_PASSWORD")


# ── run() ──────────────────────────────────────────────────────────────────────

def _posting_json():
    return ('[{"company": "Acme", "role": "PM", '
            '"job_url": "https://www.linkedin.com/jobs/view/1"}]')


def test_run_persists_what_the_session_found(mocker):
    mocker.patch.object(cu_linkedin, "run_session", return_value=(_posting_json(), 5, 0))
    record_run = mocker.patch.object(db, "record_run")
    cu_linkedin.run()
    args = record_run.call_args
    assert args.args[0] == "success"
    assert args.args[1] == 1
    assert args.kwargs["source"] == "cu_linkedin"


def test_run_records_a_failure_when_a_row_errors(mocker):
    mocker.patch.object(cu_linkedin, "run_session", return_value=(_posting_json(), 5, 0))
    mocker.patch.object(db, "create_job_application", side_effect=RuntimeError("boom"))
    record_run = mocker.patch.object(db, "record_run")
    cu_linkedin.run()
    assert record_run.call_args.args[0] == "failure"


def test_run_includes_action_failures_from_the_session_in_the_error_count(mocker):
    # If Xvfb/Chrome is down, every action fails during the session -- that must surface as a
    # real error count on record_run, not the hardcoded errors=0 a wedged box used to report
    # (which would make M2's host-health UI render a wedged box as healthy).
    mocker.patch.object(cu_linkedin, "run_session", return_value=("[]", 12, 3))
    record_run = mocker.patch.object(db, "record_run")
    cu_linkedin.run()
    assert record_run.call_args.args[0] == "failure"
    assert record_run.call_args.args[3] == 3


def test_run_logs_reply_length_when_a_non_empty_reply_extracts_to_zero_postings(mocker, caplog):
    # A truncated/broken extraction and a genuine "nothing found" session both end up with
    # postings == [] -- log the raw reply's length (never the text itself) so the two are
    # distinguishable on the first live run.
    mocker.patch.object(cu_linkedin, "run_session", return_value=("not valid json", 5, 0))
    with caplog.at_level("INFO"):
        cu_linkedin.run()
    assert any("reply_len=14" in r.message for r in caplog.records)


def test_run_logs_a_small_reply_length_for_a_genuinely_empty_reply(mocker, caplog):
    # A genuine "nothing found" session replies with the literal "[]" the task prompt asks for --
    # its reply_len (2) reads as obviously clean next to a broken/truncated session's much larger
    # reply_len, which is the whole point of logging the length rather than nothing at all.
    mocker.patch.object(cu_linkedin, "run_session", return_value=("[]", 5, 0))
    with caplog.at_level("INFO"):
        cu_linkedin.run()
    assert any("reply_len=2" in r.message for r in caplog.records)


def test_run_no_ops_when_disabled(mocker):
    mocker.patch.object(config, "CU_LINKEDIN_ENABLED", False)
    session = mocker.patch.object(cu_linkedin, "run_session")
    record_run = mocker.patch.object(db, "record_run")
    cu_linkedin.run()
    session.assert_not_called()
    record_run.assert_not_called()


@pytest.mark.parametrize("scope", ["agent", "all"])
def test_run_paused_exits_without_a_session(mocker, scope):
    # LinkedIn browsing is the single highest-consequence activity in this whole system -- it
    # risks the user's real account -- so it is the one thing the global pause switch must be
    # able to stop, matching the existing convention in agent.py and monitor.py. No record_run
    # call on the paused exit either, same rule monitor.py follows (this can fire far more often
    # than a real session would, and must not flood agent_runs).
    mocker.patch.object(db, "get_pause_scope", return_value=scope)
    session = mocker.patch.object(cu_linkedin, "run_session")
    record_run = mocker.patch.object(db, "record_run")
    cu_linkedin.run()
    session.assert_not_called()
    record_run.assert_not_called()


def test_run_not_paused_proceeds(mocker):
    mocker.patch.object(db, "get_pause_scope", return_value="none")
    session = mocker.patch.object(cu_linkedin, "run_session", return_value=("[]", 0, 0))
    cu_linkedin.run()
    session.assert_called_once()


def test_run_flags_a_captcha_without_persisting_anything(mocker):
    mocker.patch.object(cu_linkedin, "run_session",
                        return_value=("CAPTCHA_OR_CHALLENGE", 3, 0))
    create = mocker.patch.object(db, "create_job_application")
    cu_linkedin.run()
    create.assert_not_called()


@pytest.mark.parametrize("failure", [
    RuntimeError("anthropic down"),
    ValueError("bad response"),
    KeyError("missing"),
    TimeoutError("x11 wedged"),
])
def test_run_never_raises(mocker, failure):
    mocker.patch.object(cu_linkedin, "run_session", side_effect=failure)
    cu_linkedin.run()


def test_run_survives_a_record_run_failure(mocker):
    mocker.patch.object(cu_linkedin, "run_session", return_value=("[]", 0, 0))
    mocker.patch.object(db, "record_run", side_effect=RuntimeError("supabase down"))
    cu_linkedin.run()
