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
import signal
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

_SPACE_LIKE = {"\xa0": " ", " ": " ", " ": " "}


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
            proc = subprocess.Popen(
                _argv(model or config.RESUME_MODEL, system), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=workdir,
                env=_child_env(config_dir), start_new_session=True,
            )
            try:
                stdout, stderr = proc.communicate(input=prompt, timeout=config.CLAUDE_CLI_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.communicate()
                raise ClaudeSubscriptionError(f"claude CLI timed out after {config.CLAUDE_CLI_TIMEOUT_SECONDS}s")
            proc = type('CompletedProcess', (), {'returncode': proc.returncode, 'stdout': stdout, 'stderr': stderr})()
        except OSError as exc:
            raise ClaudeSubscriptionError(f"could not run claude CLI: {exc}") from exc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        shutil.rmtree(config_dir, ignore_errors=True)
    return _parse(proc)
