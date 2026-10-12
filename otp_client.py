"""
Thin HTTP client for the local OTP broker (POST /v1/wait on 127.0.0.1:8765).

Returns the broker's code/link dict, or None when there is nothing to return. Never raises, and
never logs a code or link value.
"""

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

BROKER_URL = "http://127.0.0.1:8765"


TOKEN_FILE_DEFAULT = "/etc/jobagent24/otp_broker_token"

_token_cache = {}


class BrokerUnreachable(Exception):
    pass


# ── Auth token ─────────────────────────────────────────────────────────────────

def _load_token():
    """Bearer token from OTP_BROKER_TOKEN_FILE, cached per path once found; None when unavailable."""
    path = os.environ.get("OTP_BROKER_TOKEN_FILE", TOKEN_FILE_DEFAULT)
    if path in _token_cache:
        return _token_cache[path]
    try:
        with open(path) as fh:
            token = fh.read().strip()
    except OSError as exc:
        log.warning(f"[APPLY-VERIFY] | otp broker token unreadable ({type(exc).__name__}); sending no Authorization header")
        return None
    if not token:
        log.warning("[APPLY-VERIFY] | otp broker token file is empty; sending no Authorization header")
        return None
    _token_cache[path] = token
    return token


# ── Transport ──────────────────────────────────────────────────────────────────

def _post(path, payload, timeout):
    """(status, parsed JSON or None). Raises BrokerUnreachable when no HTTP answer came back."""
    headers = {"Content-Type": "application/json"}
    token = _load_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        BROKER_URL + path, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise BrokerUnreachable(type(exc).__name__) from exc


# ── Public API ─────────────────────────────────────────────────────────────────

def get_verification_code(sender_domains, since, timeout_seconds=60, kind="otp",
                          raise_unreachable=False):
    """Dict from the broker on 200; None on timeout (408), conflict (409) or an unreachable broker.

    With raise_unreachable=True an unreachable broker raises BrokerUnreachable instead, so a
    caller can fall back to another source.
    """
    payload = {
        "sender_domains": list(sender_domains),
        "after": since.isoformat(),
        "kind": kind,
        "timeout_s": timeout_seconds,
    }
    try:
        status, body = _post("/v1/wait", payload, timeout_seconds + 5)
        if status == 401:
            log.warning("[APPLY-VERIFY] | otp broker reachable but unauthorized - check token file")
            raise BrokerUnreachable("401 unauthorized")
    except BrokerUnreachable as exc:
        log.warning(f"[APPLY-VERIFY] | otp broker unreachable: {exc}")
        if raise_unreachable:
            raise
        return None
    if status == 200 and isinstance(body, dict):
        return body
    if status == 408:
        return None
    if status == 409:
        log.warning("[APPLY-VERIFY] | otp broker returned 409 (conflict)")
        return None
    log.warning(f"[APPLY-VERIFY] | otp broker returned unexpected status {status}")
    return None
