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


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")


@pytest.fixture
def run(mocker, token):
    return mocker.patch("claude_subscription.subprocess.run", return_value=_completed(_ok_payload()))


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


def test_prompt_goes_on_stdin_not_argv(run):
    claude_subscription.complete("SECRET-PROMPT-BODY")
    assert run.call_args.kwargs["input"] == "SECRET-PROMPT-BODY"
    assert "SECRET-PROMPT-BODY" not in run.call_args.args[0]


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

    def fake_run(argv, **kwargs):
        seen["cwd"], seen["cfg"] = kwargs["cwd"], kwargs["env"]["CLAUDE_CONFIG_DIR"]
        seen["cwd_entries"], seen["cfg_entries"] = os.listdir(seen["cwd"]), os.listdir(seen["cfg"])
        return _completed(_ok_payload())

    mocker.patch("claude_subscription.subprocess.run", side_effect=fake_run)
    claude_subscription.complete("hi")
    assert seen["cwd_entries"] == [] and seen["cfg_entries"] == []
    assert not os.path.exists(seen["cwd"]) and not os.path.exists(seen["cfg"])


def test_missing_token_raises_before_spawning(mocker, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    run = mocker.patch("claude_subscription.subprocess.run")
    with pytest.raises(claude_subscription.ClaudeSubscriptionError, match="setup-token"):
        claude_subscription.complete("hi")
    run.assert_not_called()


# ── Output parsing ─────────────────────────────────────────────────────────────

def test_returns_text_and_usage_with_cache_tokens_folded_into_input(run):
    text, usage = claude_subscription.complete("hi")
    assert text == "hello"
    assert usage == {"input_tokens": 3 + 100 + 20, "output_tokens": 7}


def test_is_error_raises(mocker, token):
    payload = json.dumps({"is_error": True, "result": "Invalid model", "usage": {}})
    mocker.patch("claude_subscription.subprocess.run", return_value=_completed(payload))
    with pytest.raises(claude_subscription.ClaudeSubscriptionError, match="Invalid model"):
        claude_subscription.complete("hi")


def test_nonzero_exit_with_garbage_stdout_raises(mocker, token):
    mocker.patch("claude_subscription.subprocess.run",
                 return_value=_completed("not json", returncode=1, stderr="boom"))
    with pytest.raises(claude_subscription.ClaudeSubscriptionError, match="boom"):
        claude_subscription.complete("hi")


def test_runs_in_its_own_process_group_so_a_timeout_kills_children(run):
    claude_subscription.complete("hi")
    assert run.call_args.kwargs["start_new_session"] is True


def test_timeout_raises_subscription_error(mocker, token):
    mocker.patch("claude_subscription.subprocess.run",
                 side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=300))
    with pytest.raises(claude_subscription.ClaudeSubscriptionError, match="timed out"):
        claude_subscription.complete("hi")


def test_missing_binary_raises_subscription_error(mocker, token):
    mocker.patch("claude_subscription.subprocess.run", side_effect=FileNotFoundError("claude"))
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
    mocker.patch("claude_subscription.subprocess.run", return_value=_completed(payload, returncode=1))
    with pytest.raises(claude_subscription.ClaudeUsageLimitError):
        claude_subscription.complete("hi")


def test_ordinary_error_is_not_a_usage_limit(mocker, token):
    payload = json.dumps({"is_error": True, "result": "Invalid model name", "usage": {}})
    mocker.patch("claude_subscription.subprocess.run", return_value=_completed(payload, returncode=1))
    with pytest.raises(claude_subscription.ClaudeSubscriptionError) as exc:
        claude_subscription.complete("hi")
    assert not isinstance(exc.value, claude_subscription.ClaudeUsageLimitError)


def test_result_text_is_sanitized(mocker, token):
    mocker.patch("claude_subscription.subprocess.run",
                 return_value=_completed(_ok_payload(result="Dear​ Ana, hi‮")))
    text, _ = claude_subscription.complete("hi")
    assert text == "Dear Ana, hi"


# ── sanitize ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,clean", [
    ("a​b", "ab"),            # zero-width space
    ("a‍‌b", "ab"),      # zero-width joiner / non-joiner
    ("﻿start", "start"),      # byte-order mark
    ("a‮b⁦c", "abc"),    # bidi overrides / isolates
    ("co­op", "coop"),        # soft hyphen
    ("a b c", "a b c"),  # non-breaking spaces become plain spaces
    ("line1\nline2\tx", "line1\nline2\tx"),
    ("café • 50%", "café • 50%"),
])
def test_sanitize(raw, clean):
    assert claude_subscription.sanitize(raw) == clean
