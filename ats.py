"""
Reads a target company's public applicant-tracking-system job board and returns
normalized active job postings for the research pipeline.

Best-effort by contract: every network call is wrapped and fetch_jobs returns []
on any failure. Nothing here may raise into research.py's pipeline.

Self-contained: no db, gmail, or emailer import, and nothing outside the standard
library, so a Supabase or Anthropic outage can never reach this module.
"""

import html
import json
import logging
import re
import urllib.error
import urllib.request

import config

log = logging.getLogger(__name__)

_USER_AGENT = "cold-email-agent/1.0 (research; contact via the sending address)"

# Trailing legal-entity noise that is never part of an ATS slug.
_CORPORATE_SUFFIXES = frozenset({
    "inc", "incorporated", "llc", "llp", "ltd", "limited", "corp",
    "corporation", "co", "company", "plc", "gmbh", "ag", "sa", "nv", "bv",
})


# ── Slug derivation ────────────────────────────────────────────────────────────

# Deliberately NOT entity_resolution.normalize(). That function replaces
# punctuation with spaces to keep alias groups reachable for fuzzy matching; a
# URL slug needs the opposite treatment, and coupling them would mean a slug
# tweak silently reshapes visa entity matching.
def _slug_candidates(company):
    if not company or not isinstance(company, str):
        return []

    cleaned = re.sub(r"[^a-z0-9]+", " ", company.lower()).strip()
    tokens = [t for t in cleaned.split() if t]
    while len(tokens) > 1 and tokens[-1] in _CORPORATE_SUFFIXES:
        tokens.pop()
    if not tokens:
        return []

    joined = "".join(tokens)
    candidates = [joined]
    hyphenated = "-".join(tokens)
    if hyphenated != joined:
        candidates.append(hyphenated)
    return candidates[:config.ATS_MAX_SLUG_CANDIDATES]


# ── HTML stripping ─────────────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


# Greenhouse returns HTML-escaped HTML, so this unescapes twice: once to expose
# the markup, once to resolve entities that were double-escaped inside it.
def _strip_html(text):
    if not text or not isinstance(text, str):
        return ""
    without_tags = _TAG_RE.sub(" ", html.unescape(text))
    return _WS_RE.sub(" ", html.unescape(without_tags)).strip()


# ── Provider payload parsing ───────────────────────────────────────────────────

def _job(title, location, url, description, source):
    title = title.strip() if isinstance(title, str) else ""
    if not title:
        return None
    return {
        "title": title,
        "location": location.strip() if isinstance(location, str) else "",
        "url": url.strip() if isinstance(url, str) else "",
        "description": (description or "")[:config.ATS_MAX_DESCRIPTION_CHARS],
        "source": source,
    }


def _entries(payload, key):
    if key is None:
        items = payload
    else:
        items = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _parse_greenhouse(payload):
    jobs = []
    for item in _entries(payload, "jobs"):
        location = item.get("location")
        job = _job(
            item.get("title"),
            location.get("name") if isinstance(location, dict) else location,
            item.get("absolute_url"),
            _strip_html(item.get("content")),
            "greenhouse",
        )
        if job:
            jobs.append(job)
    return jobs


def _parse_ashby(payload):
    jobs = []
    for item in _entries(payload, "jobs"):
        description = item.get("descriptionPlain")
        if not isinstance(description, str) or not description.strip():
            description = _strip_html(item.get("descriptionHtml"))
        job = _job(
            item.get("title"),
            item.get("location"),
            item.get("jobUrl"),
            _WS_RE.sub(" ", description).strip() if description else "",
            "ashby",
        )
        if job:
            jobs.append(job)
    return jobs


def _parse_lever(payload):
    jobs = []
    for item in _entries(payload, None):
        categories = item.get("categories")
        description = item.get("descriptionPlain")
        if not isinstance(description, str) or not description.strip():
            description = _strip_html(item.get("description"))
        job = _job(
            item.get("text"),
            categories.get("location") if isinstance(categories, dict) else None,
            item.get("hostedUrl"),
            _WS_RE.sub(" ", description).strip() if description else "",
            "lever",
        )
        if job:
            jobs.append(job)
    return jobs


# ── Provider cascade ───────────────────────────────────────────────────────────

# Order matters and the first non-empty result wins. A company lives on exactly
# one ATS, so querying the rest after a hit is wasted latency inside a
# per-contact loop.
_PROVIDERS = (
    ("greenhouse",
     "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
     _parse_greenhouse),
    ("ashby",
     "https://api.ashbyhq.com/posting-api/job-board/{slug}",
     _parse_ashby),
    ("lever",
     "https://api.lever.co/v0/postings/{slug}?mode=json",
     _parse_lever),
)


def _http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=config.ATS_TIMEOUT_SECONDS) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", errors="replace"))


def _try_provider(source, template, parser, slug):
    url = template.format(slug=slug)
    try:
        payload = _http_get_json(url)
    except urllib.error.HTTPError as exc:
        # 404 is the documented clean miss for all three providers, not an error.
        if getattr(exc, "code", None) != 404:
            log.warning(f"[RESEARCH-A] | {slug} | {source} | http error: {exc}")
        return []
    except Exception as exc:
        log.warning(f"[RESEARCH-A] | {slug} | {source} | fetch failed: {exc}")
        return []

    try:
        return parser(payload)
    except Exception as exc:
        log.warning(f"[RESEARCH-A] | {slug} | {source} | parse failed: {exc}")
        return []


# ── Relevance ranking ──────────────────────────────────────────────────────────

# Seniority and generic title words carry no signal about function, which is the
# only thing being matched here.
_TITLE_STOPWORDS = frozenset({
    "a", "an", "and", "at", "for", "in", "of", "on", "the", "to", "with",
    "senior", "sr", "junior", "jr", "staff", "lead", "principal", "head",
    "vp", "svp", "evp", "director", "manager", "chief", "associate", "intern",
    "i", "ii", "iii", "iv", "level", "remote",
})


def _tokens(text):
    if not text or not isinstance(text, str):
        return set()
    words = re.split(r"[^a-z0-9]+", text.lower())
    return {w for w in words if w and w not in _TITLE_STOPWORDS}


def _rank_jobs(jobs, role, max_jobs):
    role_tokens = _tokens(role)
    if not role_tokens:
        return jobs[:max_jobs]
    ordered = sorted(
        enumerate(jobs),
        key=lambda pair: (-len(role_tokens & _tokens(pair[1].get("title"))), pair[0]),
    )
    return [job for _, job in ordered][:max_jobs]


# ── Public entry point ─────────────────────────────────────────────────────────

def fetch_jobs(company, role=None, max_jobs=None):
    """
    Return up to `max_jobs` (default config.ATS_MAX_JOBS) active job postings for
    `company`, ranked by relevance to `role` when given, from the first public ATS
    that recognises the company.

    Returns [] when the company is not on a supported ATS, when ATS_ENABLED is
    off, or on any failure. Never raises -- enrichment must never cost a draft.
    """
    try:
        if not config.ATS_ENABLED:
            return []

        cap = max_jobs if max_jobs is not None else config.ATS_MAX_JOBS

        candidates = _slug_candidates(company)
        if not candidates:
            return []

        for source, template, parser in _PROVIDERS:
            for slug in candidates:
                jobs = _try_provider(source, template, parser, slug)
                if jobs:
                    ranked = _rank_jobs(jobs, role, cap)
                    log.info(
                        f"[RESEARCH-A] | {company} | source={source} | "
                        f"slug={slug} | found={len(jobs)} | kept={len(ranked)}"
                    )
                    return ranked

        log.info(f"[RESEARCH-A] | {company} | no_ats_match | candidates={len(candidates)}")
        return []
    except Exception as exc:
        log.warning(f"[RESEARCH-A] | {company} | unexpected error: {exc}")
        return []
