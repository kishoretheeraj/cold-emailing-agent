"""
One canonical identity per real job, whatever URL spelling a source used (spec 2026-10-08
fifty-a-day §3.1). Pure: no I/O, no logging, never raises.

  identify(url)   -> {platform, tenant, job_id, job_key, canonical_url, apply_url}
  company_key(s)  -> company name without legal suffixes and punctuation
  title_key(s)    -> "title identity": the sorted set of title words
  fingerprint(s)  -> 64-bit SimHash of a job description (16 hex chars), "" when too short

Platforms are decided by hostname, never by substring: "clever.com" is not Lever and
"?src=greenhouse.io" is not Greenhouse. Greenhouse, Lever and Ashby job ids are globally unique,
so their keys omit the board; a Workday requisition id is unique per tenant (any site).

The title-identity rule, the tracking-parameter cleanup and the SimHash construction (3-word
shingles, SHA-1, 64 bits) follow Career-Ops (github.com/santifer/career-ops, MIT), ported to
Python: two postings are one role only when their titles carry the same set of words, so
"Product Manager - Berlin" and "- Munich" stay two openings.
"""

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import config

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_UUID_RE = re.compile(rf"^{_UUID}$", re.IGNORECASE)
_LOCALE_RE = re.compile(r"^[a-z]{2}-[a-z]{2}$", re.IGNORECASE)
_WD_SHARD_RE = re.compile(r"^wd\d+$", re.IGNORECASE)
_ORACLE_HOST_RE = re.compile(r"\.fa(\.[a-z0-9-]+)*\.oraclecloud\.com$")

# Query parameters that only say where a click came from. Real parameters (an id, gh_jid) stay.
_TRACKING_PARAMS = {
    "gh_src", "source", "src", "ref", "referrer", "refid", "trk", "trackingid", "embed", "iis",
    "iisn", "fbclid", "gclid", "mc_cid", "mc_eid", "_ga", "jobboardsource", "sourcetype", "lever-via",
}
_TRACKING_PREFIXES = ("utm_", "lever-")


def _empty(url=""):
    return {"platform": "generic", "tenant": None, "job_id": None, "job_key": None,
            "canonical_url": url or None, "apply_url": url or None}


def _clean_query(query):
    kept = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True)
            if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith(_TRACKING_PREFIXES)]
    return urlencode(sorted(kept))


def _segments(path):
    return [s for s in path.split("/") if s]


def _host_is(host, domain):
    return host == domain or host.endswith("." + domain)


def _aggregator(host, path):
    for entry in config.APPLY_AGENT_AGGREGATOR_DOMAINS:
        domain, _, prefix = entry.partition("/")
        if _host_is(host, domain) and path.lower().startswith("/" + prefix if prefix else "/"):
            return True
    return False


def _result(platform, job_id, job_key, canonical_url, apply_url, tenant=None):
    return {"platform": platform, "tenant": tenant, "job_id": job_id, "job_key": job_key,
            "canonical_url": canonical_url, "apply_url": apply_url}


def _greenhouse(host, segs, query, generic_url):
    params = {k.lower(): v for k, v in parse_qsl(query)}
    eu = ".eu." in "." + host + "."
    if _host_is(host, "greenhouse.io"):
        board = job_id = None
        if len(segs) >= 3 and segs[1] == "jobs" and segs[2].isdigit():
            board, job_id = segs[0], segs[2]
        elif segs[:2] == ["embed", "job_app"] and params.get("token", "").isdigit():
            job_id, board = params["token"], params.get("for") or None
        if job_id:
            if segs[0] == "embed":
                # The embed URL renders only the form; keep it as the place to apply.
                embed = {"for": board, "token": job_id} if board else {"token": job_id}
                url = f"https://{host}/embed/job_app?{urlencode(embed)}"
            else:
                url = f"https://job-boards{'.eu' if eu else ''}.greenhouse.io/{board.lower()}/jobs/{job_id}"
            return _result("greenhouse", job_id, f"greenhouse:{job_id}", url, url, tenant=board)
        return None
    if params.get("gh_jid", "").isdigit():
        # A company careers page embedding the Greenhouse form: the job id is global, and the
        # embed URL opens the form itself.
        job_id = params["gh_jid"]
        return _result("greenhouse", job_id, f"greenhouse:{job_id}", generic_url,
                       f"https://boards.greenhouse.io/embed/job_app?token={job_id}")
    return None


def _lever(host, segs):
    if host not in ("jobs.lever.co", "jobs.eu.lever.co") or len(segs) < 2 or not _UUID_RE.match(segs[1]):
        return None
    slug, job_id = segs[0], segs[1].lower()
    url = f"https://{host}/{slug}/{job_id}"
    return _result("lever", job_id, f"lever:{job_id}", url, url + "/apply", tenant=slug)


def _ashby(host, segs, query):
    if host == "jobs.ashbyhq.com" and len(segs) >= 2 and _UUID_RE.match(segs[1]):
        org, job_id = segs[0], segs[1].lower()
        url = f"https://jobs.ashbyhq.com/{org}/{job_id}"
        return _result("ashby", job_id, f"ashby:{job_id}", url, url + "/application", tenant=org)
    return None


def _ashby_embedded(query):
    job_id = {k.lower(): v for k, v in parse_qsl(query)}.get("ashby_jid", "")
    return job_id.lower() if _UUID_RE.match(job_id) else None


def _workday(host, segs, original):
    if not (_host_is(host, "myworkdayjobs.com") or _host_is(host, "myworkdaysite.com")):
        return None
    labels = host.split(".")
    tenant = labels[0] if len(labels) >= 3 and not _WD_SHARD_RE.match(labels[0]) else ""
    rest = [s for s in segs if not _LOCALE_RE.match(s)]
    if rest[:1] == ["recruiting"] and len(rest) >= 3:
        tenant, rest = rest[1], rest[2:]
    site = rest[0] if rest else ""
    req = None
    for marker in ("job", "details"):
        if marker in rest and rest.index(marker) < len(rest) - 1:
            last = rest[-1]
            req = last.rsplit("_", 1)[-1] if "_" in last else last
            break
    # One requisition can be published under several sites of a tenant (careers page, Indeed feed,
    # ...): the requisition id is the identity, the site is not (Career-Ops #3439).
    key = f"workday:{tenant}:{req}".lower() if req else None
    return _result("workday", req, key, original, original, tenant=tenant or None)


def _by_id(host, segs, platform):
    if platform == "smartrecruiters" and _host_is(host, "smartrecruiters.com") and len(segs) >= 2:
        match = re.match(r"^(\d{6,})", segs[1])
        if match:
            return match.group(1), f"smartrecruiters:{match.group(1)}"
    if platform == "workable" and _host_is(host, "workable.com") and "j" in segs:
        index = segs.index("j")
        if index + 1 < len(segs):
            code = segs[index + 1].lower()
            return code, f"workable:{code}"
    if platform == "oracle" and "job" in segs:
        index = segs.index("job")
        if index + 1 < len(segs) and segs[index + 1].isdigit():
            return segs[index + 1], f"oracle:{host.split('.')[0]}:{segs[index + 1]}"
    if platform == "icims" and "jobs" in segs:
        index = segs.index("jobs")
        if index + 1 < len(segs) and segs[index + 1].isdigit():
            return segs[index + 1], f"icims:{host.split('.')[0]}:{segs[index + 1]}"
    return None, None


def identify(url):
    """Canonical identity of a job URL. Unknown or malformed input is 'generic' with no key."""
    if not isinstance(url, str) or not url.strip():
        return _empty()
    raw = url.strip()
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return _empty(raw)
    if parts.scheme.lower() not in ("http", "https") or not host or "." not in host:
        return _empty(raw)
    segs = _segments(parts.path)
    path = "/" + "/".join(segs)
    query = _clean_query(parts.query)
    generic_url = urlunsplit(("https", host, path if segs else "", query, ""))
    original = urlunsplit(("https", host, parts.path.rstrip("/") or "", "", ""))
    generic = _result("generic", None, f"url:{generic_url}", generic_url, generic_url)

    if _aggregator(host, parts.path):
        return dict(generic, platform="aggregator")
    for found in (_greenhouse(host, segs, parts.query, generic_url), _lever(host, segs),
                  _ashby(host, segs, parts.query), _workday(host, segs, original)):
        if found:
            return found
    embedded_ashby = _ashby_embedded(parts.query)
    if embedded_ashby:
        return dict(generic, job_id=embedded_ashby, job_key=f"ashby:{embedded_ashby}")
    for platform, matches in (("smartrecruiters", _host_is(host, "smartrecruiters.com")),
                              ("workable", _host_is(host, "workable.com")),
                              ("oracle", bool(_ORACLE_HOST_RE.search(host))),
                              ("icims", _host_is(host, "icims.com"))):
        if matches:
            job_id, key = _by_id(host, segs, platform)
            return dict(generic, platform=platform, job_id=job_id, job_key=key or generic["job_key"])
    return generic


# ── Company and title identity ─────────────────────────────────────────────────

_COMPANY_SUFFIXES = {"inc", "incorporated", "llc", "llp", "ltd", "limited", "corp", "corporation",
                     "co", "company", "plc", "gmbh", "ag", "sa", "nv", "bv", "holdings", "group"}
_TITLE_STOP = {"a", "an", "and", "the", "of", "for", "to", "in", "at", "with", "on"}


def _words(text):
    return [w for w in re.split(r"[^a-z0-9]+", str(text or "").lower().replace("&", " and ")) if w]


def company_key(name):
    """Company name folded for comparison: no legal suffixes, punctuation or leading 'The'."""
    words = _words(name)
    if words[:1] == ["the"]:
        words = words[1:]
    while len(words) > 1 and words[-1] in _COMPANY_SUFFIXES:
        words.pop()
    return "".join(w for w in words if w != "and") or ""


def title_key(title):
    """The set of a title's words. Same set means same role; a different city, level or
    specialty word means a different opening."""
    return " ".join(sorted({w for w in _words(title) if w not in _TITLE_STOP}))


# ── Description fingerprint ────────────────────────────────────────────────────

FINGERPRINT_MIN_TEXT = 200
REPOST_SIMILARITY = 0.9


def _normalize_text(text):
    text = re.sub(r"<[^>]*>", " ", str(text or "").lower())
    text = re.sub(r"&[a-z#0-9]+;", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    return re.sub(r"[\W_]+", " ", text).strip()


def fingerprint(text):
    """64-bit SimHash of a description over 3-word shingles, as 16 hex chars; '' when there is too
    little text to tell one posting from boilerplate."""
    normalized = _normalize_text(text)
    if len(normalized) < FINGERPRINT_MIN_TEXT:
        return ""
    tokens = normalized.split(" ")
    if len(tokens) < 3:
        return ""
    weights = [0] * 64
    for i in range(len(tokens) - 2):
        digest = hashlib.sha1(" ".join(tokens[i:i + 3]).encode()).digest()
        value = int.from_bytes(digest[:8], "big")
        for bit in range(64):
            weights[bit] += 1 if (value >> (63 - bit)) & 1 else -1
    result = 0
    for bit in range(64):
        if weights[bit] > 0:
            result |= 1 << (63 - bit)
    return f"{result:016x}"


def similarity(a, b):
    """1 - Hamming distance / 64 of two fingerprints; 0 when either is missing or malformed."""
    if not (isinstance(a, str) and isinstance(b, str) and re.fullmatch(r"[0-9a-f]{16}", a)
            and re.fullmatch(r"[0-9a-f]{16}", b)):
        return 0
    return 1 - bin(int(a, 16) ^ int(b, 16)).count("1") / 64
