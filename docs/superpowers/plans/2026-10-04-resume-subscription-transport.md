# Resume Generation on the Claude Subscription Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `resume_agent.py`'s Claude calls run on Kishore's Claude subscription via `claude -p` (not the pay-as-you-go API key), the automatic strong-verdict path moves from GitHub Actions to a Beelink systemd worker, and the resume/cover-letter PDFs carry no tool attribution, invisible characters, LibreOffice/pikepdf metadata, or LibreOffice-only fonts.

**Architecture:** A new self-contained `claude_subscription.py` shells out to Claude Code headless with every context source stripped (empty cwd, empty `CLAUDE_CONFIG_DIR`, `--strict-mcp-config --setting-sources ""`, no tools, `ANTHROPIC_API_KEY` removed from the child env) and returns `(text, usage)` exactly like today's `_call_claude`. `resume_agent` dispatches on `config.RESUME_CLAUDE_BACKEND`, gains a `--drain` worker mode the Beelink timer runs, and records subscription usage as `billing='subscription'`, `$0`. `resume_build`/`resume_scrub` fix the font and metadata leaks found while designing this.

**Tech Stack:** Python 3.11 (CI) / 3.13 (Beelink), Claude Code CLI 2.1.x, python-docx, pikepdf 10, LibreOffice headless, Supabase (PostgREST + SQL migrations), systemd.

**Spec:** `docs/superpowers/specs/2026-10-04-resume-subscription-transport-design.md`

**Reviews folded in:** `docs/reviews/2026-10-04-resume-subscription-transport-review.md` (finding 1 accepted; 2 and 4 already covered; 3 rejected: `claude` is a native binary on both machines) and `docs/reviews/2026-10-04-resume-subscription-independent-review.md` (findings 1-4 and 6 accepted, 5 partly; see "Known limitations").

## Global Constraints

- Never pass `--bare` to `claude` (it ignores OAuth; the subscription login would silently not apply).
- The child process env is an explicit **allowlist** (OS basics, locale, proxy/CA settings, `CLAUDE_CODE_OAUTH_TOKEN`, plus the two vars the module sets). Never inherited wholesale: no `ANTHROPIC_*` credential/endpoint override, no `CLAUDE_CODE_USE_BEDROCK/VERTEX/FOUNDRY`, no app secrets.
- `config.RESUME_CLAUDE_BACKEND` is read from the `RESUME_CLAUDE_BACKEND` env var and **defaults to `"api"`**. Only `resume-worker.service` sets `subscription`; GitHub Actions switches by a deliberate workflow edit in Task 10, after the worker is proven.
- `billing='subscription'` records the auth route, not a guarantee of no charge. Task 10 requires the operator to verify paid usage credits are off before the worker runs unattended.
- `CLAUDE_CODE_OAUTH_TOKEN` is never written by code or by an agent; the operator puts it in `/etc/job-agent/claude.env` (root:root 0600) or their own shell. Only `resume-worker.service` loads that file.
- No watermark-removal step, tool, or prompt is added anywhere (spec, "Verified facts"). The cover letter stays Claude-written (option 3).
- Allowed attribution words: "Claude", "Claude Code", "Anthropic" (operator's own skills, and a possible target company).
- `config.RESUME_FONT_NAME` (`"Calibri"`) is the only font family allowed in a built PDF.
- `job_pick.py`'s judge and every non-resume Claude call stay on the API key.
- Repo conventions (CLAUDE.md): no type annotations, `# ── Section ──` banners, no docstrings on `_private` helpers, log format `[MARKER] | ... | ...`, every change ships with tests, outbound calls mocked in tests.
- Migration timestamp `20261005000000` (sorts after PR #11's live `20261004*` migrations).
- Work on branch `feat/resume-subscription`; open a PR; do not push straight to `main`.

## Known limitations (accepted, not built)

- **No claim/lease on resume builds.** `resume-worker.service` is a oneshot timer job (never overlaps itself) and runs under `PrivateTmp`; a manual `--build` of the same row on the Mac at the same moment would upload twice to the same storage paths, last writer wins, and `set_resume_files` bumps `documents_version`, which invalidates any approval (the safe direction). Single operator; revisit if a second worker host is added.
- **A per-row Claude CLI error (e.g. prompt too long) stops drain on the same oldest row every run.** `OnFailure` fires every 30 min, so it is loud, not silent.
- **No per-row retry counter.** Machine-wide failures stop the run without marking rows (Task 6); only per-row content failures set `resume_error`, which the operator clears to retry.

## Review Focus

1. **Migration applied after the code merges** → every subscription `api_usage_log` insert fails. Expected: API-billed rows keep working regardless (the `billing` key is only sent when it isn't `"api"`). Pinned by `test_log_api_usage_omits_billing_for_api_rows` in Task 2.
2. **Exact usage-limit wording from the CLI is unverified.** Expected: anything that looks like a limit/overload is retried next run, not marked `resume_error` forever. Pinned by the parametrized `test_retry_later_messages_raise_usage_limit_error` in Task 1; Task 10 records the real wording when first seen.
3. **A drain row that fails before `propose` writes anything** (e.g. deadline passed) must not be retried every 30 minutes. Pinned by `test_drain_marks_deadline_passed_rows` in Task 6.
4. **Cover letter whose first line legitimately starts with "Here"** ("Here at Acme…" is unlikely, but possible). Expected: one regeneration with the violation listed, not a silent ship or a crash. Pinned by `test_lint_cover_letter_flags_chat_preamble_so_build_regenerates` in Task 3 (build's existing one-retry loop does the regeneration).
5. **pikepdf restamping the producer on save** (a live bug today: every scrubbed PDF says `pikepdf 10.12.0`). Pinned by `test_scrub_leaves_no_pikepdf_or_libreoffice_trace` in Task 5.

---

### Task 1: `claude_subscription.py` — the subscription transport

**Files:**
- Create: `claude_subscription.py`
- Modify: `config.py` (Resume intelligence section, after `RESUME_MODEL_COST_PER_MTOK_OUTPUT`)
- Test: `tests/test_claude_subscription.py`

**Interfaces:**
- Produces: `claude_subscription.complete(prompt, system=None, model=None) -> (str, {"input_tokens": int, "output_tokens": int})`; `claude_subscription.sanitize(text) -> str`; exceptions `ClaudeSubscriptionError`, `ClaudeUsageLimitError(ClaudeSubscriptionError)`; config `CLAUDE_CLI_PATH`, `CLAUDE_CLI_TIMEOUT_SECONDS`.

- [ ] **Step 1: Add config constants**

In `config.py`, directly after `RESUME_MODEL_COST_PER_MTOK_OUTPUT = 15.0`:

```python
# "subscription": resume generation runs on the operator's Claude subscription through Claude
# Code's headless mode (claude_subscription.py). "api": the pay-as-you-go anthropic client.
# Defaults to "api" so merging changes nothing; only resume-worker.service sets "subscription",
# and GitHub Actions opts in by setting it in jobright_pull.yml once the worker is proven.
RESUME_CLAUDE_BACKEND = os.environ.get("RESUME_CLAUDE_BACKEND", "api")
CLAUDE_CLI_PATH = os.environ.get("CLAUDE_CLI_PATH", "claude")
CLAUDE_CLI_TIMEOUT_SECONDS = 300
RESUME_WORKER_BATCH = 3
```

(`os` is already imported in `config.py`; confirm with `grep -n "^import os" config.py`.)

- [ ] **Step 2: Write the failing tests**

Create `tests/test_claude_subscription.py`:

```python
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
                 return_value=_completed(_ok_payload(result="Dear\u200b Ana,\u00a0hi\u202e")))
    text, _ = claude_subscription.complete("hi")
    assert text == "Dear Ana, hi"


# ── sanitize ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,clean", [
    ("a\u200bb", "ab"),            # zero-width space
    ("a\u200d\u200cb", "ab"),      # zero-width joiner / non-joiner
    ("\ufeffstart", "start"),      # byte-order mark
    ("a\u202eb\u2066c", "abc"),    # bidi overrides / isolates
    ("co\u00adop", "coop"),        # soft hyphen
    ("a\u00a0b\u202fc", "a b c"),  # non-breaking spaces become plain spaces
    ("line1\nline2\tx", "line1\nline2\tx"),
    ("café • 50%", "café • 50%"),
])
def test_sanitize(raw, clean):
    assert claude_subscription.sanitize(raw) == clean
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_claude_subscription.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'claude_subscription'`.

- [ ] **Step 4: Implement the module**

Create `claude_subscription.py`:

```python
"""
Runs one Claude completion on the operator's Claude subscription through Claude Code's headless
mode (`claude -p`), instead of the pay-as-you-go Messages API. Subscription OAuth tokens are only
valid through Claude Code / the Agent SDK, never against the Messages API directly.

Every call strips all ambient context: an empty working directory and an empty CLAUDE_CONFIG_DIR
(so no CLAUDE.md, memory, plugins, hooks or keychain login), --strict-mcp-config and
--setting-sources "" (no MCP servers, no settings), and --tools "" (text only). Measured
2026-10-04 on the operator's Mac: without these a one-word call loaded ~224K context tokens; with
them ~6.5K. The child env is an allowlist (_ENV_ALLOW): Claude Code bills an API key, an alternate auth
token, a custom endpoint or a cloud provider in preference to the subscription whenever one is
configured. Never pass --bare: it ignores OAuth. start_new_session puts the CLI in its own process
group so a timeout can't leave children running.

Auth is CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`), which the caller's environment must
already hold. Raises on every failure -- resume_agent's pipeline is raise-on-failure.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata

import config


class ClaudeSubscriptionError(Exception):
    """Any failure running Claude Code on the subscription."""


class ClaudeUsageLimitError(ClaudeSubscriptionError):
    """The subscription's usage window is exhausted, or the service is rate-limited or
    overloaded. Transient: the caller should retry on a later run rather than record a failure."""


# ── Text hygiene ───────────────────────────────────────────────────────────────

_SPACE_LIKE = {"\u00a0": " ", "\u202f": " ", "\u2007": " "}


def sanitize(text):
    """Remove invisible format/control characters (zero-width, bidi controls, BOM, soft hyphen)
    and turn non-breaking spaces into plain spaces. Newlines and tabs are kept."""
    out = []
    for ch in text:
        ch = _SPACE_LIKE.get(ch, ch)
        if ch in "\n\t" or unicodedata.category(ch) not in ("Cf", "Cc"):
            out.append(ch)
    return "".join(out)


# ── CLI invocation ─────────────────────────────────────────────────────────────

_DEFAULT_SYSTEM = "You are a careful writing assistant. Reply with only what was asked for."
_RETRY_LATER = re.compile(r"usage limit|limit reached|rate.?limit|overloaded|\b429\b|\b529\b", re.I)


def _argv(model, system):
    return [
        config.CLAUDE_CLI_PATH, "-p",
        "--output-format", "json",
        "--tools", "",
        "--no-session-persistence",
        "--strict-mcp-config",
        "--setting-sources", "",
        "--model", model,
        "--system-prompt", system or _DEFAULT_SYSTEM,
    ]


# An allowlist, not a denylist: Claude Code honours alternate credentials (ANTHROPIC_API_KEY,
# ANTHROPIC_AUTH_TOKEN), endpoint overrides (ANTHROPIC_BASE_URL) and provider switches
# (CLAUDE_CODE_USE_BEDROCK/VERTEX/FOUNDRY), any of which would silently route around the
# subscription. It also keeps this process's app secrets (Supabase, Gmail) out of the CLI.
_ENV_ALLOW = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "TZ",
              "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
              "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "CLAUDE_CODE_OAUTH_TOKEN")


def _child_env(config_dir):
    env = {k: v for k, v in os.environ.items() if k in _ENV_ALLOW or k.startswith("LC_")}
    env["CLAUDE_CONFIG_DIR"] = config_dir
    env["DISABLE_AUTOUPDATER"] = "1"
    return env


def _parse(proc):
    try:
        payload = json.loads(proc.stdout)
    except (TypeError, ValueError):
        payload = None
    if proc.returncode != 0 or not isinstance(payload, dict) or payload.get("is_error"):
        detail = ((payload or {}).get("result") if isinstance(payload, dict) else None) \
            or proc.stderr or proc.stdout or ""
        detail = str(detail).strip()[:500]
        if _RETRY_LATER.search(detail):
            raise ClaudeUsageLimitError(detail)
        raise ClaudeSubscriptionError(f"claude CLI failed (exit {proc.returncode}): {detail}")
    u = payload.get("usage") or {}
    usage = {
        "input_tokens": (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                         + u.get("cache_read_input_tokens", 0)),
        "output_tokens": u.get("output_tokens", 0),
    }
    return sanitize(payload.get("result") or ""), usage


def complete(prompt, system=None, model=None):
    """Run one completion on the subscription. Returns (text, usage) with usage =
    {"input_tokens", "output_tokens"}, cache tokens folded into input. Raises
    ClaudeUsageLimitError for retry-later conditions, ClaudeSubscriptionError otherwise."""
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        raise ClaudeSubscriptionError(
            "CLAUDE_CODE_OAUTH_TOKEN is not set -- create one with `claude setup-token`")
    workdir = tempfile.mkdtemp(prefix="claude-cwd-")
    config_dir = tempfile.mkdtemp(prefix="claude-cfg-")
    try:
        try:
            proc = subprocess.run(
                _argv(model or config.RESUME_MODEL, system), input=prompt, capture_output=True,
                text=True, timeout=config.CLAUDE_CLI_TIMEOUT_SECONDS, cwd=workdir,
                env=_child_env(config_dir), start_new_session=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClaudeSubscriptionError(f"claude CLI timed out after {exc.timeout}s") from exc
        except OSError as exc:
            raise ClaudeSubscriptionError(f"could not run claude CLI: {exc}") from exc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        shutil.rmtree(config_dir, ignore_errors=True)
    return _parse(proc)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_claude_subscription.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add claude_subscription.py config.py tests/test_claude_subscription.py
git commit -m "feat(resume): claude_subscription transport for claude -p on the subscription"
```

---

### Task 2: `billing` on the usage ledger (migration + db + usage_tracking)

**Files:**
- Create: `supabase/migrations/20261005000000_resume_subscription_billing_and_error.sql`
- Modify: `db.py:808-819` (`log_api_usage`)
- Modify: `usage_tracking.py:23-35` (`log_usage`)
- Test: `tests/test_resume_subscription_migration.py` (new), `tests/test_usage_tracking.py`

**Interfaces:**
- Produces: `db.log_api_usage(module, action, model, input_tokens, output_tokens, cost_usd, contact_id=None, job_application_id=None, billing="api")`; `usage_tracking.log_usage(module, action, model, usage, contact_id=None, job_application_id=None, billing="api")`; DB columns `api_usage_log.billing`, `job_applications.resume_error` (the latter used in Task 6).

- [ ] **Step 1: Write the migration**

Create `supabase/migrations/20261005000000_resume_subscription_billing_and_error.sql`:

```sql
-- Resume generation moves to the operator's Claude subscription (claude -p on the Beelink).
-- See docs/superpowers/specs/2026-10-04-resume-subscription-transport-design.md.

-- 1. api_usage_log.billing: 'subscription' rows carry real token counts and cost_usd = 0, so the
--    ledger's dollar totals keep meaning "API spend". Existing and API rows default to 'api'.
ALTER TABLE api_usage_log
  ADD COLUMN IF NOT EXISTS billing TEXT NOT NULL DEFAULT 'api'
  CHECK (billing IN ('api', 'subscription'));

-- 2. job_applications.resume_error: set by resume_agent.py --drain when a strong-verdict row's
--    propose/build fails for a non-transient reason, so the Beelink worker stops retrying it
--    against the subscription window. NULL = eligible. Clearing it re-queues the row.
ALTER TABLE job_applications ADD COLUMN IF NOT EXISTS resume_error TEXT;

-- job_applications uses column-level grants for anon (20260925000000), computed once, and
-- authenticated was made to mirror anon as a one-time snapshot (20261004000001). A new column has
-- no privilege for either role unless granted here. The worker writes it with the anon key.
GRANT UPDATE (resume_error) ON job_applications TO anon;
GRANT UPDATE (resume_error) ON job_applications TO authenticated;
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_resume_subscription_migration.py`:

```python
"""Static checks over the resume-subscription migration (no live DB in the suite; Task 10
verifies the grants live)."""

import re
from pathlib import Path

SQL = (Path(__file__).resolve().parent.parent / "supabase" / "migrations"
       / "20261005000000_resume_subscription_billing_and_error.sql").read_text()
CODE = re.sub(r"--[^\n]*", "", SQL)


def test_billing_column_defaults_to_api_and_is_constrained():
    assert re.search(r"ADD COLUMN IF NOT EXISTS billing TEXT NOT NULL DEFAULT 'api'", CODE)
    assert re.search(r"CHECK \(billing IN \('api', 'subscription'\)\)", CODE)


def test_resume_error_is_nullable_text():
    assert re.search(r"ADD COLUMN IF NOT EXISTS resume_error TEXT;", CODE)


def test_resume_error_granted_to_anon_and_authenticated():
    for role in ("anon", "authenticated"):
        assert re.search(rf"GRANT UPDATE \(resume_error\) ON job_applications TO {role};", CODE), role


def test_migration_never_touches_approval_columns():
    for col in ("approved_at", "approved_revision_hash", "preview_revision_hash", "automation_status"):
        assert col not in CODE
```

Append to `tests/test_usage_tracking.py`:

```python
def test_log_usage_subscription_writes_zero_cost_and_billing(mocker):
    log_api_usage = mocker.patch.object(db, "log_api_usage", return_value={"id": 1})
    usage = {"input_tokens": 1_000, "output_tokens": 500}
    usage_tracking.log_usage("resume_agent", "propose", "claude-sonnet-4-6", usage,
                             job_application_id=7, billing="subscription")
    log_api_usage.assert_called_once_with(
        module="resume_agent", action="propose", model="claude-sonnet-4-6",
        input_tokens=1_000, output_tokens=500, cost_usd=0.0,
        contact_id=None, job_application_id=7, billing="subscription",
    )


def test_log_usage_subscription_logs_even_for_an_unpriced_model(mocker):
    log_api_usage = mocker.patch.object(db, "log_api_usage")
    usage_tracking.log_usage("resume_agent", "propose", "some-unpriced-model",
                             {"input_tokens": 1, "output_tokens": 1}, billing="subscription")
    log_api_usage.assert_called_once()


def test_log_api_usage_omits_billing_for_api_rows(mocker):
    insert = mocker.MagicMock()
    client = mocker.MagicMock()
    client.table.return_value.insert = insert
    mocker.patch.object(db, "get_client", return_value=client)
    db.log_api_usage("emailer", "first_touch", "claude-sonnet-4-6", 1, 1, 0.1)
    assert "billing" not in insert.call_args.args[0]
    db.log_api_usage("resume_agent", "propose", "claude-sonnet-4-6", 1, 1, 0.0, billing="subscription")
    assert insert.call_args.args[0]["billing"] == "subscription"
```

Update the existing `test_log_usage_computes_cost_and_calls_db` expectation to include `billing="api"` at the end of the `assert_called_once_with(...)` kwargs.

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_resume_subscription_migration.py tests/test_usage_tracking.py -q`
Expected: the migration tests pass (file exists); the three new usage tests and the updated one FAIL (`unexpected keyword argument 'billing'`).

- [ ] **Step 4: Implement**

`db.py`, replace `log_api_usage`:

```python
def log_api_usage(module, action, model, input_tokens, output_tokens, cost_usd,
                  contact_id=None, job_application_id=None, billing="api"):
    """Insert one row into the system-wide api_usage_log ledger. Raises on failure -- callers
    (usage_tracking.log_usage) are responsible for the best-effort wrapping, since this accessor
    follows the rest of db.py's pattern of surfacing real failures rather than swallowing them."""
    payload = {
        "module": module, "action": action, "model": model,
        "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost_usd,
        "contact_id": contact_id, "job_application_id": job_application_id,
    }
    # 'api' is the column default: leaving it out keeps every existing writer working even before
    # migration 20261005000000 lands.
    if billing != "api":
        payload["billing"] = billing
    result = _retry(lambda: get_client().table("api_usage_log").insert(payload).execute())
    return result.data[0] if result.data else None
```

`usage_tracking.py`, replace `log_usage`:

```python
def log_usage(module, action, model, usage, contact_id=None, job_application_id=None, billing="api"):
    """Compute cost and write one api_usage_log row. Best-effort -- never raises, matching this
    repo's enrichment-never-costs-a-draft posture (the caller is mid-generation; a logging or
    pricing-table gap must not block or fail it). billing='subscription' rows record real tokens
    at $0 -- the subscription, not the API, paid for them."""
    try:
        cost = 0.0 if billing == "subscription" else calculate_cost(
            model, usage["input_tokens"], usage["output_tokens"])
        db.log_api_usage(
            module=module, action=action, model=model,
            input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
            cost_usd=cost, contact_id=contact_id, job_application_id=job_application_id,
            billing=billing,
        )
    except Exception as exc:
        log.warning(f"[USAGE] | {module} | {action} | log_usage failed: {exc}")
```

- [ ] **Step 5: Run tests to verify they pass, plus every caller's tests**

Run: `.venv/bin/python -m pytest tests/test_resume_subscription_migration.py tests/test_usage_tracking.py tests/test_resume_agent.py -q`
Expected: all pass except `test_propose_writes_strategy_to_db`, which now sees `billing="api"` in `log_api_usage`'s kwargs. Add `billing="api"` to that assertion's kwargs; re-run; all pass. Then run `.venv/bin/python -m pytest -q -x -k "usage or emailer or research or monitor"` and fix any other `log_api_usage.assert_called_once_with(...)` expectation the same way.

- [ ] **Step 6: Commit**

```bash
git add supabase/migrations/20261005000000_resume_subscription_billing_and_error.sql db.py usage_tracking.py tests/
git commit -m "feat(usage): billing column on api_usage_log; resume_error column with grants"
```

---

### Task 3: `resume_agent` on the subscription backend, with the attribution scan

**Files:**
- Modify: `resume_agent.py:18-31` (imports), `:50-78` (`_call_claude`, `_track_usage`), `:134-165` (`propose`), `:262-267` (`_lint_cover_letter`)
- Test: `tests/test_resume_agent.py`

**Interfaces:**
- Consumes: `claude_subscription.complete`, `claude_subscription.sanitize` (Task 1); `usage_tracking.log_usage(..., billing=)` (Task 2); `config.RESUME_CLAUDE_BACKEND`.
- Produces: `resume_agent._check_attribution(text) -> list[str]` (violation strings); `_call_claude(prompt, system=None) -> (text, usage)` unchanged signature.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_resume_agent.py` (`claude_subscription` import goes with the other imports at the top):

```python
import claude_subscription


# ── Backend dispatch ───────────────────────────────────────────────────────────

def test_call_claude_subscription_backend_uses_claude_subscription(mocker):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "subscription")
    complete = mocker.patch.object(claude_subscription, "complete", return_value=("txt", _USAGE))
    assert resume_agent._call_claude("p", system="s") == ("txt", _USAGE)
    complete.assert_called_once_with("p", system="s", model=config.RESUME_MODEL)


def test_call_claude_api_backend_sanitizes(mocker):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "api")
    resp = mocker.MagicMock()
    resp.content = [mocker.MagicMock(text="Dear\u200b Ana")]
    resp.usage.input_tokens, resp.usage.output_tokens = 10, 5
    mocker.patch.object(resume_agent._claude.messages, "create", return_value=resp)
    assert resume_agent._call_claude("p") == ("Dear Ana", {"input_tokens": 10, "output_tokens": 5})


def test_call_claude_rejects_unknown_backend(mocker):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "carrier-pigeon")
    with pytest.raises(ValueError, match="RESUME_CLAUDE_BACKEND"):
        resume_agent._call_claude("p")


def test_track_usage_subscription_records_zero_cost_and_billing(mocker):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "subscription")
    record = mocker.patch.object(db, "record_resume_usage")
    log_api = mocker.patch.object(db, "log_api_usage")
    assert resume_agent._track_usage(9, {"input_tokens": 100, "output_tokens": 50}, "propose") == 0.0
    record.assert_called_once_with(9, 100, 50, 0.0)
    assert log_api.call_args.kwargs["billing"] == "subscription"
    assert log_api.call_args.kwargs["cost_usd"] == 0.0


# ── Attribution scan ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Dear team,\nI built things.\nGenerated with Claude",
    "Dear team,\nCo-Authored-By: Claude <noreply@anthropic.com>",
    "Dear team,\nAs an AI, I cannot attend interviews.",
    "Dear team,\nAs a language model I lack opinions.",
    "Here's a cover letter tailored to the role:\nDear team,",
    "Sure! Below is the letter.\nDear team,",
    "Dear team,\nI shipped a thing.\nLet me know if you'd like any changes!",
])
def test_check_attribution_flags(text):
    assert resume_agent._check_attribution(text)


@pytest.mark.parametrize("text", [
    "Dear Anthropic hiring team,\nI shipped Claude Code workflows and the Claude Vision API.\nBest,\nKishore",
    "Dear team,\nMy work on large language model evaluation cut costs 30%.\nBest,\nKishore",
])
def test_check_attribution_allows_operator_facts(text):
    assert resume_agent._check_attribution(text) == []


def test_operator_resume_data_never_trips_the_attribution_scan():
    import json
    import os
    data_dir = os.path.join(os.path.dirname(resume_agent.__file__), "resume", "data")
    for name in os.listdir(data_dir):
        if name.endswith(".json"):
            with open(os.path.join(data_dir, name)) as f:
                text = json.dumps(json.load(f))
            assert resume_agent._check_attribution(text) == [], name


def test_propose_raises_on_attribution_in_strategy(mocker):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "api")
    mocker.patch.object(db, "get_job_application", return_value={
        "id": 1, "company": "Acme", "role": "PM", "posting_snapshot": {}})
    mocker.patch.object(resume_agent, "_call_claude", return_value=(
        '{"section_order": ["Experience"], "cover_letter_angle": "Generated with Claude"}', _USAGE))
    mocker.patch.object(db, "record_resume_usage")
    mocker.patch.object(db, "log_api_usage")
    set_strategy = mocker.patch.object(db, "set_resume_strategy")
    with pytest.raises(resume_agent.LintFailedError, match="generated with"):
        resume_agent.propose(1)
    set_strategy.assert_not_called()


def test_lint_cover_letter_flags_chat_preamble_so_build_regenerates(mocker):
    # build()'s existing retry loop regenerates on any _lint_cover_letter violation, with the
    # violation list appended to the prompt; this pins that a preamble is such a violation.
    mocker.patch.object(resume_lint, "check_cover_letter", return_value=[])
    violations = resume_agent._lint_cover_letter("Here's your cover letter:\nDear team,", "resume text")
    assert any("preamble" in v for v in violations)
```

(`resume_lint` must be imported at the top of the test file if it isn't already: `import resume_lint`.)

Also add `mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "api")` as the first line of the existing `test_propose_writes_strategy_to_db` and `test_propose_strips_markdown_json_fence_before_parsing`, since they assert API-priced cost (the default is already `"api"`, but pin it so a developer's shell env can't flip the test).

Skills group labels are the one piece of Claude-written text that renders on the resume (independent review, finding 6). Constrain them in `_check_skills_governance`; append:

```python
@pytest.mark.parametrize("label,ok", [
    ("Data & Tools", True),
    ("Product, Analytics", True),
    ("AI/ML Platforms", True),
    ("Strategic Leadership And Vision Setting", False),     # > 4 words
    ("Tools I'm great at!", False),                          # disallowed characters
    ("Generated with Claude", False),                        # attribution
])
def test_skills_group_labels_are_short_plain_and_unattributed(label, ok):
    skills = {"spine": ["SQL"], "swap_pool": [], "banned": []}
    strategy = {"skills_groups": [{"label": label, "skills": ["SQL"]}]}
    assert (resume_agent._check_skills_governance(skills, strategy) == []) is ok
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_resume_agent.py -q`
Expected: new tests FAIL (`AttributeError: ... '_check_attribution'`, dispatch not implemented).

- [ ] **Step 3: Implement**

In `resume_agent.py` imports, add `import re` (stdlib block) and `import claude_subscription` (project block, alphabetical before `config`).

Replace the `# ── Claude client ──` section's `_call_claude`, plus `_track_usage`, with:

```python
def _call_claude(prompt, system=None):
    """Returns (text, usage) -- usage is {"input_tokens": int, "output_tokens": int}, consumed by
    _track_usage. config.RESUME_CLAUDE_BACKEND picks the operator's Claude subscription
    (claude_subscription, the default) or the pay-as-you-go API key ("api")."""
    backend = config.RESUME_CLAUDE_BACKEND
    if backend == "subscription":
        return claude_subscription.complete(prompt, system=system, model=config.RESUME_MODEL)
    if backend != "api":
        raise ValueError(f"unknown RESUME_CLAUDE_BACKEND {backend!r} -- use 'subscription' or 'api'")
    kwargs = dict(
        model=config.RESUME_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    if system:
        kwargs["system"] = system
    resp = _claude.messages.create(**kwargs)
    usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
    return claude_subscription.sanitize(resp.content[0].text), usage
```

```python
def _track_usage(application_id, usage, action):
    """Writes to both job_applications' per-application running total (db.record_resume_usage,
    used by the propose/build CLI output) and the system-wide api_usage_log ledger
    (usage_tracking.log_usage, best-effort -- used by cross-cutting cost analytics). Subscription
    calls record real tokens at $0."""
    billing = "subscription" if config.RESUME_CLAUDE_BACKEND == "subscription" else "api"
    cost = 0.0 if billing == "subscription" else _calculate_cost(usage)
    db.record_resume_usage(application_id, usage["input_tokens"], usage["output_tokens"], cost)
    usage_tracking.log_usage(
        "resume_agent", action, config.RESUME_MODEL, usage, job_application_id=application_id,
        billing=billing,
    )
    return cost
```

Add a new section right after `_track_usage`:

```python
# ── Attribution scan ─────────────────────────────────────────────────────────────
# Tool attribution and chat framing that must never reach an employer. "Claude", "Claude Code"
# and "Anthropic" are deliberately absent: they're in the operator's own skills/projects, and
# Anthropic can be the target company.

_ATTRIBUTION_PHRASES = (
    "generated with", "co-authored-by", "as an ai", "as a language model",
    "i'm claude", "i am claude", "noreply@anthropic",
)
_CHAT_PREAMBLE = re.compile(r"^\s*(here's|here is|sure\b|certainly\b|below is)", re.I)
_CHAT_SIGNOFF = re.compile(r"^\s*(let me know|i hope this)", re.I)


def _check_attribution(text):
    lowered = text.lower()
    violations = [f"contains tool attribution '{p}'" for p in _ATTRIBUTION_PHRASES if p in lowered]
    lines = [line for line in text.splitlines() if line.strip()]
    if lines and _CHAT_PREAMBLE.match(lines[0]):
        violations.append(f"starts with a chat preamble: {lines[0][:60]!r}")
    if lines and _CHAT_SIGNOFF.match(lines[-1]):
        violations.append(f"ends with a chat sign-off: {lines[-1][:60]!r}")
    return violations
```

In `_check_skills_governance`, inside the `for group in ...` loop before the skills loop, add:

```python
        label = group.get("label", "")
        if len(label.split()) > 4 or not _LABEL_CHARS.fullmatch(label):
            violations.append(f"skills group label {label!r} must be 1-4 plain words")
        violations += [f"skills group label {label!r}: {v}" for v in _check_attribution(label)]
```

and next to `_ATTRIBUTION_PHRASES`:

```python
# Skills group labels are the only model-written text rendered on the resume itself.
_LABEL_CHARS = re.compile(r"[A-Za-z0-9 &/,+.-]{1,40}")
```

Also add one line to `_STRATEGY_PROMPT`'s skills instructions: `Each skills group label is 1-4 plain words (letters, digits, & / , + . -), e.g. "Data & Tools".`

In `propose`, right after the `strategy = json.loads(...)` try/except, add:

```python
    attribution = _check_attribution(json.dumps(strategy))
    if attribution:
        raise LintFailedError(f"strategy contains tool attribution: {attribution}")
```

In `_lint_cover_letter`, add as the last `violations +=` line:

```python
    violations += _check_attribution(cl_text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_resume_agent.py tests/test_claude_subscription.py -q`
Expected: all pass. If `test_operator_resume_data_never_trips_the_attribution_scan` fails, read which phrase matched in which file and narrow that phrase rather than editing the operator's data.

- [ ] **Step 5: Commit**

```bash
git add resume_agent.py tests/test_resume_agent.py
git commit -m "feat(resume): subscription backend dispatch, \$0 billing, attribution scan"
```

---

### Task 4: Calibri everywhere in the DOCX (no OpenSymbol/Caladea)

**Files:**
- Modify: `resume_build.py` (new `new_document()` + `_force_font_everywhere()` in the helpers section; `build_docx` line ~303 uses it)
- Modify: `resume_agent.py` (`build`: cover letter `Document()` → `resume_build.new_document()`; drop the now-unused `from docx import Document` import if nothing else uses it)
- Test: `tests/test_resume_build.py`

**Interfaces:**
- Produces: `resume_build.new_document() -> docx.Document` with every `w:rFonts` in styles and bullet numbering set to `config.RESUME_FONT_NAME` and no theme-font attributes, and every bullet level's glyph U+2022.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_resume_build.py`:

```python
from docx.oxml.ns import qn


def _all_rfonts(doc):
    elems = list(doc.styles.element.iter(qn("w:rFonts")))
    elems += list(doc.part.numbering_part.element.iter(qn("w:rFonts")))
    return elems


def test_new_document_forces_resume_font_and_drops_theme_fonts():
    doc = resume_build.new_document()
    fonts = _all_rfonts(doc)
    assert fonts
    for f in fonts:
        assert not any(k.endswith("Theme}") or "Theme" in k for k in f.attrib), f.attrib
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            assert f.get(qn(attr)) == config.RESUME_FONT_NAME


def test_new_document_bullets_are_unicode_bullet_in_resume_font():
    doc = resume_build.new_document()
    bullet_levels = [lvl for lvl in doc.part.numbering_part.element.iter(qn("w:lvl"))
                     if lvl.find(qn("w:numFmt")) is not None
                     and lvl.find(qn("w:numFmt")).get(qn("w:val")) == "bullet"]
    assert bullet_levels
    for lvl in bullet_levels:
        assert lvl.find(qn("w:lvlText")).get(qn("w:val")) == "•"
        assert lvl.find(qn("w:rPr")).find(qn("w:rFonts")).get(qn("w:ascii")) == config.RESUME_FONT_NAME


def test_build_docx_output_keeps_the_forced_fonts(tmp_path):
    path = resume_build.build_docx(_STRATEGY, _MASTER, str(tmp_path / "r.docx"))
    doc = Document(path)
    for f in _all_rfonts(doc):
        assert f.get(qn("w:ascii")) == config.RESUME_FONT_NAME
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_resume_build.py -q -k "new_document or forced_fonts"`
Expected: FAIL (`AttributeError: module 'resume_build' has no attribute 'new_document'`).

- [ ] **Step 3: Implement**

In `resume_build.py`, add after `_add_right_tab_stop` (helpers section):

```python
# LibreOffice can't use Word's theme fonts or its Symbol bullet glyph, and substitutes Carlito,
# Caladea and OpenSymbol -- LibreOffice-only font names that end up embedded in the PDF. Pinning
# every style and bullet level to one real installed font keeps the PDF's font list Word-shaped.
_FONT_ATTRS = ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia")


def _pin_rfonts(rfonts):
    for key in [k for k in rfonts.attrib if "Theme" in k or k == qn("w:hint")]:
        del rfonts.attrib[key]
    for attr in _FONT_ATTRS:
        rfonts.set(qn(attr), _FONT_NAME)


def _force_font_everywhere(doc):
    for rfonts in doc.styles.element.iter(qn("w:rFonts")):
        _pin_rfonts(rfonts)
    for lvl in doc.part.numbering_part.element.iter(qn("w:lvl")):
        fmt = lvl.find(qn("w:numFmt"))
        if fmt is None or fmt.get(qn("w:val")) != "bullet":
            continue
        text = lvl.find(qn("w:lvlText"))
        if text is not None:
            text.set(qn("w:val"), "•")
        rpr = lvl.find(qn("w:rPr"))
        if rpr is None:
            rpr = OxmlElement("w:rPr")
            lvl.append(rpr)
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.insert(0, rfonts)
        _pin_rfonts(rfonts)


def new_document():
    """A python-docx Document whose styles and bullet numbering all use config.RESUME_FONT_NAME,
    with theme-font references removed and bullets rendered as U+2022 in that font."""
    doc = Document()
    _force_font_everywhere(doc)
    return doc
```

In `build_docx`, change `doc = Document()` to `doc = new_document()`.

In `resume_agent.build`, change `cl_doc = Document()` to `cl_doc = resume_build.new_document()`, then remove `from docx import Document` from `resume_agent.py` if `grep -n "Document(" resume_agent.py` shows no other use.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_resume_build.py tests/test_resume_agent.py -q`
Expected: all pass. If `doc.styles.element` yields no `w:rFonts` (template drift), `test_new_document_forces_resume_font_and_drops_theme_fonts` fails on `assert fonts`: in that case add an explicit `w:rFonts` to `w:docDefaults/w:rPrDefault/w:rPr` in `_force_font_everywhere` before the loop.

- [ ] **Step 5: Commit**

```bash
git add resume_build.py resume_agent.py tests/test_resume_build.py
git commit -m "fix(resume): pin every style and bullet to Calibri so PDFs embed no LibreOffice fonts"
```

- [ ] **Step 6: Write the failing tests for an isolated LibreOffice profile** (review finding 1, `docs/reviews/2026-10-04-resume-subscription-transport-review.md`)

A conversion killed mid-run (the worker's `MemoryMax`/timeout) leaves LibreOffice's shared user-profile lock behind, and a LibreOffice window already open on the Mac can make a headless `--convert-to` exit 0 without writing anything. Each conversion gets its own throwaway profile, and a missing output file raises.

Append to `tests/test_resume_build.py` (next to `test_convert_to_pdf_calls_soffice_and_returns_pdf_path`):

```python
def test_convert_to_pdf_uses_a_throwaway_profile_and_removes_it(mocker, tmp_path):
    seen = {}

    def fake_run(argv, **kwargs):
        profile_arg = [a for a in argv if a.startswith("-env:UserInstallation=file://")]
        seen["profile"] = profile_arg[0].split("file://", 1)[1] if profile_arg else None
        seen["exists_during"] = seen["profile"] is not None and os.path.isdir(seen["profile"])
        seen["argv"] = argv
        (tmp_path / "resume.pdf").write_bytes(b"%PDF-1.7")
        return MagicMock(returncode=0)

    mocker.patch("resume_build.subprocess.run", side_effect=fake_run)
    out = resume_build.convert_to_pdf(str(tmp_path / "resume.docx"), str(tmp_path))
    assert out == str(tmp_path / "resume.pdf")
    assert seen["exists_during"] is True
    assert not os.path.exists(seen["profile"])
    for flag in ("--headless", "--norestore", "--nolockcheck"):
        assert flag in seen["argv"]


def test_convert_to_pdf_raises_when_no_pdf_was_written(mocker, tmp_path):
    mocker.patch("resume_build.subprocess.run", return_value=MagicMock(returncode=0))
    with pytest.raises(RuntimeError, match="no PDF"):
        resume_build.convert_to_pdf(str(tmp_path / "resume.docx"), str(tmp_path))
```

Update the existing `test_convert_to_pdf_calls_soffice_and_returns_pdf_path` so its fake run writes the expected PDF file (otherwise the new existence check raises), and so it asserts `args[0] == "soffice"` still holds.

- [ ] **Step 7: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_resume_build.py -q -k convert_to_pdf`
Expected: the two new tests FAIL (no `-env:UserInstallation` flag; no existence check).

- [ ] **Step 8: Implement**

In `resume_build.py`, add `import pathlib` and `import tempfile` to the stdlib imports, and replace `convert_to_pdf`:

```python
def convert_to_pdf(docx_path, output_dir):
    """Convert docx_path to PDF via LibreOffice headless. Returns the output PDF path. Raises on
    any failure (missing soffice binary, conversion error, timeout, no PDF written) -- never
    swallowed. Each call uses its own throwaway LibreOffice profile: a conversion killed mid-run
    leaves the shared profile's lock behind, and an already-open LibreOffice window can make a
    headless conversion exit 0 without writing anything."""
    base = os.path.splitext(os.path.basename(docx_path))[0]
    pdf_path = os.path.join(output_dir, base + ".pdf")
    with tempfile.TemporaryDirectory(prefix="soffice-profile-") as profile:
        subprocess.run(
            ["soffice", f"-env:UserInstallation={pathlib.Path(profile).as_uri()}",
             "--headless", "--norestore", "--nolockcheck", "--nologo", "--nodefault",
             "--convert-to", "pdf", "--outdir", output_dir, docx_path],
            check=True, capture_output=True, timeout=config.RESUME_SOFFICE_TIMEOUT_SECONDS,
        )
    if not os.path.exists(pdf_path):
        raise RuntimeError(f"soffice exited cleanly but wrote no PDF at {pdf_path}")
    return pdf_path
```

`config.RESUME_SOFFICE_TIMEOUT_SECONDS` is 30; a fresh profile adds a few seconds of first-run setup per call. Raise it to 90 in `config.py` and note why in its comment.

Also give each `build()` call its own working directory instead of fixed `/tmp/resume_<id>.docx` / `/tmp/cover_letter_<id>.docx` paths: at the top of `build`, `workdir = tempfile.mkdtemp(prefix=f"resume-{application_id}-")`, use `os.path.join(workdir, "resume.docx")` / `"cover_letter.docx"` and `workdir` as the PDF output dir, and wrap the rest of the function in `try: ... finally: shutil.rmtree(workdir, ignore_errors=True)` (uploads read the files before the `finally` runs). Add `import shutil, tempfile` to `resume_agent.py`. Test: assert `fit_to_one_page` is called with a path outside `/tmp/resume_` and that the directory no longer exists after `build` returns or raises.

- [ ] **Step 9: Run tests, then commit**

Run: `.venv/bin/python -m pytest tests/test_resume_build.py tests/test_resume_agent.py -q`
Expected: all pass.

```bash
git add resume_build.py resume_agent.py config.py tests/test_resume_build.py tests/test_resume_agent.py
git commit -m "fix(resume): throwaway LibreOffice profile per conversion; fail when no PDF is written"
```

---

### Task 5: PDF metadata scrub that actually sticks, plus XMP and font checks

**Files:**
- Modify: `resume_scrub.py` (`_FINGERPRINTS`, `scrub_pdf_metadata`; add `read_pdf_xmp_text`, `embedded_font_families`, `check_fonts`)
- Modify: `resume_agent.py` (`build`: both PDF verification blocks)
- Test: `tests/test_resume_scrub.py`, `tests/test_resume_agent.py`

**Interfaces:**
- Produces: `resume_scrub.read_pdf_xmp_text(pdf_path) -> str`; `resume_scrub.embedded_font_families(pdf_path) -> set[str]`; `resume_scrub.check_fonts(pdf_path, allowed) -> list[str]` (violations).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_resume_scrub.py`:

```python
@pytest.fixture
def libreoffice_pdf(tmp_path):
    path = str(tmp_path / "lo.pdf")
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    with pdf.open_metadata() as meta:
        meta["pdf:Producer"] = "LibreOffice 26.8"
        meta["xmp:CreatorTool"] = "Writer"
        meta["xmp:CreateDate"] = "2026-10-04T21:00:00"
    pdf.docinfo["/Producer"] = pikepdf.String("LibreOffice 26.8")
    pdf.save(path)
    return path


def test_scrub_leaves_no_pikepdf_or_libreoffice_trace(libreoffice_pdf):
    resume_scrub.scrub_pdf_metadata(libreoffice_pdf, title="Acme - Resume", keywords="PM")
    xmp = resume_scrub.read_pdf_xmp_text(libreoffice_pdf).lower()
    info = resume_scrub.read_pdf_metadata_text(libreoffice_pdf).lower()
    for trace in ("pikepdf", "libreoffice", "writer", "2026-10-04t21:00:00"):
        assert trace not in xmp, trace
        assert trace not in info, trace
    assert "microsoft: print to pdf" in xmp and "microsoft: print to pdf" in info


def test_scrub_writes_matching_dates_in_xmp_and_docinfo(libreoffice_pdf):
    resume_scrub.scrub_pdf_metadata(libreoffice_pdf, title="Acme - Resume", keywords="PM")
    with pikepdf.open(libreoffice_pdf) as pdf:
        created = str(pdf.docinfo["/CreationDate"])
        modified = str(pdf.docinfo["/ModDate"])
        assert str(pdf.docinfo["/Keywords"]) == "PM"
        with pdf.open_metadata() as meta:
            xmp_created = meta["xmp:CreateDate"]
            xmp_modified = meta["xmp:ModifyDate"]
    assert created[2:10] == xmp_created[:10].replace("-", "")
    assert modified[2:10] == xmp_modified[:10].replace("-", "")
    assert created < modified


def test_verify_no_fingerprints_catches_pikepdf():
    assert resume_scrub.verify_no_fingerprints("Producer pikepdf 10.12.0") == ["pikepdf"]


def _pdf_with_fonts(path, base_fonts):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    fonts = pikepdf.Dictionary()
    for i, name in enumerate(base_fonts):
        fonts[f"/F{i}"] = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.TrueType, BaseFont=pikepdf.Name(name)))
    pdf.pages[0].Resources = pikepdf.Dictionary(Font=fonts)
    pdf.save(path)
    return path


@pytest.mark.parametrize("names", [
    ["/AAAAAA+Calibri"], ["/BAAAAA+Calibri-Bold"], ["/CAAAAA+Calibri-Italic"],
    ["/DAAAAA+Calibri-BoldItalic"], ["/EAAAAA+Calibri,Bold"], ["/Calibri"],
])
def test_embedded_font_families_normalizes_every_face(tmp_path, names):
    assert resume_scrub.embedded_font_families(_pdf_with_fonts(str(tmp_path / "f.pdf"), names)) == {"Calibri"}


def test_embedded_font_families_finds_fonts_nested_in_form_xobjects(tmp_path):
    path = str(tmp_path / "n.pdf")
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    font = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.TrueType, BaseFont=pikepdf.Name("/XYZABC+Carlito")))
    form = pdf.make_stream(b"")
    form.Type, form.Subtype = pikepdf.Name.XObject, pikepdf.Name.Form
    form.BBox = [0, 0, 10, 10]
    form.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    pdf.pages[0].Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm1=form))
    pdf.save(path)
    assert resume_scrub.embedded_font_families(path) == {"Carlito"}


def test_check_fonts_flags_libreoffice_substitutes(tmp_path):
    path = _pdf_with_fonts(str(tmp_path / "f.pdf"),
                           ["/BAAAAA+Carlito-Bold", "/CAAAAA+OpenSymbol", "/EAAAAA+Caladea-Regular",
                            "/FAAAAA+Calibri"])
    violations = resume_scrub.check_fonts(path, allowed={"Calibri"})
    assert sorted(violations) == sorted([
        "embedded font 'Caladea' is not allowed",
        "embedded font 'Carlito' is not allowed",
        "embedded font 'OpenSymbol' is not allowed",
    ])


def test_check_fonts_clean(tmp_path):
    path = _pdf_with_fonts(str(tmp_path / "f.pdf"), ["/AAAAAA+Calibri", "/BBBBBB+Calibri-Bold"])
    assert resume_scrub.check_fonts(path, allowed={"Calibri"}) == []
```

Append to `tests/test_resume_agent.py` a test that `build` refuses to upload a PDF with a bad font. Locate the existing happy-path `build` test (`grep -n "def test_build" tests/test_resume_agent.py`) and copy its mocks into:

```python
def test_build_refuses_to_upload_when_pdf_embeds_a_substitute_font(mocker):
    # Same fixture mocks as the existing happy-path build test, then:
    mocker.patch.object(resume_scrub, "check_fonts", return_value=["embedded font 'Carlito' is not allowed"])
    upload = mocker.patch.object(db, "upload_resume_file")
    with pytest.raises(resume_agent.LintFailedError, match="Carlito"):
        resume_agent.build(1)
    upload.assert_not_called()
```

Add `resume_build.pdf_text(pdf_path)` (pypdf is already imported there for `page_count`): `return "\n".join(page.extract_text() or "" for page in PdfReader(pdf_path).pages)`, with a test that builds a one-page PDF via pikepdf and asserts it returns a string. In the `build` test above, also mock `resume_build.pdf_text` → `"Dear team"`; add a second test where it returns `"Dear\u200b team"` and `build` raises `LintFailedError` matching `invisible`.

(If no happy-path `build` test exists, mock in this order: `db.get_job_application` → a row with a valid `resume_strategy`; `resume_agent._load_data` → small dicts; `resume_build.fit_to_one_page` → `("/tmp/r.pdf", "standard")`; `resume_scrub.scrub_pdf_metadata`; `resume_scrub.read_pdf_metadata_text`/`read_pdf_xmp_text` → `""`; `resume_agent._call_claude` → a clean letter; `resume_build.convert_to_pdf` → `"/tmp/c.pdf"`; `resume_build.new_document` → `MagicMock()`; `resume_agent._track_usage` → `0.0`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_resume_scrub.py tests/test_resume_agent.py -q -k "scrub or font or fingerprint"`
Expected: FAIL (`pikepdf` trace present; `read_pdf_xmp_text`/`embedded_font_families`/`check_fonts` missing).

- [ ] **Step 3: Implement**

In `resume_scrub.py`:

```python
_FINGERPRINTS = ("libreoffice", "soffice", "python-docx", "docx-js", "openoffice", "pikepdf")
```

`"writer"` is deliberately not a fingerprint: it would match a real job title ("Technical Writer - Resume"). The test's `"writer"` trace check still holds because the whole XMP packet is replaced, so LibreOffice's `CreatorTool = Writer` can't survive.

Replace `scrub_pdf_metadata`:

```python
def scrub_pdf_metadata(pdf_path, title, keywords):
    """Overwrite XMP and docinfo metadata on pdf_path in place. Target values match a real
    Microsoft Word export, not a LibreOffice/tool default. The existing XMP packet is deleted
    first so no LibreOffice key or real timestamp survives, and pikepdf's own editor stamp is
    disabled -- left on, it rewrites pdf:Producer to "pikepdf <version>" on save."""
    created = datetime.datetime.now() - datetime.timedelta(days=5)
    modified = datetime.datetime.now()
    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        if "/Metadata" in pdf.Root:
            del pdf.Root.Metadata
        with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
            meta["xmp:CreatorTool"] = "Microsoft Word"
            meta["pdf:Producer"] = "Microsoft: Print To PDF"
            meta["dc:creator"] = ["Kishore Theeraj Vasudevan Jaya"]
            meta["dc:title"] = title
            meta["xmp:CreateDate"] = created.isoformat(timespec="seconds")
            meta["xmp:ModifyDate"] = modified.isoformat(timespec="seconds")
            meta["xmp:MetadataDate"] = modified.isoformat(timespec="seconds")

        for key in list(pdf.docinfo.keys()):
            del pdf.docinfo[key]
        pdf.docinfo["/Creator"] = pikepdf.String("Microsoft Word")
        pdf.docinfo["/Producer"] = pikepdf.String("Microsoft: Print To PDF")
        pdf.docinfo["/Author"] = pikepdf.String("Kishore Theeraj Vasudevan Jaya")
        pdf.docinfo["/Title"] = pikepdf.String(title)
        pdf.docinfo["/Keywords"] = pikepdf.String(keywords)
        pdf.docinfo["/CreationDate"] = pikepdf.String(created.strftime("D:%Y%m%d%H%M%S"))
        pdf.docinfo["/ModDate"] = pikepdf.String(modified.strftime("D:%Y%m%d%H%M%S"))

        pdf.save(pdf_path)
```

Add after `read_pdf_metadata_text`:

```python
def read_pdf_xmp_text(pdf_path):
    """Concatenate a PDF's XMP metadata values into one string, for verify_no_fingerprints --
    docinfo alone misses tool names LibreOffice and pikepdf write into XMP."""
    with pikepdf.open(pdf_path) as pdf:
        with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
            return " ".join(f"{k} {v}" for k, v in meta.items())


def embedded_font_families(pdf_path):
    """Family names of every font object in the file -- page resources, form XObjects, anything
    nested -- with the 6-letter subset prefix and style suffix ("-Bold", ",Italic") removed:
    "/BAAAAA+Calibri-Bold" -> "Calibri"."""
    families = set()
    with pikepdf.open(pdf_path) as pdf:
        for obj in pdf.objects:
            if isinstance(obj, pikepdf.Dictionary) and obj.get("/Type") == pikepdf.Name.Font \
                    and "/BaseFont" in obj:
                base = str(obj.BaseFont).lstrip("/").split("+", 1)[-1]
                families.add(base.split("-")[0].split(",")[0])
    families.discard("")
    return families


def check_fonts(pdf_path, allowed):
    """Return a violation string per embedded font family not in `allowed` (empty = clean)."""
    return [f"embedded font '{name}' is not allowed"
            for name in sorted(embedded_font_families(pdf_path) - set(allowed))]
```

In `resume_agent.build`, replace the resume PDF check

```python
    resume_fingerprints = resume_scrub.verify_no_fingerprints(resume_scrub.read_pdf_metadata_text(pdf_path))
    if resume_fingerprints:
        raise LintFailedError(f"resume PDF metadata still contains fingerprints: {resume_fingerprints}")
```

with a call to a new helper (defined just above `build`):

```python
def _verify_clean_pdf(pdf_path, label):
    meta_text = resume_scrub.read_pdf_metadata_text(pdf_path) + " " + resume_scrub.read_pdf_xmp_text(pdf_path)
    problems = [f"metadata fingerprint '{fp}'" for fp in resume_scrub.verify_no_fingerprints(meta_text)]
    problems += resume_scrub.check_fonts(pdf_path, allowed={config.RESUME_FONT_NAME})
    # The rendered text itself, not just what went in: catches invisible characters from stored
    # strategies or source data, and attribution that slipped past the per-response checks.
    text = resume_build.pdf_text(pdf_path)
    if claude_subscription.sanitize(text) != text.replace("\u00a0", " ").replace("\u202f", " "):
        problems.append("rendered text contains invisible control/format characters")
    problems += [p for p in _check_attribution(text) if "tool attribution" in p]
    if problems:
        raise LintFailedError(f"{label} PDF is not clean: {problems}")
```

```python
    _verify_clean_pdf(pdf_path, "resume")
```

and the cover letter's equivalent block with `_verify_clean_pdf(cl_pdf_path, "cover letter")`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_resume_scrub.py tests/test_resume_agent.py -q`
Expected: all pass. Existing `test_scrub_pdf_metadata_*` tests should still pass; if one asserted pikepdf-era behavior (e.g. reading `pdf:Producer` through a default `open_metadata()`), update it to the new expected Word values, not the reverse.

- [ ] **Step 5: Commit**

```bash
git add resume_scrub.py resume_agent.py tests/test_resume_scrub.py tests/test_resume_agent.py
git commit -m "fix(resume): scrub no longer stamps pikepdf; check XMP and embedded fonts before upload"
```

---

### Task 6: `resume_agent.py --drain` worker mode

**Files:**
- Modify: `db.py` (add two accessors after `set_resume_files`)
- Modify: `resume_agent.py` (new `drain()` before `# ── CLI` / `__main__`; argparse)
- Test: `tests/test_resume_agent.py`, `tests/test_db_resume_queue.py` (new)

**Interfaces:**
- Consumes: `claude_subscription.ClaudeUsageLimitError` (Task 1); `job_applications.resume_error` (Task 2).
- Produces: `db.get_strong_applications_without_resume(limit) -> list[dict]`; `db.set_resume_error(application_id, message) -> dict|None`; `resume_agent.drain(limit=None) -> int` (error count); CLI `python resume_agent.py --drain` (exit 1 when the count is nonzero).

- [ ] **Step 1: Write the failing tests**

Every other `drain` test in this step must also start with `mocker.patch.object(resume_agent, "_worker_preflight", return_value=[])` and `mocker.patch.object(resume_agent, "_check_deadline", return_value=True)`; add those two lines to each.

Create `tests/test_db_resume_queue.py`:

```python
"""db.py accessors for the Beelink resume worker's queue (mock pattern from test_db_draft_history)."""

import db


def _client(mocker, data):
    client = mocker.MagicMock()
    chain = client.table.return_value
    for name in ("select", "eq", "is_", "order", "limit", "update"):
        getattr(chain, name).return_value = chain
    chain.execute.return_value = mocker.MagicMock(data=data)
    mocker.patch.object(db, "get_client", return_value=client)
    return chain


def test_get_strong_applications_without_resume_filters_and_limits(mocker):
    chain = _client(mocker, [{"id": 3}])
    assert db.get_strong_applications_without_resume(2) == [{"id": 3}]
    eq_calls = {c.args for c in chain.eq.call_args_list}
    assert ("pick_verdict", "strong") in eq_calls and ("stage", "saved") in eq_calls
    is_calls = {c.args for c in chain.is_.call_args_list}
    assert ("resume_file_ref", "null") in is_calls and ("resume_error", "null") in is_calls
    chain.order.assert_called_with("created_at", desc=False)
    chain.limit.assert_called_with(2)


def test_set_resume_error_truncates(mocker):
    chain = _client(mocker, [{"id": 3}])
    db.set_resume_error(3, "x" * 5000)
    payload = chain.update.call_args.args[0]
    assert len(payload["resume_error"]) == 1000
    chain.eq.assert_called_with("id", 3)
```

Append to `tests/test_resume_agent.py`:

```python
# ── drain ──────────────────────────────────────────────────────────────────────

def test_drain_runs_propose_then_build_per_row(mocker):
    mocker.patch.object(db, "get_strong_applications_without_resume",
                        return_value=[{"id": 1, "company": "A"}, {"id": 2, "company": "B", "resume_strategy": {"x": 1}}])
    propose = mocker.patch.object(resume_agent, "propose")
    build = mocker.patch.object(resume_agent, "build")
    set_error = mocker.patch.object(db, "set_resume_error")
    assert resume_agent.drain(limit=3) == 0
    propose.assert_called_once_with(1)
    assert [c.args[0] for c in build.call_args_list] == [1, 2]
    set_error.assert_not_called()


def test_drain_records_error_and_continues(mocker):
    mocker.patch.object(db, "get_strong_applications_without_resume",
                        return_value=[{"id": 1, "company": "A"}, {"id": 2, "company": "B"}])
    mocker.patch.object(resume_agent, "propose", side_effect=[ValueError("bad json"), None])
    build = mocker.patch.object(resume_agent, "build")
    set_error = mocker.patch.object(db, "set_resume_error")
    assert resume_agent.drain() == 1
    set_error.assert_called_once()
    assert set_error.call_args.args[0] == 1 and "bad json" in set_error.call_args.args[1]
    build.assert_called_once_with(2)


def test_drain_marks_deadline_passed_rows(mocker):
    mocker.patch.object(db, "get_strong_applications_without_resume", return_value=[{"id": 5, "company": "A"}])
    mocker.patch.object(resume_agent, "propose", side_effect=resume_agent.DeadlinePassedError("passed"))
    set_error = mocker.patch.object(db, "set_resume_error")
    assert resume_agent.drain() == 1
    set_error.assert_called_once()


def test_drain_stops_on_usage_limit_without_marking(mocker):
    mocker.patch.object(db, "get_strong_applications_without_resume",
                        return_value=[{"id": 1, "company": "A"}, {"id": 2, "company": "B"}])
    propose = mocker.patch.object(resume_agent, "propose",
                                  side_effect=claude_subscription.ClaudeUsageLimitError("usage limit reached"))
    set_error = mocker.patch.object(db, "set_resume_error")
    assert resume_agent.drain() == 0
    propose.assert_called_once_with(1)
    set_error.assert_not_called()


def test_drain_survives_a_failing_error_write(mocker):
    mocker.patch.object(db, "get_strong_applications_without_resume", return_value=[{"id": 1, "company": "A"}])
    mocker.patch.object(resume_agent, "propose", side_effect=ValueError("x"))
    mocker.patch.object(db, "set_resume_error", side_effect=RuntimeError("db down"))
    assert resume_agent.drain() == 1


def test_drain_uses_configured_batch_by_default(mocker):
    mocker.patch.object(resume_agent, "_worker_preflight", return_value=[])
    get = mocker.patch.object(db, "get_strong_applications_without_resume", return_value=[])
    resume_agent.drain()
    get.assert_called_once_with(config.RESUME_WORKER_BATCH)


def test_drain_preflight_failure_touches_no_rows(mocker):
    mocker.patch.object(resume_agent, "_worker_preflight", return_value=["CLAUDE_CODE_OAUTH_TOKEN is not set"])
    get = mocker.patch.object(db, "get_strong_applications_without_resume")
    set_error = mocker.patch.object(db, "set_resume_error")
    assert resume_agent.drain() == 1
    get.assert_not_called()
    set_error.assert_not_called()


def test_drain_transport_failure_stops_the_run_without_marking(mocker):
    mocker.patch.object(db, "get_strong_applications_without_resume",
                        return_value=[{"id": 1, "company": "A"}, {"id": 2, "company": "B"}])
    propose = mocker.patch.object(resume_agent, "propose",
                                  side_effect=claude_subscription.ClaudeSubscriptionError("claude CLI failed (exit 1): auth"))
    set_error = mocker.patch.object(db, "set_resume_error")
    assert resume_agent.drain() == 1
    propose.assert_called_once_with(1)
    set_error.assert_not_called()


def test_drain_checks_the_deadline_even_when_a_strategy_exists(mocker):
    mocker.patch.object(db, "get_strong_applications_without_resume",
                        return_value=[{"id": 4, "company": "A", "resume_strategy": {"x": 1}}])
    mocker.patch.object(resume_agent, "_check_deadline", return_value=False)
    build = mocker.patch.object(resume_agent, "build")
    set_error = mocker.patch.object(db, "set_resume_error")
    assert resume_agent.drain() == 1
    build.assert_not_called()
    assert "deadline" in set_error.call_args.args[1].lower()


def test_worker_preflight_reports_missing_prereqs(mocker, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    mocker.patch("resume_agent.shutil.which", return_value=None)
    problems = resume_agent._worker_preflight()
    assert any("CLAUDE_CODE_OAUTH_TOKEN" in p for p in problems)
    assert any("soffice" in p for p in problems)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_db_resume_queue.py tests/test_resume_agent.py -q -k "drain or strong_applications or resume_error"`
Expected: FAIL (missing functions).

- [ ] **Step 3: Implement**

`db.py`, after `set_resume_files`:

```python
def get_strong_applications_without_resume(limit):
    """Rows at stage='saved' that job_pick.py scored 'strong', with no built resume and no
    recorded resume_error, oldest first -- the Beelink resume worker's queue
    (resume_agent.py --drain). Applied/rejected/withdrawn rows are never rebuilt."""
    result = _retry(lambda: get_client().table("job_applications")
                     .select("*").eq("pick_verdict", "strong").eq("stage", "saved")
                     .is_("resume_file_ref", "null").is_("resume_error", "null")
                     .order("created_at", desc=False).limit(limit).execute())
    return result.data or []


def set_resume_error(application_id, message):
    """Record why the resume worker gave up on a row, so it isn't retried every run. Clearing
    the column re-queues the row."""
    result = _retry(lambda: get_client().table("job_applications")
                     .update({"resume_error": str(message)[:1000],
                              "updated_at": datetime.utcnow().isoformat()})
                     .eq("id", application_id).execute())
    return result.data[0] if result.data else None
```

`resume_agent.py`, after `run_build`:

```python
# ── Worker (Beelink resume-worker.service) ─────────────────────────────────────────

def _worker_preflight():
    problems = []
    if config.RESUME_CLAUDE_BACKEND == "subscription" and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        problems.append("CLAUDE_CODE_OAUTH_TOKEN is not set")
    if config.RESUME_CLAUDE_BACKEND == "subscription" and not shutil.which(config.CLAUDE_CLI_PATH):
        problems.append(f"claude CLI not found at {config.CLAUDE_CLI_PATH}")
    if not shutil.which("soffice"):
        problems.append("soffice (LibreOffice) not found")
    return problems


def drain(limit=None):
    """Build resumes for strong-verdict rows that don't have one yet: propose (unless a strategy
    already exists) then build, one row at a time. Machine-wide problems -- a failed preflight,
    a usage limit, or any Claude CLI/auth/timeout failure -- stop the run without marking a row,
    so fixing the machine resumes the queue; a usage limit isn't counted as an error. Per-row
    content failures (bad strategy, lint, deadline passed, page overflow) are written to
    resume_error so the row isn't retried every run. Returns the error count."""
    problems = _worker_preflight()
    if problems:
        log.warning(f"[RESUME] | drain | preflight failed, no rows touched: {problems}")
        return 1
    rows = db.get_strong_applications_without_resume(limit or config.RESUME_WORKER_BATCH)
    log.info(f"[RESUME] | drain | START | rows={len(rows)}")
    built = errors = 0
    for job in rows:
        job_id = job.get("id")
        try:
            if not _check_deadline(job):
                raise DeadlinePassedError(f"row {job_id}'s deadline has passed")
            if not job.get("resume_strategy"):
                propose(job_id)
            build(job_id)
            built += 1
        except claude_subscription.ClaudeUsageLimitError as exc:
            log.warning(f"[RESUME] | {job_id} | {job.get('company')} | usage limit, stopping drain: {exc}")
            break
        except claude_subscription.ClaudeSubscriptionError as exc:
            errors += 1
            log.warning(f"[RESUME] | {job_id} | {job.get('company')} | Claude CLI failure, stopping drain: {exc}")
            break
        except Exception as exc:
            errors += 1
            log.warning(f"[RESUME] | {job_id} | {job.get('company')} | drain failed: {exc}")
            try:
                db.set_resume_error(job_id, f"{type(exc).__name__}: {exc}")
            except Exception as write_exc:
                log.warning(f"[RESUME] | {job_id} | could not record resume_error: {write_exc}")
    log.info(f"[RESUME] | drain | DONE | built={built} | errors={errors}")
    return errors
```

Replace the `__main__` argparse block:

```python
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", type=int)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--propose", action="store_true")
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--drain", action="store_true")
    args = parser.parse_args()
    if (args.propose or args.build) and args.job_id is None:
        parser.error("--job-id is required with --propose/--build")

    if args.propose:
        run_propose(args.job_id)
    elif args.build:
        run_build(args.job_id)
    elif args.drain:
        sys.exit(1 if drain() else 0)
```

Add `import shutil` and `import sys` to the stdlib imports (`shutil` may already be there from Task 4).

Also update the module docstring's Usage block:

```
Usage:
  python3 resume_agent.py --job-id 42 --propose
  python3 resume_agent.py --job-id 42 --build
  python3 resume_agent.py --drain        # Beelink resume-worker.service: strong rows without a resume
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_db_resume_queue.py tests/test_resume_agent.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add db.py resume_agent.py tests/test_db_resume_queue.py tests/test_resume_agent.py
git commit -m "feat(resume): --drain worker mode with resume_error and usage-limit stop"
```

---

### Task 7: `job_pick.py` queues instead of building on the subscription backend

**Files:**
- Modify: `job_pick.py:157-164`
- Test: `tests/test_job_pick.py`

**Interfaces:**
- Consumes: `config.RESUME_CLAUDE_BACKEND`.

- [ ] **Step 1: Write the failing test and pin existing tests to the API backend**

In `tests/test_job_pick.py`, add `import config` at the top. In each existing test that patches `job_pick.resume_agent.propose` (`grep -n "resume_agent.propose" tests/test_job_pick.py`), add as the first line:

```python
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "api")
```

Append:

```python
def test_run_queues_strong_verdict_for_the_beelink_on_subscription_backend(mocker):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "subscription")
    mocker.patch("job_pick.db.get_unscored_saved_applications",
                 return_value=[{"id": 1, "company": "Acme", "role": "PM", "posting_snapshot": {}}])
    mocker.patch("job_pick.score_job", return_value={"verdict": "strong", "score": 0.9, "reasoning": "x"})
    set_verdict = mocker.patch("job_pick.db.set_pick_verdict")
    propose = mocker.patch("job_pick.resume_agent.propose")
    build = mocker.patch("job_pick.resume_agent.build")

    job_pick.run()

    set_verdict.assert_called_once_with(1, "strong", 0.9, "x")
    propose.assert_not_called()
    build.assert_not_called()
```

- [ ] **Step 2: Run tests to verify the new one fails**

Run: `.venv/bin/python -m pytest tests/test_job_pick.py -q`
Expected: only `test_run_queues_strong_verdict_for_the_beelink_on_subscription_backend` FAILS (propose was called).

- [ ] **Step 3: Implement**

In `job_pick.py`, add `import config` if absent, and replace the `if result["verdict"] == "strong":` block:

```python
            if result["verdict"] == "strong":
                if config.RESUME_CLAUDE_BACKEND == "subscription":
                    # GitHub Actions never holds the subscription token; the Beelink's
                    # resume-worker.service picks strong rows up (resume_agent.py --drain).
                    queued += 1
                    log.info(f"[JOB-PICK] | {job.get('company')} | {job.get('role')} | queued for Beelink resume worker")
                else:
                    try:
                        resume_agent.propose(job_id)
                        resume_agent.build(job_id)
                        triggered += 1
                    except Exception as exc:
                        log.warning(f"[JOB-PICK] | {job.get('company')} | resume pipeline failed: {exc}")
                        pipeline_errors += 1
```

Initialize `queued = 0` next to `triggered = 0`, and add `| resume_queued={queued}` to the `DONE` log line after `resume_triggered={triggered}`.

Queue-age alarm (independent review, finding 3): at the end of `run()`, when the backend is `subscription`, count strong rows still waiting after `config.RESUME_QUEUE_STALE_HOURS` (new constant, `24`) and return it; `__main__` exits 1 when it's nonzero, so `jobright_pull.yml`'s existing `if: failure()` → `notify_failure.py` step alerts the operator that the Beelink worker isn't consuming. Add `db.count_stale_strong_without_resume(hours)` (same filters as `get_strong_applications_without_resume` plus `.lt("updated_at", <now - hours>)`, `count="exact"`, returns `result.count or 0`). Tests: `run()` returns the stale count on the subscription backend and `0` (no DB call) on `api`; `db.count_stale_strong_without_resume` filters as described. `job_pick.run()` previously returned `None`; check `grep -rn "job_pick.run()" .` for callers that relied on that (none are expected).

Update `run()`'s docstring to: `"""Batch-score unscored job applications. Strong verdicts are queued for the Beelink resume worker (subscription backend) or built immediately (api backend)."""`

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_job_pick.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add job_pick.py tests/test_job_pick.py
git commit -m "feat(job_pick): queue strong verdicts for the Beelink worker on the subscription backend"
```

---

### Task 8: Beelink deploy artifacts (worker unit, timer, provisioning)

**Files:**
- Create: `deploy/beelink/systemd/resume-worker.service`, `deploy/beelink/systemd/resume-worker.timer`, `deploy/beelink/env/claude.env.example`
- Modify: `deploy/beelink/provision-debian.sh`
- Test: `tests/test_beelink_units.py`

**Interfaces:**
- Consumes: CLI `resume_agent.py --drain` (Task 6); `config.CLAUDE_CLI_PATH` env override (Task 1).

- [ ] **Step 1: Write the failing tests**

In `tests/test_beelink_units.py`, add `"resume-worker.service", "resume-worker.timer",` to `test_every_expected_unit_exists`'s sorted list (alphabetical: after `"novnc@.service"`, before `"x11vnc@.service"`). Then append:

```python
# ── resume-worker ──────────────────────────────────────────────────────────────

_WORKER = os.path.join(_SYSTEMD, "resume-worker.service")
_CLAUDE_ENV_EXAMPLE = os.path.join(_ROOT, "deploy", "beelink", "env", "claude.env.example")


def test_resume_worker_runs_drain_as_jobagent_with_its_own_token_file():
    unit = _directives(_WORKER)
    assert "User=jobagent" in unit and "Type=oneshot" in unit
    assert "ExecStart=/opt/job-agent/.venv/bin/python resume_agent.py --drain" in unit
    assert "EnvironmentFile=/etc/job-agent/base.env" in unit
    assert "EnvironmentFile=/etc/job-agent/claude.env" in unit
    assert "Environment=CLAUDE_CLI_PATH=/var/lib/job-agent/.local/bin/claude" in unit
    assert "Environment=RESUME_CLAUDE_BACKEND=subscription" in unit
    assert "TimeoutStartSec=3600" in unit
    assert "OnFailure=notify-failure@%n.service" in unit


def test_only_the_resume_worker_loads_the_claude_token():
    for path in _unit_paths():
        if os.path.basename(path) != "resume-worker.service":
            assert "claude.env" not in _read(path), path


def test_claude_env_template_is_valueless_and_token_only():
    lines = [l for l in _read(_CLAUDE_ENV_EXAMPLE).splitlines() if l and not l.startswith("#")]
    assert lines == ["CLAUDE_CODE_OAUTH_TOKEN="]


def test_resume_worker_timer_cadence():
    timer = _read(os.path.join(_SYSTEMD, "resume-worker.timer"))
    assert "OnCalendar=*:0/30" in timer
    assert "RandomizedDelaySec=300" in timer


def test_provision_installs_worker_prereqs_but_never_enables_the_worker_timer():
    code = _provision_code()
    assert "libreoffice-writer-nogui" in code
    assert "claude.ai/install.sh" in code
    assert "/usr/local/share/fonts/calibri" in code
    assert "fc-match" in code
    assert "enable --now resume-worker" not in code and "enable resume-worker" not in code
```

The existing `test_every_service_is_hardened`, `test_every_service_explicitly_blanks_apply_agent_armed` and `test_every_service_has_a_hard_memory_cap` cover the new unit automatically. Extend `test_provision_never_writes_a_secret_value` to also check `"CLAUDE_CODE_OAUTH_TOKEN="` is not in `_provision_code()` (the script may *name* the file, never write a value).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_beelink_units.py -q`
Expected: FAIL (files missing).

- [ ] **Step 3: Implement**

`deploy/beelink/systemd/resume-worker.service`:

```ini
# Builds resumes for job_pick.py's strong-verdict rows on the operator's Claude subscription
# (resume_agent.py --drain -> claude_subscription.py -> claude -p). The only unit that loads
# /etc/job-agent/claude.env (CLAUDE_CODE_OAUTH_TOKEN). base.env also carries ANTHROPIC_API_KEY
# because config.py hard-requires it at import; claude_subscription.py strips it from the CLI's
# environment so the subscription, not the API key, is billed.
[Unit]
Description=Resume worker: build resumes for strong-verdict jobs on the Claude subscription
OnFailure=notify-failure@%n.service

[Service]
Type=oneshot
User=jobagent
Group=jobagent
WorkingDirectory=/opt/job-agent
EnvironmentFile=/etc/job-agent/base.env
EnvironmentFile=/etc/job-agent/claude.env
Environment=HOME=/var/lib/job-agent
Environment=CLAUDE_CLI_PATH=/var/lib/job-agent/.local/bin/claude
Environment=RESUME_CLAUDE_BACKEND=subscription
Environment=APPLY_AGENT_ARMED=
ExecStart=/opt/job-agent/.venv/bin/python resume_agent.py --drain
TimeoutStartSec=3600
MemoryMax=1536M
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/opt/job-agent /var/lib/job-agent
```

`deploy/beelink/systemd/resume-worker.timer`:

```ini
[Unit]
Description=Run the resume worker every 30 minutes

[Timer]
OnCalendar=*:0/30
RandomizedDelaySec=300
Persistent=false

[Install]
WantedBy=timers.target
```

`deploy/beelink/env/claude.env.example`:

```
# Template for /etc/job-agent/claude.env on the Beelink (root:root, chmod 0600). Loaded ONLY by
# resume-worker.service. The value comes from running `claude setup-token` on the Mac (browser
# login, one-year token) and is pasted in by hand -- never by a script or an agent, never into
# git, never into base.env (rule 1 of docs/beelink-server.md: no global Claude credentials).
CLAUDE_CODE_OAUTH_TOKEN=
```

`deploy/beelink/provision-debian.sh` changes:

1. Add `libreoffice-writer-nogui` to the `apt-get install` package list.
2. After the Chrome block, add:

```bash
# ── Resume worker prerequisites ────────────────────────────────────────────────
# Calibri is the operator's own licensed font (from Microsoft Word on the Mac), copied here by
# hand. Without it LibreOffice embeds Carlito and resume_agent's font check refuses every build.
FONT_DIR=/usr/local/share/fonts/calibri
[ -d "$FONT_DIR" ] && fc-cache -f "$FONT_DIR" >/dev/null
# fc-match, not a file check: proves fontconfig actually resolves the family LibreOffice will ask for.
if [ "$(fc-match -f '%{family}' Calibri)" != "Calibri" ]; then
    echo "fontconfig does not resolve Calibri -- copy Calibri*.ttf from the Mac into $FONT_DIR (RUNBOOK, top)" >&2
    exit 1
fi
```

3. After the venv block, add:

```bash
log "claude CLI for jobagent"
if [ ! -x "$STATE/.local/bin/claude" ]; then
    runuser -u jobagent -- env HOME="$STATE" bash -c 'curl -fsSL https://claude.ai/install.sh | bash' >/dev/null
fi
runuser -u jobagent -- env HOME="$STATE" "$STATE/.local/bin/claude" --version
if [ ! -f "$ETC/claude.env" ]; then
    install -o root -g root -m 0600 "$APP/deploy/beelink/env/claude.env.example" "$ETC/claude.env"
    log "created $ETC/claude.env from the template -- paste your setup-token: sudo nano $ETC/claude.env"
fi
```

4. In the units section, after `systemctl daemon-reload`, add a comment line only (no enable):

```bash
    # resume-worker.timer is enabled by hand after the first watched run (RUNBOOK).
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_beelink_units.py -q && bash -n deploy/beelink/provision-debian.sh`
Expected: all pass, no syntax error. `test_one_display_slot_fits_the_real_memory_budget` is unaffected (it sums only the slot units).

- [ ] **Step 5: Commit**

```bash
git add deploy/beelink tests/test_beelink_units.py
git commit -m "feat(beelink): resume-worker unit and timer; provision LibreOffice, Calibri check, claude for jobagent"
```

---

### Task 9: Docs and memory

**Files:**
- Modify: `CLAUDE.md` (Module layout; "Resume intelligence" section; GitHub Actions `jobright_pull.yml` bullet; Tests list)
- Modify: `docs/beelink-server.md` (section 3 "Installed"; roadmap Phase 2b; rule 1 note)
- Modify: `deploy/beelink/RUNBOOK.md` (top Debian block: Calibri copy + claude.env + worker)
- Modify: `docs/python/db-schema.md` (`api_usage_log.billing`, `job_applications.resume_error`)
- Memory: `~/.claude/projects/-Users-kishoretheeraj-Documents-cold-email-agent/memory/project-resume-subscription.md` + `MEMORY.md` line

- [ ] **Step 1: CLAUDE.md**

Add `claude_subscription.py` to the Module layout list (after `candidate_profile.py`). Append to the "Resume intelligence" section:

```markdown
**Subscription transport (2026-10-04).** `resume_agent._call_claude` runs on the operator's
Claude subscription via `claude_subscription.complete()` (`claude -p`), selected by
`config.RESUME_CLAUDE_BACKEND` (env var, default `"api"`; `resume-worker.service` sets
`"subscription"`, and `jobright_pull.yml` opts in after rollout). Every call strips ambient context
(empty cwd + empty `CLAUDE_CONFIG_DIR`, `--strict-mcp-config --setting-sources ""`, `--tools ""`)
-- measured ~224K -> ~6.5K tokens per call -- and passes the CLI an env **allowlist** (Claude Code
prefers an API key, auth token, base URL or cloud provider over the subscription). `$0` on
`billing='subscription'` rows assumes paid usage credits are off for the account.
**Never `--bare`**: it ignores OAuth. Auth is `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`).
Subscription usage is logged with `api_usage_log.billing='subscription'`, `cost_usd=0`.
On the subscription backend `job_pick.py` queues instead of zero-tapping (and alarms after 24h); strong rows are built by the
Beelink's `resume-worker.timer` (`resume_agent.py --drain`, `resume_error` stops retries; a usage
limit stops the run unmarked). Every Claude response is `sanitize()`d (zero-width/bidi/control
chars), `_check_attribution` hard-fails tool attribution and chat preambles/sign-offs, the scrub
deletes the XMP packet and disables pikepdf's editor stamp (it used to write `pikepdf 10.x` as
Producer on every PDF), and `check_fonts` refuses any embedded font other than
`RESUME_FONT_NAME` (LibreOffice used to embed Carlito/Caladea/OpenSymbol, on the Mac too) --
Calibri must be installed where `soffice` sees it. Claude's text may be watermarked
(anthropic.com/news/claude-text-watermark); resume bullets/skills are the operator's own text and
only the 1-4-word skills group labels are model-written (governed); the cover letter is
Claude-written. No
watermark-removal step exists or will be added. Spec:
docs/superpowers/specs/2026-10-04-resume-subscription-transport-design.md.
```

In the `jobright_pull.yml` bullet, replace "zero-tap trigger `resume_agent.py`'s propose+build on `strong` verdicts" with "mark `strong` verdicts for the Beelink resume worker (zero-tap propose+build only when `RESUME_CLAUDE_BACKEND='api'`)". Add the three new test files to the Tests list: `tests/test_claude_subscription.py`, `tests/test_resume_subscription_migration.py`, `tests/test_db_resume_queue.py`.

- [ ] **Step 2: docs/beelink-server.md, RUNBOOK.md, db-schema.md**

`docs/beelink-server.md` section 3 "Installed": add "Claude Code for `jobagent` (resume worker) — installed by `provision-debian.sh`". Phase 2b: add checklist items "- [ ] Copy Calibri to `/usr/local/share/fonts/calibri/`", "- [ ] Paste `claude setup-token` output into `/etc/job-agent/claude.env`", "- [ ] Watched `systemctl start resume-worker`, then `systemctl enable --now resume-worker.timer`". Under rule 1, add: "The resume worker is the one deliberate subscription-token holder: `CLAUDE_CODE_OAUTH_TOKEN` in `/etc/job-agent/claude.env`, loaded only by `resume-worker.service`."

`deploy/beelink/RUNBOOK.md` top Debian block, before the provision command, add:

```bash
# Mac: copy your own Calibri (from Microsoft Word) to the box -- provision refuses to run without it
scp "/Applications/Microsoft Word.app/Contents/Resources/DFonts/"[Cc]alibri*.ttf kishore@beelink:/tmp/
# Beelink: install it
sudo install -d /usr/local/share/fonts/calibri && sudo install -m 0644 /tmp/[Cc]alibri*.ttf /usr/local/share/fonts/calibri/ && rm /tmp/[Cc]alibri*.ttf
```

and after `sudo nano /etc/job-agent/base.env`:

```bash
# Mac: create a one-year subscription token (opens a browser); copy the printed token
claude setup-token
# Beelink: paste it after CLAUDE_CODE_OAUTH_TOKEN=
sudo nano /etc/job-agent/claude.env
```

`docs/python/db-schema.md`: under `api_usage_log`, add `billing TEXT NOT NULL DEFAULT 'api'` (`'api'|'subscription'`; subscription rows have real tokens and `cost_usd = 0`). Under `job_applications`, add `resume_error TEXT NULL` (set by `resume_agent.py --drain`; clearing re-queues; anon/authenticated `UPDATE (resume_error)` granted in `20261005000000`).

- [ ] **Step 3: Memory**

Write `project-resume-subscription.md` (frontmatter: name `project-resume-subscription`, type `project`) with: what shipped, the measured 224K→6.5K isolation finding, the `--bare`-ignores-OAuth finding, the pikepdf/LibreOffice-font leak found in every earlier resume, the watermark facts and the option-3 decision (no removal step, declined twice), and what's left (Task 10 live steps). Add to `MEMORY.md`: `- [Project: Resume on Subscription](project-resume-subscription.md) — 2026-10-04; claude -p transport, Beelink resume-worker, pikepdf/Carlito metadata leaks fixed; no watermark removal (declined)`.

- [ ] **Step 4: Full suite, then commit**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: all green (≈1321 + new tests).

```bash
git add CLAUDE.md docs/beelink-server.md deploy/beelink/RUNBOOK.md docs/python/db-schema.md
git commit -m "docs: resume generation on the Claude subscription"
git push -u origin feat/resume-subscription
gh pr create --title "Resume generation on the Claude subscription" --body-file - <<'EOF'
Moves resume_agent's Claude calls to the operator's subscription via `claude -p` (spec:
docs/superpowers/specs/2026-10-04-resume-subscription-transport-design.md), adds the Beelink
resume worker, and fixes three metadata leaks found on the way (pikepdf producer stamp,
LibreOffice XMP dates, Carlito/Caladea/OpenSymbol fonts). Migration 20261005000000 must be
applied before merge.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
```

---

### Task 10: Live verification and rollout (operator + agent)

Steps marked **(operator)** need Kishore (browser login, sudo password, signing key, his own fonts). Everything else an agent can run. Record results in the PR description.

**Order matters (independent review, finding 3).** Merging is safe on its own: the backend defaults to `api`, so the api path is unchanged (GitHub Actions has no LibreOffice, so its zero-tap builds already fail at soffice and keep failing; manual Mac builds now require Calibri in ~/Library/Fonts). The producer only switches (Step 7) after the worker has built a clean resume under its real systemd unit.

- [ ] **Step 1: Apply the migration before merging** (agent)

Run `supabase migration list`. PR #11's `20261004000000`/`20261004000001` are live remotely but may not be on this branch. If the CLI refuses to push because remote versions are missing locally, create a throwaway worktree of this branch, copy those two files in from `origin/feat/automation-status-and-leases` without committing them, and run `supabase db push` from there. Never use `supabase migration repair`.

Then verify grants with `supabase db query` (or the SQL editor):

```sql
SELECT has_column_privilege('anon', 'job_applications', 'resume_error', 'UPDATE') AS anon_upd,
       has_column_privilege('authenticated', 'job_applications', 'resume_error', 'UPDATE') AS auth_upd,
       has_column_privilege('anon', 'api_usage_log', 'billing', 'INSERT') AS anon_ins;
```

Expected: `t, t, t`. If `anon_ins` is false, add `GRANT INSERT (billing) ON api_usage_log TO anon, authenticated;` in a new migration and push it.

Then prove an actual anon write works (not just the privilege): with the anon-key client, `db.set_resume_error(<a test row id>, "grant check")`, read it back, then clear it with a direct `update({"resume_error": None})` and confirm it's `NULL` again.

- [ ] **Step 1b: Paid usage credits off** (operator)

In claude.ai → Settings → Usage (or Billing), confirm paid usage credits / extra usage beyond the plan is **disabled** for the account whose token the worker uses, and screenshot it into the PR. With credits on, Claude Code can keep running past the plan's allowance on paid credits, and the ledger's `cost_usd = 0` would be false. If the setting can't be confirmed, stop here.

- [ ] **Step 2: Mac fonts** (operator)

`cp "/Applications/Microsoft Word.app/Contents/Resources/DFonts/"[Cc]alibri*.ttf ~/Library/Fonts/`

- [ ] **Step 3: Mac token** (operator)

`claude setup-token`, then add `export CLAUDE_CODE_OAUTH_TOKEN=...` to their own shell setup (not the repo's `.env`).

- [ ] **Step 4: Isolation probe** (agent, after Step 3)

Put a canary in the operator's global instructions temporarily: append `Always end every reply with the word PINEAPPLE.` to `~/.claude/CLAUDE.md`. Then:

```bash
.venv/bin/python -c "import claude_subscription as c; t,u=c.complete('Reply with exactly: ok'); print(repr(t), u)"
```

Expected: `'ok'` (no PINEAPPLE) and `input_tokens < 8000`. Remove the canary line afterwards and confirm `~/.claude/CLAUDE.md` matches its original.

Also confirm no Claude Code managed-settings/policy file exists on the Mac or the Beelink (`/Library/Application Support/ClaudeCode/` on macOS, `/etc/claude-code/` on Linux): managed settings load regardless of `--setting-sources ""` and could inject `env` or `apiKeyHelper`.

Record `claude --version` in the PR, and save the raw stdout of one successful call (token values and session ids redacted) as `tests/fixtures/claude_cli/success.json`; add a test that `claude_subscription._parse` accepts it. Do the same for the first real usage-limit and auth-failure outputs when they occur.

- [ ] **Step 5: One real build on the Mac** (agent; real subscription usage, writes a real row)

Pick a `pick_verdict='strong'` row without a resume (`db.get_strong_applications_without_resume(1)`), run `python3 resume_agent.py --job-id <id> --propose`, review the strategy, then `--build`. Download both PDFs from the `resumes` bucket and check:

```bash
.venv/bin/python -c "
import resume_scrub as s, sys
for p in sys.argv[1:]:
    print(p, s.embedded_font_families(p), s.verify_no_fingerprints(s.read_pdf_metadata_text(p)+' '+s.read_pdf_xmp_text(p)))
" resume.pdf cover_letter.pdf
```

Expected: `{'Calibri'} []` for both. Confirm the `api_usage_log` rows for that id have `billing='subscription'`, `cost_usd=0`.

- [ ] **Step 6: Beelink** (operator, then agent)

Operator: copy Calibri (RUNBOOK); run `claude setup-token` if a separate token is wanted for the box (or reuse); merge the PR; sign a tag on the merge commit after reviewing it (`git -c gpg.format=ssh -c user.signingkey=$HOME/.ssh/id_ed25519 tag -s beelink-v2 <merge-commit> -m beelink-v2 && git push origin beelink-v2`); `scp` the updated `provision-debian.sh` to the box; `sudo bash ~/provision-debian.sh beelink-v2 ~/allowed_signers`; `sudo nano /etc/job-agent/claude.env`.

**Backlog triage (before `enable --now`).** Count `pick_verdict='strong' AND stage='saved' AND resume_file_ref IS NULL AND resume_error IS NULL`. The operator decides which old postings to skip and marks them `resume_error='backlog-skip'`; otherwise the worker builds old postings first and job_pick's stale alarm fires every run until the backlog drains.

Agent: `ssh kishore@beelink 'sudo systemctl start resume-worker.service; systemctl status resume-worker.service --no-pager; sudo journalctl -u resume-worker -n 30 --no-pager'`. This is the canary under the real hardened unit (PrivateTmp, ProtectHome, MemoryMax, jobagent). Repeat Step 5's PDF check on what it uploaded. If clean: `sudo systemctl enable --now resume-worker.timer` and `systemctl list-timers resume-worker.timer`. Record the CLI's real usage-limit message the first time it appears in `resume_agent.log` and add it to `test_retry_later_messages_raise_usage_limit_error`.

- [ ] **Step 7: Switch the producer** (agent, after Step 6 is clean)

Add `RESUME_CLAUDE_BACKEND: subscription` to the env of the "Score newly-discovered jobs" step in `.github/workflows/jobright_pull.yml`, commit, push. From the next run on, strong verdicts queue for the Beelink and `job_pick` alarms if any wait more than 24h.

- [ ] **Step 8: Rollback procedure** (document in the PR; run only if needed)

Remove that env line from `jobright_pull.yml` (new strong verdicts build in GitHub Actions again on the API key), then build anything already queued with the API path on the Mac: `RESUME_CLAUDE_BACKEND=api python3 resume_agent.py --drain` (repeat until it reports `rows=0`). Disable the timer on the box: `sudo systemctl disable --now resume-worker.timer`.
