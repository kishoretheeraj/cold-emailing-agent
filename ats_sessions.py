"""
Per-tenant browser sessions for the Beelink apply worker (spec 2026-10-08 §5).

`tenant_key(url)` says which URLs share one candidate account: the host for single-company
domains and Workday tenants, host plus company slug on shared multi-company hosts (Lever,
Greenhouse, Ashby, Workable, SmartRecruiters). A logged-in Playwright `storage_state` is kept per
tenant under APPLY_SESSIONS_DIR (0700 dir, 0600 files, names hashed so a directory listing does
not reveal where the operator applied).
"""

import hashlib
import os
import re
from urllib.parse import urlparse

import config

_SHARED_HOSTS = {
    "jobs.lever.co", "jobs.eu.lever.co",
    "boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io",
    "jobs.ashbyhq.com", "apply.workable.com", "jobs.smartrecruiters.com",
}
_WORKDAY_TENANT_HOST = re.compile(r"(^|\.)myworkdayjobs\.com$")
_WORKDAY_SHARED_HOST = re.compile(r"(^|\.)myworkdaysite\.com$")


def tenant_key(url):
    """The account boundary for `url`, or None when it is not an http(s) URL with a host."""
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    segments = [s for s in parsed.path.split("/") if s]
    if host in _SHARED_HOSTS:
        return f"{host}/{segments[0].lower()}" if segments else None
    if _WORKDAY_SHARED_HOST.search(host):
        # wd3.myworkdaysite.com/recruiting/<tenant>/<site>/...
        if len(segments) >= 2 and segments[0].lower() == "recruiting":
            return f"{host}/{segments[1].lower()}"
        return None
    return host


def _path(key):
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return os.path.join(config.APPLY_SESSIONS_DIR, f"{digest}.json")


def state_path(key):
    """The saved storage_state file for this tenant, or None when there is none yet."""
    path = _path(key)
    return path if os.path.isfile(path) else None


def save_state(context, key):
    """Write the context's cookies and storage for this tenant, atomically, readable only by us."""
    os.makedirs(config.APPLY_SESSIONS_DIR, mode=0o700, exist_ok=True)
    os.chmod(config.APPLY_SESSIONS_DIR, 0o700)
    path = _path(key)
    tmp = f"{path}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
        context.storage_state(path=tmp)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def forget(key):
    """Drop this tenant's saved session (a stale or logged-out state)."""
    try:
        os.remove(_path(key))
    except FileNotFoundError:
        pass
