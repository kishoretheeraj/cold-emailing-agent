"""Subprocess environment allowlisting.

Child processes get a minimal allowlisted environment, never the full parent env.
This prevents accidental leakage of API keys, tokens, passwords to subprocesses.
"""

import fnmatch
import os

ALLOWLIST = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR",
    "PYTHONPATH", "CLAUDE_CODE_OAUTH_TOKEN", "PYTHONUNBUFFERED",
})

BLOCKLIST_PATTERNS = ["*API_KEY*", "*SECRET*", "*TOKEN*", "*PASSWORD*", "*PRIVATE*", "*CREDENTIAL*"]

# The one deliberate subscription credential a child may carry (see docs/beelink-server.md rule 1).
_BLOCKLIST_EXEMPT = frozenset({"CLAUDE_CODE_OAUTH_TOKEN"})


def _is_blocked(name: str) -> bool:
    if name in _BLOCKLIST_EXEMPT:
        return False
    upper = name.upper()
    return any(fnmatch.fnmatchcase(upper, pattern) for pattern in BLOCKLIST_PATTERNS)


def clean_env(extra: dict | None = None) -> dict:
    """A new env dict: allowlisted, non-blocklisted os.environ entries plus `extra`.

    `extra` is merged unfiltered because it is an intentional, caller-written addition.
    os.environ is never modified.
    """
    env = {k: v for k, v in os.environ.items() if k in ALLOWLIST and not _is_blocked(k)}
    if extra:
        env.update(extra)
    return env


def assert_no_secrets(env: dict) -> None:
    """Raise ValueError naming (never showing values of) any blocklisted keys in env."""
    leaked = sorted(k for k in env if _is_blocked(k))
    if leaked:
        raise ValueError(f"secret-looking keys present in environment: {', '.join(leaked)}")
