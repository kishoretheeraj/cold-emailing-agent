"""Ambiguity signals: reasons a question must go to human review instead of being inferred.

detect() returns (is_ambiguous, reasons). Tracked topics are work authorization, sponsorship,
relocation, salary and start date. The negation / status-qualifier / scope / foreign-country /
multi-topic signals only apply to questions touching a tracked topic; "truncated" and
"rule_conflict" apply everywhere. A work-auth question that workauth.classify_question would
answer outright (e.g. the plain "able to work in the US without sponsorship" form) is
whitelisted from the tracked-topic signals, so nothing the operator-approved rules answer is
turned into a review here.
"""
import re

from . import workauth

_TOPICS = (
    ("work_auth", workauth._AUTH_RE),
    ("sponsorship", workauth._SPONSOR_RE),
    ("relocation", re.compile(r"\brelocat\w*", re.I)),
    ("salary", re.compile(
        r"\bsalary\b|\bcompensation\b|pay\s+(?:expectation|range|requirement)s?|"
        r"\b(?:desired|expected|target)\s+(?:pay|base|rate)\b|\bhourly\s+rate\b", re.I)),
    ("start_date", re.compile(
        r"start\s+date|available\s+to\s+start|earliest\s+(?:start|date)|when\s+can\s+you\s+start|"
        r"notice\s+period|availab\w*\s+date", re.I)),
)
_TOKEN = re.compile(r"[\w'’.\-]+")
MIN_TOKENS = 3
# "now or in the future" asks about both scopes at once (and is answered by the work-auth rules);
# it is removed before the current-vs-future mismatch test.
_NOW_OR_FUTURE_RE = re.compile(
    r"\b(?:now|currently|presently|today)\s*,?\s*(?:or|and/or)\s+(?:at\s+any\s+(?:time|point)\s+)?"
    r"(?:in\s+the\s+)?(?:future|later)\b", re.I)


def _topics(q):
    return [name for name, pat in _TOPICS if pat.search(q)]


def _workauth_answers(q):
    try:
        return workauth.classify_question(q)[0] == "answer"
    except Exception:
        return False


def _rule_conflict(candidates):
    """candidates: (rule_id, bank_path) pairs, or bare rule ids resolved through the answer map."""
    paths, ids = set(), []
    for c in candidates or ():
        if isinstance(c, (tuple, list)):
            if len(c) > 1 and c[1]:
                paths.add(c[1])
        else:
            ids.append(c)
    if ids:
        from . import mapper
        by_id = {r["id"]: r.get("bank_path") for r in mapper.load_rules()}
        paths.update(by_id[i] for i in ids if by_id.get(i))
    return len(paths) > 1


def detect(text, candidate_rule_ids=()):
    """Return (is_ambiguous, reasons). reasons: multi_topic, negation, status_qualifier,
    scope_mismatch, foreign_country, rule_conflict, truncated."""
    q = " ".join((text or "").split())
    reasons = []
    if len(_TOKEN.findall(q)) < MIN_TOKENS:
        reasons.append("truncated")
    topics = _topics(q)
    if topics and not (set(topics) & {"work_auth", "sponsorship"} and _workauth_answers(q)):
        if len(topics) > 1:
            reasons.append("multi_topic")
        if workauth._NEG_RE.search(q):
            reasons.append("negation")
        if workauth._STATUS_RE.search(q):
            reasons.append("status_qualifier")
        scoped = _NOW_OR_FUTURE_RE.sub(" ", q)
        if workauth._CURRENT_RE.search(scoped) and workauth._FUTURE_RE.search(scoped):
            reasons.append("scope_mismatch")
        if workauth._COUNTRY_RE.search(q):
            reasons.append("foreign_country")
    if _rule_conflict(candidate_rule_ids):
        reasons.append("rule_conflict")
    return bool(reasons), reasons
