"""Canonical job dict for every ingestion consumer.

Producers disagree. job_sources emits {company, title, url, location, posted_at, description,
sponsorship, source}; the ats_api clients emit {absolute_url | hosted_url, content | description,
updated_at | created_at, ...} with no company. canonical_job() maps either to the first shape at the
consumer boundary; the producers are untouched.
"""

import html
import re

CANONICAL_KEYS = ("company", "title", "url", "location", "posted_at", "description", "sponsorship", "source")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


# ── Normalizers ────────────────────────────────────────────────────────────────

def _s(value):
    return value.strip() if isinstance(value, str) else ""


def _plain(value):
    if not isinstance(value, str) or not value:
        return ""
    text = _TAG_RE.sub(" ", html.unescape(value))
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def canonical_job(raw, company="", source=""):
    """Return the canonical job dict for either producer shape, or None for non-dict input.
    A dict already in canonical shape (it has "url") is returned unchanged, same object, so callers
    that mutate it (job_sourcing.save -> add_detail) keep working. `company` and `source` fill what
    the raw posting does not carry (the ats_api clients know the board token, not the display name)."""
    if not isinstance(raw, dict):
        return None
    if "url" in raw:
        return raw
    return {
        "company": _s(raw.get("company")) or _s(company),
        "title": _s(raw.get("title")) or _s(raw.get("text")),
        "url": _s(raw.get("absolute_url")) or _s(raw.get("hosted_url")) or _s(raw.get("apply_url")),
        "location": _s(raw.get("location")),
        "posted_at": raw.get("posted_at") or raw.get("updated_at") or raw.get("created_at") or None,
        "description": _plain(raw.get("description") or raw.get("content") or ""),
        "sponsorship": raw.get("sponsorship"),
        "source": _s(raw.get("source")) or _s(source),
    }


def canonical_jobs(items, company="", source=""):
    return [job for job in (canonical_job(i, company, source) for i in items or []) if job]
