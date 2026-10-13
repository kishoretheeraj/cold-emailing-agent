"""
Append-only audit trail for vault retrieval (item 9).

One JSON line per attempt: UTC timestamp, action, tenant, the unix user, the SSH client IP, the
result and the method. It never contains a password, a TOTP code or any other secret. Writing is
best-effort: a failure is swallowed (logged without detail) and `audit()` never raises; it returns
False on failure so a caller that must not proceed unaudited (the reveal display) can refuse.
"""

import getpass
import json
import logging
import os
from datetime import datetime, timezone

from credential_vault import fsync_dir

log = logging.getLogger(__name__)

DEFAULT_AUDIT_LOG = "/var/log/jobagent24/vault_audit.jsonl"
_RESULTS = ("ok", "denied", "error")
_METHODS = ("tty", "token")


def audit_log_path():
    return os.environ.get("VAULT_AUDIT_LOG") or DEFAULT_AUDIT_LOG


def _unix_user():
    # Under `sudo -u jobagent`, getuser() reports jobagent; SUDO_USER names the real operator.
    try:
        return os.environ.get("SUDO_USER") or getpass.getuser()
    except Exception:
        return None


def _ssh_client_ip():
    parts = os.environ.get("SSH_CONNECTION", "").split()
    return parts[0] if parts else None


def audit(action, tenant, result, method, log_path=None):
    """Append one audit line. Returns True on success, False on any failure; never raises.
    Callers that must not proceed unaudited (the reveal display) gate on the return value."""
    try:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "tenant": tenant,
            "unix_user": _unix_user(),
            "ssh_client_ip": _ssh_client_ip(),
            "result": result if result in _RESULTS else "error",
            "method": method if method in _METHODS else "tty",
        }
        line = (json.dumps(record, sort_keys=True, default=str) + "\n").encode()
        path = log_path or audit_log_path()
        os.makedirs(os.path.dirname(path) or ".", mode=0o750, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
        try:
            view = memoryview(line)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            fsync_dir(path)  # a new log file's directory entry must be durable before we claim success
        except OSError:
            return False
        return True
    except Exception as exc:
        try:
            log.warning(f"vault audit | write failed | {type(exc).__name__}")
        except Exception:
            pass
        return False
