"""
Is a posting still open? Asked of the ATS's own public API, before a resume is built for it
(spec 2026-10-08 fifty-a-day §3.3). check(url) -> 'live' | 'closed' | 'unknown'. Never raises.

Conservative, after Career-Ops' liveness-api.mjs (github.com/santifer/career-ops, MIT): a false
'closed' drops a real job, so only a definitive signal says closed -- a 404/410 from Greenhouse's
per-job API or Workday's CXS detail, or an Ashby board that no longer lists the posting. Lever's
API 404s confidential postings whose page still works, so a Lever 404 is 'unknown'. Rate limits,
5xx, timeouts and sites without a public API are 'unknown' too, and the preview pass reads the
page itself later (application_quality.posting_closed_reason).
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

import config
import job_identity

log = logging.getLogger(__name__)

_USER_AGENT = "job-agent/1.0 (personal job search)"


def _http_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=config.SOURCING_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _endpoint(ident, url):
    platform, tenant, job_id = ident["platform"], ident["tenant"], ident["job_id"]
    if platform == "greenhouse" and tenant and job_id:
        return f"https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs/{job_id}", True
    if platform == "lever" and tenant and job_id:
        host = "api.eu.lever.co" if "jobs.eu.lever.co" in ident["canonical_url"] else "api.lever.co"
        return f"https://{host}/v0/postings/{tenant}/{job_id}", False
    if platform == "ashby" and tenant:
        return f"https://api.ashbyhq.com/posting-api/job-board/{tenant}", True
    if platform == "workday" and tenant:
        parts = urllib.parse.urlsplit(url)
        segs = [s for s in parts.path.split("/") if s]
        segs = [s for s in segs if not job_identity._LOCALE_RE.match(s)]
        if segs[:1] == ["recruiting"]:
            segs = segs[2:]
        if len(segs) >= 2:
            return f"https://{parts.hostname}/wday/cxs/{tenant}/{segs[0]}/{'/'.join(segs[1:])}", True
    return None, False


def check(url):
    """'live', 'closed' or 'unknown' for a posting URL."""
    try:
        ident = job_identity.identify(url)
        endpoint, not_found_is_closed = _endpoint(ident, url)
        if not endpoint:
            return "unknown"
        try:
            payload = _http_json(endpoint)
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410) and not_found_is_closed:
                return "closed"
            return "unknown"
        if ident["platform"] == "ashby":
            jobs = payload.get("jobs") if isinstance(payload, dict) else None
            if not isinstance(jobs, list):
                return "unknown"
            listed = any(isinstance(j, dict) and str(j.get("id", "")).lower() == ident["job_id"] for j in jobs)
            return "live" if listed else "closed"
        return "live"
    except Exception as exc:
        log.info(f"[LIVENESS] | {url} | {type(exc).__name__}: {exc}")
        return "unknown"

