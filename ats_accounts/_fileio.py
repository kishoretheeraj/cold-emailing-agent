"""
Small file helpers shared by the vault sidecar and the backoff store: JSON read and an atomic,
fsync'd, 0600 write. Locking is done by callers with credential_vault.file_lock.
"""

import json
import os
import time

from credential_vault import fsync_dir


def read_json(path, default):
    """Parsed JSON at `path`, or `default` when the file does not exist. A damaged file raises
    ValueError: for attempt limits and backoff, silently starting over would lift the limits."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def atomic_write_json(path, data):
    """Write JSON to a temp file (0600), fsync it, rename over `path`, fsync the directory."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{time.time_ns()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        fsync_dir(path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
