"""Work-authorization / sponsorship question classifier and option picker.

Moved verbatim from apply_pipeline.py. The truth it answers from is fixed (WORK_AUTH_FACTS):
authorized to work in the US via OPT, needs sponsorship in the future (H-1B), none needed today.
Anything outside those facts (qualifiers, compound conditions, future authorization, other
countries, attestations) goes to review, never to inference.
"""
import re

# Confirmed facts the work-authorization answers are derived from (never guessed).
WORK_AUTH_FACTS = {"authorized_us_via_opt": True, "needs_sponsorship_future": True, "needs_sponsorship_now": False}

_AUTH_RE = re.compile(r"authori[sz]ed to work|legally authori[sz]ed|work authori[sz]ation|eligible to work|"
                      r"right to work|legally (?:permitted|entitled) to work", re.I)
_SPONSOR_RE = re.compile(r"sponsor", re.I)
_ATTEST_RE = re.compile(
    r"\b(?:certif\w*|attest\w*|acknowledg\w*|i\s+agree|agree\s+to|consent\w*|i\s+declare|declar\w*|affirm\w*|"
    r"under\s+penalty|true\s+and\s+(?:correct|complete|accurate)|terms\s+(?:and|&)\s+conditions|"
    r"privacy\s+(?:policy|notice))\b", re.I)
_COUNTRIES = (
    "canada", "united kingdom", "u.k.", "uk", "great britain", "britain", "england", "scotland", "wales",
    "ireland", "india", "germany", "france", "spain", "italy", "netherlands", "poland", "portugal", "sweden",
    "norway", "denmark", "finland", "switzerland", "austria", "belgium", "australia", "new zealand",
    "singapore", "japan", "china", "hong kong", "korea", "taiwan", "israel", "uae", "united arab emirates",
    "mexico", "brazil", "argentina", "colombia", "chile", "philippines", "vietnam", "thailand", "indonesia",
    "malaysia", "pakistan", "bangladesh", "nigeria", "kenya", "south africa", "egypt", "turkey", "russia",
    "ukraine", "czech republic", "czechia", "romania", "hungary", "greece", "europe", "european union",
    "eu", "eea", "emea", "apac", "latam",
    "peru", "venezuela", "ecuador", "uruguay", "costa rica", "panama", "saudi arabia", "qatar", "ghana",
    "morocco", "sri lanka", "nepal", "serbia", "bulgaria", "croatia", "slovakia", "lithuania", "latvia",
    "estonia", "luxembourg", "iceland", "cyprus", "malta",
)
_COUNTRY_RE = re.compile(r"(?<![\w.])(?:" + "|".join(re.escape(c) for c in _COUNTRIES) + r")(?![\w])", re.I)
_NEG_RE = re.compile(r"\b(?:not|no|never|none|neither|nor|unable|cannot|can\s*not|without|except|other\s+than)\b|n['’]t", re.I)
_AUTH_US_RE = re.compile(r"authori[sz]ed to work in (?:the )?(?:US|U\.S\.|U\.S\.A\.|USA|United States(?: of America)?)(?!\w)", re.I)
_WITHOUT_RE = re.compile(r"without\s+(?:visa\s+)?sponsorship", re.I)
_NOT_REQUIRE_RE = re.compile(r"not\s+require\s+(?:visa\s+)?sponsorship|no\s+need\s+for\s+sponsorship", re.I)
_REQUIRE_RE = re.compile(r"\b(?:requir\w*|need\w*)\b.*\bspons(?:or|ors|orship)\b|\bspons\w*\b.*\brequired?\b", re.I)
_WORK_RE = re.compile(r"\b(?:work|employ\w*)\b", re.I)
# Qualifiers that make the question about a stronger/other status than the facts cover (OPT is not
# permanent, not citizenship, not "any employer").
_STATUS_RE = re.compile(
    r"permanen\w*|indefinite\w*|citizen\w*|green\s*card|\bLPR\b|unrestricted|without\s+(?:any\s+)?restriction\w*|"
    r"no\s+restriction\w*|any\s+employer|work\s+permit|lifetime|unlimited", re.I)
# Temporal scope: "currently/now" marks the current status; any future wording marks the future need.
# Only an explicit "now/currently ... or in the future" combines the two. A bare "will" is future wording
# but does not cancel a current marker: "Will you currently need sponsorship?" still asks about today.
_CURRENT_RE = re.compile(r"\b(?:currently|presently|right\s+now|at\s+this\s+time|at\s+present|today|now)\b", re.I)
_NOW_OR_FUTURE_RE = re.compile(
    r"\b(?:now|currently|presently|today)\s+(?:or|and)\s+(?:in\s+)?(?:the\s+)?future\b", re.I)
_FUTURE_RE = re.compile(
    r"\bfuture\b|\blater\b|\beventually\b|\bwill\b|\bwould\b|\bever\b|\bat\s+any\s+(?:time|point)\b|\bafter\b|"
    r"\bupon\b|\bwithin\b|\bnext\b|\bgoing\s+forward\b|\bonce\b|\bbeyond\b|\bexpir\w*", re.I)
# Words that may surround a work-auth/sponsorship core without adding an assertion of their own.
# Anything else ("and at least 21 years old", "on an H-1B", "willing to relocate") is an extra
# condition that no known fact covers.
_FILLER = frozenset((
    "are you do does did will would can could is be am i we now currently presently at this time present today "
    "right or and in the future any point to work working able legally lawfully authorized authorised "
    "authorization authorisation eligible employment employ us u.s. u.s.a. usa united states america of for a an "
    "with without visa sponsorship sponsor require requires required need needs needed employer company our from "
    "please answer yes no have legal permitted entitled now ever u.s u.s.a".split()))
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9.'’\-]*", re.I)

SKIP = ("skip", None, None, "unrelated")


def _yn(flag):
    return "Yes" if flag else "No"


def _review(reason):
    return "review", None, None, reason


def _extra_conditions(q):
    """True when the question carries words beyond a plain work-auth / sponsorship ask."""
    for tok in _TOKEN_RE.findall(q.lower()):
        tok = tok.rstrip(".'’-") if tok not in ("u.s.", "u.s.a.") else tok
        if tok and tok not in _FILLER:
            return True
    return False


def classify_question(text, kind="select"):
    """Decide a work-authorization / sponsorship question from its MEANING.
    Returns (action, key, answer, reason): action is 'answer' (key + 'Yes'/'No'), 'review' (leave
    unanswered, report) or 'skip' (not a work-auth question). Never guesses: qualifiers
    ("permanently"), compound conditions, and future scope on authorization all go to review."""
    q = " ".join((text or "").split())
    if not q:
        return SKIP
    if _ATTEST_RE.search(q):
        return _review("attestation")
    has_auth, has_spons = bool(_AUTH_RE.search(q)), bool(_SPONSOR_RE.search(q))
    if not has_auth and not has_spons:
        return SKIP
    if _COUNTRY_RE.search(q):
        return _review("foreign_country")
    if _STATUS_RE.search(q):
        return _review("stronger_status_qualifier")
    if has_auth and has_spons:
        return _review("auth_and_sponsor_ambiguous")
    explicit_combined = bool(_NOW_OR_FUTURE_RE.search(q))
    current_marked = bool(_CURRENT_RE.search(q))
    future = bool(_FUTURE_RE.search(q))
    needs = WORK_AUTH_FACTS["needs_sponsorship_future"]
    if has_auth:
        if _NEG_RE.search(q):
            return _review("negation_ambiguous")
        if future:
            return _review("future_authorization")  # OPT expires: authorization later is not a known fact
        if _AUTH_US_RE.search(q):
            action = ("answer", "work_auth", _yn(WORK_AUTH_FACTS["authorized_us_via_opt"]), "")
        else:
            return _review("auth_country_unclear")
    elif _NOT_REQUIRE_RE.search(q):
        return _review("negated_sponsorship")
    elif _WITHOUT_RE.search(q):
        m = _WITHOUT_RE.search(q)
        rest = q[:m.start()] + " " + q[m.end():]
        if _NEG_RE.search(rest) or _REQUIRE_RE.search(rest) or not _WORK_RE.search(q):
            return _review("negation_ambiguous")
        if current_marked and not explicit_combined:
            # Present OPT eligibility differs from the future H-1B need, so "No" would be untrue.
            return _review("current_scope_ambiguous")
        # Operator-approved: authorized via OPT but needs H-1B sponsorship later,
        # so a plain "work without sponsorship" ask is truthfully No.
        action = ("answer", "sponsorship", "No", "")
    elif _NEG_RE.search(q):
        return _review("negation_ambiguous")
    elif _REQUIRE_RE.search(q):
        if current_marked and not explicit_combined:
            # A bare "do you currently require sponsorship" (or "will you currently need it") is not
            # truthfully Yes for someone on OPT who needs none right now; "now or in the future" is
            # explicitly combined and still answers Yes.
            return _review("current_scope_ambiguous")
        action = ("answer", "sponsorship", _yn(needs), "")
    else:
        return _review("sponsorship_unrecognized")
    if _extra_conditions(q):
        return _review("extra_conditions")
    if kind == "checkbox" and action[2] != "Yes":
        return _review("checkbox_no_answer")  # an unchecked box is not an answer
    return action


_OPT_PREFIX_RE = re.compile(r"^\s*(yes|no)\s*(?:[,;:.\-–—(]\s*|$)(.*)$", re.I | re.S)
_US = r"(?:the\s+)?(?:us|u\.s\.|u\.s\.a\.|usa|united\s+states(?:\s+of\s+america)?)"
# The only appended assertions an option may carry: restatements of the known facts, per question.
# There is deliberately no ("sponsorship", "no") entry: "No, I do not require sponsorship" would assert
# that no sponsorship is needed, contradicting the future H-1B need, so such options go to review.
_KNOWN_TAILS = {
    ("work_auth", "yes"): re.compile(
        rf"^(?:(?:i|we)\s+)?(?:am\s+)?(?:legally\s+)?authori[sz]ed\s+to\s+work\s+in\s+{_US}$", re.I),
    ("sponsorship", "yes"): re.compile(
        r"^(?:(?:i|we)\s+)?(?:will\s+)?requires?\s+(?:visa\s+)?sponsorship"
        r"(?:\s+(?:now\s+or\s+)?in\s+the\s+future)?$", re.I),
}


def _tail_ok(tail, answer, key):
    tail = re.sub(r"[\s.)]+$", "", re.sub(r"^[\s(]+", "", tail or ""))
    if not tail:
        return True
    pat = _KNOWN_TAILS.get((key, answer.lower())) if key else None
    return bool(pat and pat.match(tail))


def pick_option_ex(options, answer, key=None):
    """(option, status). status: 'ok' | 'absent' | 'ambiguous' | 'assertion'. A bare Yes/No (any case)
    wins; a prefix-matched 'Yes, ...' / 'No - ...' option is checked against its FULL text: an
    appended assertion that is not a known fact for this question (`key`) is 'assertion' (-> review)."""
    want = answer.lower()
    exact = [o for o in options if o.strip().lower() == want]
    if exact:
        return (exact[0], "ok") if len(exact) == 1 else (None, "ambiguous")
    near = []
    for o in options:
        m = _OPT_PREFIX_RE.match(o)
        if m and m.group(1).lower() == want:
            near.append((o, m.group(2)))
    if not near:
        return None, "absent"
    if len(near) > 1:
        return None, "ambiguous"
    o, tail = near[0]
    return (o, "ok") if _tail_ok(tail, answer, key) else (None, "assertion")


def pick_option(options, answer, key=None):
    return pick_option_ex(options, answer, key)[0]
