"""
Per-tenant browser sessions for the Beelink apply worker (spec 2026-10-08 §5).

`tenant_key(url)` says which URLs share one candidate account: the host for single-company
domains and Workday tenants, host plus company slug on shared multi-company hosts (Lever,
Greenhouse, Ashby, Workable, SmartRecruiters). A logged-in Playwright `storage_state` is kept per
tenant under APPLY_SESSIONS_DIR (0700 dir, 0600 files, names hashed so a directory listing does
not reveal where the operator applied).
"""

import hashlib
import json
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


def _within(host, domain):
    host = (host or "").lower().lstrip(".")
    return host == domain or host.endswith("." + domain)


def _storage_state(context, indexed_db):
    # IndexedDB is captured only on request (Playwright >= 1.51); an older Playwright without the
    # keyword falls back to cookies + localStorage.
    if indexed_db:
        try:
            return context.storage_state(indexed_db=True)
        except TypeError:
            pass
    return context.storage_state()


def save_state(context, key, domain=None, indexed_db=False):
    """Write the context's cookies and storage for this tenant, atomically, readable only by us.
    With `domain`, only that site's cookies and origins are kept: a human who signed in with
    Google during a takeover must not leave the operator's Google session in this file. With
    `indexed_db`, origins carry their IndexedDB too (some tenants keep session material there)."""
    os.makedirs(config.APPLY_SESSIONS_DIR, mode=0o700, exist_ok=True)
    os.chmod(config.APPLY_SESSIONS_DIR, 0o700)
    path = _path(key)
    tmp = f"{path}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
        if domain:
            state = _storage_state(context, indexed_db)
            state = {"cookies": [c for c in state.get("cookies", []) if _within(c.get("domain"), domain)],
                     "origins": [o for o in state.get("origins", [])
                                 if _within(urlparse(o.get("origin", "")).hostname, domain)]}
            with open(tmp, "w") as fh:
                json.dump(state, fh)
        elif indexed_db:
            with open(tmp, "w") as fh:
                json.dump(_storage_state(context, True), fh)
        else:
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


def restore_cookies(context, key):
    """Add this tenant's saved cookies to an open context (the form opened on a host the browser
    was not launched with). True when there were any."""
    path = state_path(key)
    if not path:
        return False
    try:
        with open(path) as fh:
            cookies = json.load(fh).get("cookies") or []
    except (OSError, ValueError):
        return False
    if not cookies:
        return False
    context.add_cookies(cookies)
    return True


# ── Loading a saved state into a fresh per-job context (apply_pipeline) ────────

_MAX_STATE_BYTES = 1 << 20


class SessionStateUntrusted(Exception):
    """A saved storage_state that must not be loaded: wrong mode, too large, malformed, or holding
    cookies/origins outside the job's host after filtering."""


def shared_host(key):
    """True for a tenant on a multi-company host (key carries a company slug): its origin-wide
    localStorage/IndexedDB is shared with other companies' tenants and is never restored."""
    return "/" in (key or "")


def _origin_parts(origin):
    try:
        parsed = urlparse(origin or "")
        port = parsed.port or {"http": 80, "https": 443}.get(parsed.scheme)
    except ValueError:
        return None
    if not parsed.hostname or parsed.path not in ("", "/") or parsed.query or parsed.username:
        return None
    return parsed.scheme, parsed.hostname.lower(), port


def load_state_for(key, host, allowed_origins):
    """The saved storage_state for this tenant, filtered for one job on `host`, as a dict for
    `new_context(storage_state=...)`; None when nothing is saved. Fails closed
    (SessionStateUntrusted) on a file that is not 0600, larger than 1 MiB, or malformed.

    Kept: cookies whose domain is `host` itself or a subdomain of it (never a parent or sibling
    domain, which would ride along to other tenants' hosts), and storage for origins in
    `allowed_origins` ({(scheme, host, port)}, scheme and port explicit) -- except on a shared
    multi-company host, where origin-wide storage belongs to every tenant there and only cookies
    are restored."""
    path = state_path(key)
    if not path:
        return None
    try:
        info = os.stat(path)
    except OSError:
        return None
    if info.st_mode & 0o077:
        raise SessionStateUntrusted("session state is readable by others")
    if info.st_size > _MAX_STATE_BYTES:
        raise SessionStateUntrusted("session state is too large")
    try:
        with open(path) as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        raise SessionStateUntrusted("session state is malformed") from None
    if (not isinstance(state, dict) or not isinstance(state.get("cookies", []), list)
            or not isinstance(state.get("origins", []), list)):
        raise SessionStateUntrusted("session state is malformed")
    host = (host or "").lower()
    cookies = [c for c in state.get("cookies", [])
               if isinstance(c, dict) and isinstance(c.get("domain"), str) and _within(c["domain"], host)]
    origins = []
    if not shared_host(key):
        for origin in state.get("origins", []):
            if not isinstance(origin, dict):
                continue
            parts = _origin_parts(origin.get("origin"))
            if parts is not None and parts in allowed_origins and parts[1] == host:
                origins.append(origin)
    # Fail closed if filtering ever let something foreign through.
    if any(not _within(c["domain"], host) for c in cookies) or any(
            (_origin_parts(o.get("origin")) or (None, None, None))[1] != host for o in origins):
        raise SessionStateUntrusted("session state escaped its host")
    return {"cookies": cookies, "origins": origins}


def expire(key):
    """Set a stale session aside instead of deleting it: renamed to <file>.expired, so an operator
    can restore it if the sign-in wall was misread. Returns the new path, or None."""
    path = _path(key)
    target = f"{path}.expired"
    try:
        os.replace(path, target)
    except FileNotFoundError:
        return None
    try:
        os.utime(target)  # the seven days count from when it was set aside, not when it was saved
    except OSError:
        pass
    prune_expired()
    return target


_EXPIRED_KEEP_SECONDS = 7 * 24 * 3600


def prune_expired(now=None):
    """Delete set-aside .expired sessions older than seven days. Never raises."""
    import time

    now = time.time() if now is None else now
    try:
        names = os.listdir(config.APPLY_SESSIONS_DIR)
    except OSError:
        return
    for name in names:
        if not name.endswith(".json.expired"):
            continue
        path = os.path.join(config.APPLY_SESSIONS_DIR, name)
        try:
            if now - os.path.getmtime(path) > _EXPIRED_KEEP_SECONDS:
                os.remove(path)
        except OSError:
            pass
