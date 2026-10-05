"""Tests for claude_subscription.py. subprocess.run is always mocked -- the real `claude` binary
never runs in the suite."""

import json
import os
import subprocess

import pytest

import claude_subscription
import config


def _ok_payload(result="hello", **usage):
    u = {"input_tokens": 3, "output_tokens": 7,
         "cache_creation_input_tokens": 100, "cache_read_input_tokens": 20}
    u.update(usage)
    return json.dumps({"type": "result", "subtype": "success", "is_error": False,
                       "result": result, "usage": u, "total_cost_usd": 0.5})


def _completed(stdout, returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class FakePopen:
    def __init__(self, stdout, returncode=0, stderr="", timeout_after_call=False):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.timeout_after_call = timeout_after_call
        self.pid = 12345
        self._communicate_count = 0

    def communicate(self, input=None, timeout=None):
        self._communicate_count += 1
        # Timeout only on the first call with a timeout argument
        if self.timeout_after_call and self._communicate_count == 1 and timeout is not None:
            raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)
        return self.stdout, self.stderr


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")


@pytest.fixture
def run(mocker, token):
    fake_popen = FakePopen(_ok_payload())
    return mocker.patch("claude_subscription.subprocess.Popen", return_value=fake_popen)


# ── Invocation ─────────────────────────────────────────────────────────────────

def test_argv_strips_every_context_source_and_never_uses_bare(run):
    claude_subscription.complete("hi", model="claude-sonnet-4-6")
    argv = run.call_args.args[0]
    assert argv[0] == config.CLAUDE_CLI_PATH
    assert "-p" in argv and "--bare" not in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in argv
    assert "--no-session-persistence" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--model") + 1] == "claude-sonnet-4-6"


def test_prompt_goes_on_stdin_not_argv(mocker, token):
    fake_popen = FakePopen(_ok_payload())
    popen_mock = mocker.patch("claude_subscription.subprocess.Popen", return_value=fake_popen)
    communicate_mock = mocker.patch.object(fake_popen, "communicate", return_value=(_ok_payload(), ""))
    claude_subscription.complete("SECRET-PROMPT-BODY")
    communicate_mock.assert_called_once()
    assert communicate_mock.call_args.kwargs["input"] == "SECRET-PROMPT-BODY"
    assert "SECRET-PROMPT-BODY" not in popen_mock.call_args.args[0]


def test_system_prompt_replaces_the_default(run):
    claude_subscription.complete("hi", system="Be terse.")
    argv = run.call_args.args[0]
    assert argv[argv.index("--system-prompt") + 1] == "Be terse."


def test_child_env_isolates_config_and_keeps_the_token(run, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
    claude_subscription.complete("hi")
    env = run.call_args.kwargs["env"]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "test-token"
    assert env["DISABLE_AUTOUPDATER"] == "1"
    assert env["PATH"] == "/usr/bin" and env["HTTPS_PROXY"] == "http://proxy:3128"
    assert env["CLAUDE_CONFIG_DIR"] != run.call_args.kwargs["cwd"]


@pytest.mark.parametrize("var", [
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "AWS_ACCESS_KEY_ID", "SUPABASE_ANON_KEY", "GMAIL_APP_PASSWORD", "NODE_OPTIONS",
])
def test_child_env_never_inherits_a_conflicting_or_secret_variable(run, monkeypatch, var):
    monkeypatch.setenv(var, "must-not-leak")
    claude_subscription.complete("hi")
    assert var not in run.call_args.kwargs["env"]


def test_cwd_and_config_dir_are_empty_and_removed(mocker, token):
    seen = {}

    def fake_popen(argv, **kwargs):
        seen["cwd"], seen["cfg"] = kwargs["cwd"], kwargs["env"]["CLAUDE_CONFIG_DIR"]
        seen["cwd_entries"], seen["cfg_entries"] = os.listdir(seen["cwd"]), os.listdir(seen["cfg"])
        fake = FakePopen(_ok_payload())
        return fake

    mocker.patch("claude_subscription.subprocess.Popen", side_effect=fake_popen)
    claude_subscription.complete("hi")
    assert seen["cwd_entries"] == [] and seen["cfg_entries"] == []
    assert not os.path.exists(seen["cwd"]) and not os.path.exists(seen["cfg"])


def test_missing_token_raises_before_spawning(mocker, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    popen = mocker.patch("claude_subscription.subprocess.Popen")
    with pytest.raises(claude_subscription.ClaudeSubscriptionError, match="setup-token"):
        claude_subscription.complete("hi")
    popen.assert_not_called()


# ── Output parsing ─────────────────────────────────────────────────────────────

def test_returns_text_and_usage_with_cache_tokens_folded_into_input(run):
    text, usage = claude_subscription.complete("hi")
    assert text == "hello"
    assert usage == {"input_tokens": 3 + 100 + 20, "output_tokens": 7}


def test_is_error_raises(mocker, token):
    payload = json.dumps({"is_error": True, "result": "Invalid model", "usage": {}})
    fake_popen = FakePopen(payload, returncode=1)
    mocker.patch("claude_subscription.subprocess.Popen", return_value=fake_popen)
    with pytest.raises(claude_subscription.ClaudeSubscriptionError, match="Invalid model"):
        claude_subscription.complete("hi")


def test_nonzero_exit_with_garbage_stdout_raises(mocker, token):
    fake_popen = FakePopen("not json", returncode=1, stderr="boom")
    mocker.patch("claude_subscription.subprocess.Popen", return_value=fake_popen)
    with pytest.raises(claude_subscription.ClaudeSubscriptionError, match="boom"):
        claude_subscription.complete("hi")


def test_runs_in_its_own_process_group_so_a_timeout_kills_children(run, mocker):
    claude_subscription.complete("hi")
    assert run.call_args.kwargs["start_new_session"] is True
    # Verify killpg would be called on timeout (tested separately in timeout test)


def test_timeout_raises_subscription_error(mocker, token):
    fake_popen = FakePopen(_ok_payload(), timeout_after_call=True)
    popen_mock = mocker.patch("claude_subscription.subprocess.Popen", return_value=fake_popen)
    killpg_mock = mocker.patch("claude_subscription.os.killpg")
    with pytest.raises(claude_subscription.ClaudeSubscriptionError, match="timed out"):
        claude_subscription.complete("hi")
    killpg_mock.assert_called_once_with(fake_popen.pid, mocker.ANY)


def test_missing_binary_raises_subscription_error(mocker, token):
    mocker.patch("claude_subscription.subprocess.Popen", side_effect=FileNotFoundError("claude"))
    with pytest.raises(claude_subscription.ClaudeSubscriptionError, match="could not run"):
        claude_subscription.complete("hi")


@pytest.mark.parametrize("message", [
    "Claude AI usage limit reached|1760000000",
    "You've hit your usage limit",
    "API Error: 429 rate_limit_error",
    "API Error: 529 Overloaded",
])
def test_retry_later_messages_raise_usage_limit_error(mocker, token, message):
    payload = json.dumps({"is_error": True, "result": message, "usage": {}})
    fake_popen = FakePopen(payload, returncode=1)
    mocker.patch("claude_subscription.subprocess.Popen", return_value=fake_popen)
    with pytest.raises(claude_subscription.ClaudeUsageLimitError):
        claude_subscription.complete("hi")


def test_ordinary_error_is_not_a_usage_limit(mocker, token):
    payload = json.dumps({"is_error": True, "result": "Invalid model name", "usage": {}})
    fake_popen = FakePopen(payload, returncode=1)
    mocker.patch("claude_subscription.subprocess.Popen", return_value=fake_popen)
    with pytest.raises(claude_subscription.ClaudeSubscriptionError) as exc:
        claude_subscription.complete("hi")
    assert not isinstance(exc.value, claude_subscription.ClaudeUsageLimitError)


def test_result_text_is_sanitized(mocker, token):
    fake_popen = FakePopen(_ok_payload(result="Dear​ Ana, hi‮"))
    mocker.patch("claude_subscription.subprocess.Popen", return_value=fake_popen)
    text, _ = claude_subscription.complete("hi")
    assert text == "Dear Ana, hi"


# ── sanitize ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,clean", [
    ("a​b", "ab"),            # zero-width space
    ("a‍‌b", "ab"),      # zero-width joiner / non-joiner
    ("﻿start", "start"),      # byte-order mark
    ("a‮b⁦c", "abc"),    # bidi overrides / isolates
    ("co\xadop", "coop"),        # soft hyphen
    ("a\xa0b c d", "a b c d"),  # non-breaking spaces become plain spaces
    ("line1\nline2\tx", "line1\nline2\tx"),
    ("café • 50%", "café • 50%"),
])
def test_sanitize(raw, clean):
    assert claude_subscription.sanitize(raw) == clean
