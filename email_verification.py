"""
Email verification for application-site signups (spec 2026-10-08 §5).

Reads only the receipt inbox (RECEIPT_IMAP_*, the address the forms are filled with), only mail
from the expected sender domains, only mail that arrived after the action that triggered it, and
returns a code or a verification link whose host is on an explicit allow-list (the tenant's own
host). Not a general inbox reader, never exposed to a prompt or a model. Never raises.
"""

import logging
import re
import time
from datetime import timedelta
from email.utils import parseaddr
from urllib.parse import urlparse

import config
import gmail

log = logging.getLogger(__name__)

# A code shown near the words "code" or "verification": 6-8 digits (optionally split by a space)
# or 6-8 uppercase letters and digits with at least one digit. The letter form is case-sensitive:
# a lowercase run is ordinary text.
_CODE_NEAR_WORD = re.compile(
    r"(?:code|verification|verify|passcode|one-time)[^\n]{0,60}?\b(\d{3}\s?\d{3,5}|(?-i:(?=[A-Z0-9]*\d)[A-Z0-9]{6,8}))\b",
    re.IGNORECASE | re.DOTALL)
_CODE_ON_OWN_LINE = re.compile(r"^\s*(\d{3}\s?\d{3}|\d{6,8})\s*$", re.MULTILINE)
_URL = re.compile(r"https://[^\s<>\"')\]]+")
_LINK_PATH_HINT = re.compile(r"verif|activat|confirm", re.IGNORECASE)
# Clock skew between the mail server and this box.
_SKEW = timedelta(minutes=2)


def _sender_matches(sender, domains):
    address = parseaddr(sender or "")[1].lower()
    domain = address.rpartition("@")[2]
    return bool(domain) and any(domain == d or domain.endswith("." + d) for d in domains)


def _extract(body, allowed_link_hosts):
    # Codes are looked for with the URLs removed, so a token inside a link is never read as one.
    text = _URL.sub(" ", body)
    match = _CODE_NEAR_WORD.search(text) or _CODE_ON_OWN_LINE.search(text)
    if match:
        return {"code": re.sub(r"\s", "", match.group(1))}
    for url in _URL.findall(body):
        host = (urlparse(url).hostname or "").lower()
        if host in allowed_link_hosts and _LINK_PATH_HINT.search(url):
            return {"link": url}
    return None


def wait_for_verification(sender_domains, since, timeout_seconds=60, poll_seconds=5,
                          allowed_link_hosts=()):
    """{"code": ...} or {"link": ...} from the newest matching message after `since`, or None."""
    address, password = config.RECEIPT_IMAP_ADDRESS, config.RECEIPT_IMAP_APP_PASSWORD
    if not (address and password):
        log.info("[APPLY-VERIFY] | receipt inbox not configured")
        return None
    domains = tuple(d.lower() for d in sender_domains)
    hosts = {h.lower() for h in allowed_link_hosts}

    def want_body(msg):
        return not msg.get("is_reply") and _sender_matches(msg.get("from"), domains)

    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            messages = gmail.fetch_inbox_since(since.date(), address, password, want_body=want_body)
        except Exception as exc:
            log.warning(f"[APPLY-VERIFY] | inbox read failed: {exc}")
            messages = []
        fresh = sorted((m for m in messages if want_body(m) and m.get("body")
                        and m["date"] >= since - _SKEW),
                       key=lambda m: m["date"], reverse=True)
        for msg in fresh:
            found = _extract(msg["body"], hosts)
            if found:
                log.info(f"[APPLY-VERIFY] | found a {'code' if 'code' in found else 'link'}")
                return found
        if time.monotonic() + poll_seconds > deadline:
            return None
        time.sleep(poll_seconds)
