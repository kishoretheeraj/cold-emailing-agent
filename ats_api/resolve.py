"""Resolve a Greenhouse/Lever job URL to its posting through the ats_api clients.

fetch_posting() never raises: any miss or failure returns None and the caller falls back to the
snapshot it already has (the Playwright path never depends on this).
"""

import logging
from urllib.parse import urlparse

from ats_api.greenhouse_api import GreenhouseAPI
from ats_api.lever_api import LeverAPI
from job_contract import canonical_job

log = logging.getLogger(__name__)

_GREENHOUSE_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"}
_LEVER_HOSTS = {"jobs.lever.co", "jobs.eu.lever.co"}


# ── URL parsing ────────────────────────────────────────────────────────────────

def board_ref(job_url):
    """(provider, board_token, posting_id) for a Greenhouse or Lever posting URL, else None.
    Matches the hostname exactly; a company page with only ?gh_jid= has no token and yields None."""
    try:
        parsed = urlparse(job_url or "")
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    segs = [s for s in parsed.path.split("/") if s]
    if host in _GREENHOUSE_HOSTS and len(segs) >= 3 and segs[1] == "jobs" and segs[2].isdigit():
        return "greenhouse", segs[0], segs[2]
    if host in _LEVER_HOSTS and len(segs) >= 2:
        return "lever", segs[0], segs[1]
    return None


# ── Fetch ──────────────────────────────────────────────────────────────────────

def fetch_posting(job_url):
    """Canonical job dict for the posting, or None (unsupported URL, closed posting, any failure)."""
    ref = board_ref(job_url)
    if ref is None:
        return None
    provider, token, posting_id = ref
    try:
        if provider == "greenhouse":
            with GreenhouseAPI(token) as api:
                raw = api.get_job(posting_id)
        else:
            with LeverAPI(token) as api:
                raw = api.get_posting(posting_id)
    except Exception as exc:
        log.warning(f"[ATS-API] | {provider} | {token} | fetch failed, falling back: {exc}")
        return None
    return canonical_job(raw, company=token, source=provider) if raw else None
