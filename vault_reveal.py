#!/usr/bin/env python3
"""
SSH-gated credential retrieval (item 9): `vault_reveal.py <tenant>`.

Run through sudo as the jobagent user from an interactive SSH session (`ssh -t`). Gates, in order:
stdout must be a TTY (so the secret cannot be piped or captured by a script), then a TOTP code from
the secret in TOTP_SECRET_FILE. Every attempt is audited (vault_audit.py). The entry is shown on the
terminal's alternate screen for --ttl seconds and then erased, so it never lands in scrollback.

Exit codes: 0 ok, 2 stdout is not a TTY, 3 authentication failed, 4 vault error, 5 audit write
failed (the entry is never displayed without a persisted audit line).

Note on the TTY gate: isatty() stops casual piping, not a determined operator recording their own
PTY (e.g. `ssh -tt ... >capture`). The gate that actually protects the secret is the TOTP code plus
the audit trail; the alternate-screen display only keeps it out of scrollback on a normal terminal.
"""

import argparse
import getpass
import json
import logging
import math
import os
import re
import sys
import time

import pyotp

from credential_vault import KEY_ENV_NAME, Vault, VaultError, file_lock, read_key_file
import vault_audit

log = logging.getLogger(__name__)

DEFAULT_VAULT_PATH = "/var/lib/job-agent/vault.bin"
DEFAULT_KEY_FILE = "/etc/job-agent/vault.env"
DEFAULT_TOTP_SECRET_FILE = "/etc/jobagent24/totp.secret"
_ALT_ON = "\x1b[?1049h"
_ALT_OFF = "\x1b[?1049l"
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_THROTTLE_MAX_FAILURES = 10
_THROTTLE_WINDOW_S = 600


def _clean(value):
    """Strip control characters so a hostile tenant/email cannot break out of the
    alternate screen (e.g. an embedded ESC[?1049l would dump the password to scrollback)."""
    return _CONTROL_CHARS.sub("", str(value or ""))


# ── TOTP attempt throttle ──────────────────────────────────────────────────────

def _throttle_path():
    return os.environ.get("VAULT_TOTP_THROTTLE_FILE",
                          "/var/lib/jobagent24/vault_totp_throttle.json")


class _ThrottleError(Exception):
    """The throttle state exists but cannot be read or parsed. Never silently treated as empty."""


_MISSING = object()


def _throttle_state(path):
    try:
        with open(path) as f:
            state = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise _ThrottleError(f"throttle state unreadable: {exc}") from exc
    if not isinstance(state, dict):
        raise _ThrottleError("throttle state is not an object")
    failures = state.get("failures", _MISSING)
    if failures is _MISSING or not isinstance(failures, list):
        raise _ThrottleError("throttle state has no failures list")
    clean = []
    for t in failures:
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t):
            raise _ThrottleError("throttle state has a non-numeric failure timestamp")
        clean.append(t)
    return clean


def _throttle_save(path, failures):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"failures": failures}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _throttle_consume():
    """Atomically consume one TOTP attempt slot. Returns the slot's timestamp, or None when the
    window is exhausted or the throttle state is unusable (fail closed: an unreadable throttle
    must not silently disable brute-force protection).

    The slot is consumed BEFORE prompting, in the same lock transaction as the check, so N
    concurrent sessions cannot each slip under the limit: the 11th concurrent session sees
    10 consumed slots and is refused. A successful verification refunds its slot; a failure
    keeps it as a recorded failure. Abandoned prompts (Ctrl-C) also consume a slot."""
    path = _throttle_path()
    try:
        with file_lock(path):
            now = time.time()
            failures = [t for t in _throttle_state(path) if now - t < _THROTTLE_WINDOW_S]
            if len(failures) >= _THROTTLE_MAX_FAILURES:
                return None
            failures.append(now)
            _throttle_save(path, failures)
            return now
    except Exception as exc:
        log.warning(f"vault reveal | throttle consume failed ({type(exc).__name__}); refusing")
        return None


def _throttle_refund():
    """Refund one consumed slot after a successful verification."""
    path = _throttle_path()
    try:
        with file_lock(path):
            failures = _throttle_state(path)
            if failures:
                failures.pop()
                _throttle_save(path, failures)
    except Exception as exc:
        log.warning(f"vault reveal | throttle refund failed ({type(exc).__name__})")


def _slot_expired(slot):
    return time.time() - slot >= _THROTTLE_WINDOW_S


# ── Authentication and vault access ────────────────────────────────────────────

def _totp_ok(code):
    try:
        path = os.environ.get("TOTP_SECRET_FILE", DEFAULT_TOTP_SECRET_FILE)
        if os.path.islink(path):
            log.warning("vault reveal | totp secret is a symlink")
        with open(path) as f:
            mode = os.fstat(f.fileno()).st_mode & 0o777
            if mode & 0o077:
                log.warning(f"vault reveal | totp secret has insecure permissions ({oct(mode)})")
                return False
            secret = f.read().strip()
        if not secret or not code:
            return False
        return bool(pyotp.TOTP(secret).verify(code.strip(), valid_window=1))
    except Exception:
        return False


def _open_vault():
    path = os.environ.get("APPLY_VAULT_PATH", DEFAULT_VAULT_PATH)
    key = os.environ.get(KEY_ENV_NAME)
    key = key.strip().encode() if key else read_key_file(os.environ.get("VAULT_KEY_FILE", DEFAULT_KEY_FILE))
    return Vault(path, key)


# ── Display ────────────────────────────────────────────────────────────────────

def _display(tenant, entry, ttl):
    lines = [
        f"tenant:   {_clean(tenant)}",
        f"email:    {_clean(entry.get('email', ''))}",
        f"password: {entry.get('password', '')}",
        "",
        f"(clears in {ttl}s)",
    ]
    out = sys.stdout
    out.write(_ALT_ON + "\n")
    try:
        out.write("\n".join(lines) + "\n")
        out.flush()
        time.sleep(ttl)
    finally:
        try:
            out.write("\x1b[H")
            for _ in lines:
                out.write("\r\x1b[2K\n")
        finally:
            out.write(_ALT_OFF)
            out.flush()


# ── Entry point ────────────────────────────────────────────────────────────────

def _run(args):
    tenant = args.tenant
    if not sys.stdout.isatty():
        print("refusing: stdout is not a TTY", file=sys.stderr)
        vault_audit.audit("reveal", tenant, "denied", "tty")
        log.warning(f"vault reveal | {tenant} | denied | not a tty")
        return 2

    slot = _throttle_consume()
    if slot is None:
        print("too many attempts; try again later", file=sys.stderr)
        vault_audit.audit("reveal", tenant, "denied", "token")
        log.warning(f"vault reveal | {tenant} | denied | throttled")
        return 3

    code = None
    if os.path.exists(os.environ.get("TOTP_SECRET_FILE", DEFAULT_TOTP_SECRET_FILE)):
        code = getpass.getpass("TOTP: ")
    if _slot_expired(slot):
        # The reservation expired while the prompt sat open: pending prompts cannot be
        # stockpiled across windows and submitted together.
        print("session expired; try again", file=sys.stderr)
        vault_audit.audit("reveal", tenant, "denied", "token")
        log.warning(f"vault reveal | {tenant} | denied | slot expired")
        return 3
    if not _totp_ok(code):
        print("authentication failed", file=sys.stderr)
        vault_audit.audit("reveal", tenant, "denied", "token")
        log.warning(f"vault reveal | {tenant} | denied | totp")
        return 3
    _throttle_refund()

    try:
        entry = _open_vault().reveal(tenant)
    except VaultError as exc:
        print(f"vault error: {exc}", file=sys.stderr)
        vault_audit.audit("reveal", tenant, "error", "token")
        log.warning(f"vault reveal | {tenant} | error | vault")
        return 4

    if not vault_audit.audit("reveal", tenant, "ok", "token"):
        print("audit failed; refusing to display", file=sys.stderr)
        log.warning(f"vault reveal | {tenant} | ok | audit write failed; not displaying")
        return 5
    log.info(f"vault reveal | {tenant} | ok")
    _display(tenant, entry, args.ttl)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Show one vault entry on screen after TOTP.")
    parser.add_argument("tenant")
    parser.add_argument("--ttl", type=int, default=30, help="seconds on screen (1-300)")
    args = parser.parse_args(argv)
    args.ttl = max(1, min(args.ttl, 300))
    sys.exit(_run(args))


if __name__ == "__main__":
    main()
