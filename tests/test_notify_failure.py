"""Tests for notify_failure.py -- the GitHub-Actions and systemd failure-email paths."""

from unittest.mock import MagicMock

import notify_failure


_GITHUB_ENV_VARS = (
    "GITHUB_WORKFLOW", "GITHUB_RUN_ID", "GITHUB_REPOSITORY",
    "GITHUB_SERVER_URL", "GITHUB_REF_NAME",
)


def _clear_failure_context(monkeypatch):
    monkeypatch.delenv("FAILED_UNIT", raising=False)
    for var in _GITHUB_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _mock_smtp(mocker):
    smtp_instance = MagicMock()
    mocker.patch.object(notify_failure.smtplib, "SMTP_SSL", return_value=smtp_instance)
    return smtp_instance


def test_github_actions_path_uses_real_env_vars(mocker, monkeypatch):
    _clear_failure_context(monkeypatch)
    monkeypatch.setenv("GITHUB_WORKFLOW", "Cold Email Agent")
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_REPOSITORY", "kishoretheeraj/cold-emailing-agent")
    monkeypatch.setenv("GITHUB_REF_NAME", "main")

    smtp = _mock_smtp(mocker)
    notify_failure.main()

    sent_msg = smtp.__enter__.return_value.send_message.call_args.args[0]
    assert sent_msg["Subject"] == "[FAILED] Cold Email Agent | run 12345"
    body = sent_msg.get_content()
    assert "Repo:   kishoretheeraj/cold-emailing-agent" in body
    assert "Branch: main" in body
    assert "Run:    https://github.com/kishoretheeraj/cold-emailing-agent/actions/runs/12345" in body


def test_github_actions_path_without_any_context_falls_back_to_placeholders(mocker, monkeypatch):
    """Regression guard for the original, pre-FAILED_UNIT behavior: a totally
    contextless invocation (no GITHUB_* vars, no FAILED_UNIT) still sends an
    email rather than crashing -- it just can't say more than '?'."""
    _clear_failure_context(monkeypatch)

    smtp = _mock_smtp(mocker)
    notify_failure.main()

    sent_msg = smtp.__enter__.return_value.send_message.call_args.args[0]
    assert sent_msg["Subject"] == "[FAILED] Cold Email Agent | run ?"
    body = sent_msg.get_content()
    assert "Repo:   ?" in body
    assert "Branch: ?" in body


def test_failed_unit_path_reports_unit_and_host_not_placeholders(mocker, monkeypatch):
    """The bug this guards: the Beelink's notify-failure@.service used to send
    this exact placeholder-only email for every systemd failure, since
    FAILED_UNIT was never read. With FAILED_UNIT set, the email must name the
    actual failed unit and never fall back to GitHub-Actions '?' placeholders."""
    _clear_failure_context(monkeypatch)
    monkeypatch.setenv("FAILED_UNIT", "xvfb@0.service")
    mocker.patch.object(notify_failure.socket, "gethostname", return_value="beelink")

    smtp = _mock_smtp(mocker)
    notify_failure.main()

    sent_msg = smtp.__enter__.return_value.send_message.call_args.args[0]
    assert sent_msg["Subject"] == "[FAILED] xvfb@0.service on beelink"
    body = sent_msg.get_content()
    assert "xvfb@0.service" in body
    assert "beelink" in body
    assert "journalctl -u xvfb@0.service" in body
    assert "?" not in body


def test_failed_unit_takes_priority_over_github_env_vars(mocker, monkeypatch):
    """FAILED_UNIT is the Beelink's own signal and must win even if stray
    GITHUB_* vars are somehow present in that environment."""
    _clear_failure_context(monkeypatch)
    monkeypatch.setenv("FAILED_UNIT", "job-linkedin-ingest.service")
    monkeypatch.setenv("GITHUB_WORKFLOW", "should not be used")
    mocker.patch.object(notify_failure.socket, "gethostname", return_value="beelink")

    smtp = _mock_smtp(mocker)
    notify_failure.main()

    sent_msg = smtp.__enter__.return_value.send_message.call_args.args[0]
    assert "should not be used" not in sent_msg["Subject"]
    assert "job-linkedin-ingest.service" in sent_msg["Subject"]


def test_main_logs_in_with_gmail_credentials(mocker, monkeypatch):
    _clear_failure_context(monkeypatch)
    smtp = _mock_smtp(mocker)
    notify_failure.main()

    smtp.__enter__.return_value.login.assert_called_once_with(
        notify_failure.os.environ["GMAIL_ADDRESS"],
        notify_failure.os.environ["GMAIL_APP_PASSWORD"],
    )


def test_failed_unit_with_escaped_name_quotes_journal_hint(mocker, monkeypatch):
    """systemd escapes odd characters in unit names as '\\xNN'. Pasted unquoted
    into a shell, the backslash is eaten and journalctl queries the wrong unit,
    so the hint must be shell-quoted. The subject keeps the raw name."""
    _clear_failure_context(monkeypatch)
    monkeypatch.setenv("FAILED_UNIT", "foo\\x20bar.service")
    mocker.patch.object(notify_failure.socket, "gethostname", return_value="beelink")

    smtp = _mock_smtp(mocker)
    notify_failure.main()

    sent_msg = smtp.__enter__.return_value.send_message.call_args.args[0]
    assert "foo\\x20bar.service" in sent_msg["Subject"]
    body = sent_msg.get_content()
    assert "journalctl -u 'foo\\x20bar.service'" in body
