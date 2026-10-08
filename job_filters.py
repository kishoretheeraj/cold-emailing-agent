"""
Zero-token triage for sourced postings (spec 2026-10-08 fifty-a-day §3.2): title, seniority,
location, posting age and sponsorship. Runs before a posting is saved, so nothing that fails here
ever costs a fit-judge call, a resume build or a browser session. Pure: no I/O.

Preferences come from the `job_search_preferences` prompts row (JSON, edited on the Prompts page),
merged over DEFAULT_PREFERENCES; a missing or malformed value falls back to its default.

The location rules are Career-Ops' (github.com/santifer/career-ops, scan.mjs, MIT), ported:
keywords match on word boundaries ("india" never drops "Indianapolis"); tiers are checked in the
order block_hard > always_allow > block > allow; naming the US in always_allow also admits USPS
state names and ", XX" codes ("Dublin, OH" is not Dublin, Ireland); a Workday posting's rolled-up
"5 Locations" is read from its URL; and an explicit remote marker in the title can rescue an
office-only location, never a blocked one. A multi-location posting passes when any of its
locations does.
"""

import copy
import json
import re
from datetime import datetime, timezone

import application_quality

DEFAULT_PREFERENCES = {
    "titles": {
        "include": ["product manager", "associate product manager", "apm", "pm", "product analyst",
                    "product owner", "technical product manager", "product management",
                    "product operations", "product associate", "product line", "program manager",
                    "business analyst", "strategy and operations", "strategy & operations",
                    "strategy analyst", "strategy associate", "business operations"],
        "exclude": ["designer", "engineer", "developer", "account executive", "recruiter",
                    "contract", "contractor", "temporary", "part time", "part-time"],
    },
    "seniority_exclude": ["senior", "sr", "staff", "principal", "lead", "director", "head", "vp",
                          "vice president", "chief", "group", "intern", "internship", "co-op"],
    # Early-career titles that contain a seniority word (banking/consulting "Senior Associate").
    "seniority_allow": ["senior associate"],
    "locations": {
        "always_allow": ["united states", "usa", "u.s.", "us remote", "remote us", "remote - us",
                         "sf", "nyc", "bay area", "silicon valley"],
        "allow": ["remote", "anywhere"],
        "block": [],
        "block_hard": ["india", "united kingdom", "uk", "england", "ireland", "canada", "germany",
                       "france", "netherlands", "spain", "poland", "singapore", "australia", "japan",
                       "china", "brazil", "mexico", "israel", "philippines", "emea", "apac", "latam"],
    },
    "max_posting_age_days": 30,
    "per_company_cap_30d": 3,
    "daily_submit_cap": 50,
    "skip_no_sponsorship": True,
}

_LIST_KEYS = (("titles", "include"), ("titles", "exclude"), ("seniority_exclude",), ("seniority_allow",),
              ("locations", "always_allow"), ("locations", "allow"), ("locations", "block"),
              ("locations", "block_hard"))
_INT_KEYS = ("max_posting_age_days", "per_company_cap_30d", "daily_submit_cap")


def _get(tree, path):
    for key in path:
        tree = tree.get(key) if isinstance(tree, dict) else None
    return tree


def _set(tree, path, value):
    for key in path[:-1]:
        tree = tree.setdefault(key, {})
    tree[path[-1]] = value


def load_preferences(prompts):
    """DEFAULT_PREFERENCES with the valid parts of the job_search_preferences row laid over it."""
    prefs = copy.deepcopy(DEFAULT_PREFERENCES)
    try:
        raw = json.loads((prompts or {}).get("job_search_preferences") or "{}")
    except (TypeError, ValueError):
        return prefs
    if not isinstance(raw, dict):
        return prefs
    for path in _LIST_KEYS:
        value = _get(raw, path)
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            _set(prefs, path, [v.strip().lower() for v in value if v.strip()])
    for key in _INT_KEYS:
        if isinstance(raw.get(key), int) and not isinstance(raw.get(key), bool) and raw[key] >= 0:
            prefs[key] = raw[key]
    if isinstance(raw.get("skip_no_sponsorship"), bool):
        prefs["skip_no_sponsorship"] = raw["skip_no_sponsorship"]
    return prefs


# ── Titles ─────────────────────────────────────────────────────────────────────

_WORD = r"[^\W_]"


def _keyword_re(keyword):
    stem = keyword.startswith("stem:")
    words = [re.escape(w) for w in re.split(r"[^\w.]+", keyword[5:] if stem else keyword) if w]
    if not words:
        return None
    body = r"[\W_]+".join(words)
    end = "" if stem else rf"(?!{_WORD})"
    return re.compile(rf"(?<!{_WORD}){body}{end}", re.IGNORECASE)


def _any_keyword(text, keywords):
    for keyword in keywords:
        pattern = _keyword_re(keyword)
        if pattern and pattern.search(text):
            return True
    return False


def title_reason(title, prefs):
    """None when the title is a target role; 'seniority' or 'title' otherwise."""
    if not isinstance(title, str) or not title.strip():
        return "title"
    unranked = title
    for phrase in prefs["seniority_allow"]:
        pattern = _keyword_re(phrase)
        if pattern:
            unranked = pattern.sub(" ", unranked)
    if _any_keyword(unranked, prefs["seniority_exclude"]):
        return "seniority"
    if not _any_keyword(title, prefs["titles"]["include"]) or _any_keyword(title, prefs["titles"]["exclude"]):
        return "title"
    return None


# ── Locations ──────────────────────────────────────────────────────────────────

_US_TOKENS = {"united states", "usa", "u.s.", "u.s.a."}
_US_STATES = (
    ("alabama", "al"), ("alaska", "ak"), ("arizona", "az"), ("arkansas", "ar"), ("california", "ca"),
    ("colorado", "co"), ("connecticut", "ct"), ("delaware", "de"), ("florida", "fl"), ("georgia", "ga"),
    ("hawaii", "hi"), ("idaho", "id"), ("illinois", "il"), ("indiana", "in"), ("iowa", "ia"),
    ("kansas", "ks"), ("kentucky", "ky"), ("louisiana", "la"), ("maine", "me"), ("maryland", "md"),
    ("massachusetts", "ma"), ("michigan", "mi"), ("minnesota", "mn"), ("mississippi", "ms"),
    ("missouri", "mo"), ("montana", "mt"), ("nebraska", "ne"), ("nevada", "nv"), ("new hampshire", "nh"),
    ("new jersey", "nj"), ("new mexico", "nm"), ("new york", "ny"), ("north carolina", "nc"),
    ("north dakota", "nd"), ("ohio", "oh"), ("oklahoma", "ok"), ("oregon", "or"), ("pennsylvania", "pa"),
    ("rhode island", "ri"), ("south carolina", "sc"), ("south dakota", "sd"), ("tennessee", "tn"),
    ("texas", "tx"), ("utah", "ut"), ("vermont", "vt"), ("virginia", "va"), ("washington", "wa"),
    ("west virginia", "wv"), ("wisconsin", "wi"), ("wyoming", "wy"), ("district of columbia", "dc"),
)
_REMOTE_TITLE = re.compile(r"(?<![a-z])remote(?=$|\s*[^a-z\s]|\s+in\b)")
_REMOTE_NEGATED = re.compile(r"\b(?:non|not|no)[^a-z]*remote")
_SPLIT_LOCATIONS = re.compile(r"\s*(?:[|;•·\n]|\s+or\s+)\s*")


def _location_matcher(keyword):
    escaped = re.escape(keyword)
    start = rf"(?<!{_WORD})" if re.match(r"\w", keyword) else ""
    end = rf"(?!{_WORD})" if re.search(r"\w$", keyword) else ""
    pattern = re.compile(start + escaped + end)
    return lambda text: bool(pattern.search(text))


def _state_code_matcher(code):
    pattern = re.compile(rf",\s*{code}(?!{_WORD})|(?:^|[\W_]){code}[\W_]*$")
    return lambda text: bool(pattern.search(text))


def _matchers(keywords):
    return [_location_matcher(k) for k in keywords]


def workday_location_hint(url):
    """The location segment of a Workday posting URL (…/job/Austin-TX/Title_R1 -> 'austin tx'), or ''."""
    match = re.search(r"myworkday(?:jobs|site)\.com/.*?/job/([^/?#]+)/[^/?#]+", str(url or ""), re.IGNORECASE)
    return re.sub(r"[-_+]+", " ", match.group(1)).strip().lower() if match else ""


def title_signals_remote(title):
    lower = str(title or "").lower()
    return bool(lower) and not _REMOTE_NEGATED.search(lower) and bool(_REMOTE_TITLE.search(lower))


def _one_location_ok(text, title, tiers):
    always_allow, allow, block, block_hard = tiers
    if any(m(text) for m in block_hard):
        return False
    if any(m(text) for m in always_allow):
        return True
    if any(m(text) for m in block):
        return False
    if not allow:
        return True
    return any(m(text) for m in allow) or title_signals_remote(title)


def location_ok(location, url, title, prefs):
    """True when any of the posting's locations is acceptable. No location at all passes."""
    locs = prefs["locations"]
    always = _matchers(locs["always_allow"])
    if _US_TOKENS & set(locs["always_allow"]):
        always += [m for name, code in _US_STATES for m in (_location_matcher(name), _state_code_matcher(code))]
    tiers = (always, _matchers(locs["allow"]), _matchers(locs["block"]), _matchers(locs["block_hard"]))
    raw = location if isinstance(location, list) else [location]
    parts = [p.strip().lower() for item in raw if isinstance(item, str)
             for p in _SPLIT_LOCATIONS.split(item) if p.strip()]
    hint = workday_location_hint(url)
    if hint:
        parts.append(hint)
    if not parts:
        return True
    return any(_one_location_ok(part, title, tiers) for part in parts)


# ── Posting age and sponsorship ────────────────────────────────────────────────

def _as_datetime(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if isinstance(value, str):
        try:
            return _as_datetime(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def fresh_enough(posted_at, prefs, now=None):
    """False only when the posting has a known date older than max_posting_age_days."""
    posted = _as_datetime(posted_at)
    limit = prefs["max_posting_age_days"]
    if posted is None or not limit:
        return True
    return ((now or datetime.now(timezone.utc)) - posted).days <= limit


_NO_SPONSORSHIP_LABELS = re.compile(r"does not offer sponsorship|citizenship is required|no sponsorship",
                                    re.IGNORECASE)


def needs_sponsorship(eligibility):
    return str((eligibility or {}).get("requires_visa_sponsorship", "")).strip().lower().startswith("y")


def sponsorship_ok(job, prefs, eligibility):
    """False when the candidate needs sponsorship and the source label or the description rules it out."""
    if not prefs["skip_no_sponsorship"] or not needs_sponsorship(eligibility):
        return True
    if _NO_SPONSORSHIP_LABELS.search(str(job.get("sponsorship") or "")):
        return False
    return not application_quality.knockout_reasons(job.get("description") or "", eligibility)


def reject_reason(job, prefs, eligibility, now=None):
    """Why a sourced posting is not worth saving ('seniority', 'title', 'location', 'stale',
    'no_sponsorship'), or None to keep it."""
    reason = title_reason(job.get("title"), prefs)
    if reason:
        return reason
    if not location_ok(job.get("location"), job.get("url"), job.get("title"), prefs):
        return "location"
    if not fresh_enough(job.get("posted_at"), prefs, now=now):
        return "stale"
    if not sponsorship_ok(job, prefs, eligibility):
        return "no_sponsorship"
    return None
