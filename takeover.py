"""
Human takeover for the Beelink apply worker (spec 2026-10-08 §4.2).

When a page shows something only a person should handle (a CAPTCHA, an SMS code, a login it
cannot complete), the worker keeps its lease and its browser open, records a takeover request on
the row, and waits. The queue card shows "Needs you" with a link to the noVNC view of display :1
and an "I'm done" button (takeover_continue). The worker heartbeats while it waits, so lease
recovery never mistakes it for dead. Nothing here solves or bypasses a challenge; it only detects
one and waits for a human.
"""

import logging
import time

import config
import db

log = logging.getLogger(__name__)

# Frames and widgets of the common challenge providers. Detection only.
_CHALLENGE_SELECTORS = (
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "iframe[src*='challenges.cloudflare.com']",
    "iframe[title*='captcha' i]",
    ".g-recaptcha",
    ".h-captcha",
    ".cf-turnstile",
    "[data-sitekey]",
)


class TakeoverLost(Exception):
    pass


def challenge_present(page):
    """True when a visible CAPTCHA-style challenge is on the page. Never raises."""
    for selector in _CHALLENGE_SELECTORS:
        try:
            locator = page.locator(selector)
            for i in range(min(locator.count(), 5)):
                if locator.nth(i).is_visible():
                    return True
        except Exception:
            continue
    return False


def await_human(job_id, lease, kind, reason, timeout_seconds=None, poll_seconds=None):
    """Ask for a human and wait. True once they press I'm done, False on timeout. Raises
    TakeoverLost when the lease is gone (recovered or re-claimed): the caller must stop touching
    the row. The request is always closed before returning False."""
    timeout_seconds = config.APPLY_TAKEOVER_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    poll_seconds = config.APPLY_TAKEOVER_POLL_SECONDS if poll_seconds is None else poll_seconds
    if not db.request_takeover(job_id, lease, kind, reason):
        raise TakeoverLost(f"takeover request refused for row {job_id}: lease no longer held")
    log.info(f"[APPLY-TAKEOVER] | {job_id} | waiting for a human | {kind} | {reason}")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        time.sleep(poll_seconds)
        db.heartbeat_application(job_id, lease)
        state = db.takeover_state(job_id, lease)
        if state == "continued":
            db.clear_takeover(job_id, lease)
            log.info(f"[APPLY-TAKEOVER] | {job_id} | human finished, resuming")
            return True
        if state == "lost":
            raise TakeoverLost(f"row {job_id} lease was lost while waiting for a human")
    try:
        db.clear_takeover(job_id, lease)
    except Exception as exc:
        log.warning(f"[APPLY-TAKEOVER] | {job_id} | could not close the request: {exc}")
    log.warning(f"[APPLY-TAKEOVER] | {job_id} | nobody took over within {timeout_seconds}s")
    return False
