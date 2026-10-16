"""Answer tailoring package: decide what (if anything) goes into one application-form field.

Pieces, each usable on its own:
  workauth   work-authorization / sponsorship classifier (moved verbatim from apply_pipeline)
  classify   coarse question class: work_auth, identity, free_text, unknown, skip
  ambiguity  signals that a question must go to human review instead of being inferred
  mapper     config/answer_map.json rules -> answer-bank lookups with transforms
  audit      per-job (private) and global (hash-only) answer logs

resolve_field() ties them together and returns a FieldDecision. It never guesses: anything it
cannot answer from the bank or the fixed work-authorization facts becomes "blank" (optional
field) or "review" (required field, or an ambiguous / denied topic).
"""
import re
from dataclasses import dataclass, field

from . import ambiguity, mapper
from . import classify as _classify_mod
from .audit import log_answer
from .classify import QuestionClass
from .workauth import _COUNTRY_RE, classify_question

__all__ = ["classify_question", "resolve_field", "log_answer", "QuestionClass", "FieldDecision"]

# Rule ids used for work-auth answers (match the ids in config/answer_map.json).
_WORK_AUTH_RULE_IDS = {"work_auth": "work_auth.authorization", "sponsorship": "work_auth.sponsorship"}

# Topics that are never answered from data: required -> review, optional -> blank. Reason code is
# "deny_<name>". Checked after work-auth routing and before the mapper.
_DENY_PATTERNS = (
    ("dob", re.compile(r"date\s+of\s+birth|\bdob\b|\bbirth\s*date\b|\bbirthday\b|\bborn\s+on\b", re.I)),
    ("ssn", re.compile(r"social\s+security|\bssn\b|national\s+(?:id|insurance)|\btax\s+(?:id|identification)\b", re.I)),
    ("salary", re.compile(
        r"\bsalary\b|\bcompensation\b|pay\s+(?:expectation|range|requirement)s?|"
        r"\b(?:desired|expected|target)\s+(?:pay|base|rate|comp)\b|\bhourly\s+rate\b", re.I)),
    ("clearance", re.compile(r"\bclearance\b|\bts/?sci\b|\bpolygraph\b", re.I)),
    ("criminal", re.compile(r"\bcriminal\b|\bconvict\w*|\bfelon\w*|\bmisdemeanor\b|\barrest\w*", re.I)),
    ("visa_status", re.compile(
        r"\bvisa\b|immigration|\bwork\s+permit\b|\bh-?1b\b|\bstem\s+opt\b|\bopt\s+(?:status|extension|student)\b|"
        r"\bcpt\b|\bgreen\s*card\b|\bcitizen\w*|\bnationality\b", re.I)),
)
_RELOCATE_RE = re.compile(r"relocat", re.I)

# EEO / demographic questions: answered only with the form's own explicit decline option.
_EEO_RE = re.compile(
    r"\b(?:gender|sex|race|racial|ethnic\w*|hispanic|latinx?|veteran|disabilit\w*|sexual\s+orientation|"
    r"transgender|protected\s+class|eeo|demographic|self[- ]identif\w*)\b", re.I)
_DECLINE_RE = re.compile(
    r"\bdecline\b|prefer\s+not\s+to\s+(?:answer|say|disclose|state|identify|specify)|"
    r"(?:i\s+)?(?:do\s+not|don['’]t)\s+wish\s+to\s+(?:answer|disclose|identify|provide|self)|"
    r"choose\s+not\s+to\s+(?:disclose|answer|identify)", re.I)


@dataclass
class FieldDecision:
    action: str                     # answer | blank | review | skip
    value: object = None
    rule_id: str = None
    bank_path: str = None
    reason_codes: list = field(default_factory=list)
    qclass: str = QuestionClass.UNKNOWN
    source: str = "blank"           # bank | facts | declined | blank | review (audit vocabulary)


def _deny_topic(q):
    for name, pat in _DENY_PATTERNS:
        if pat.search(q):
            return name
    if _RELOCATE_RE.search(q) and _COUNTRY_RE.search(q):
        return "non_us_relocation"
    return None


def _unmapped(required, reasons, qclass, rule_id=None, force_review=False):
    action = "review" if (required or force_review) else "blank"
    return FieldDecision(action, None, rule_id, None, list(reasons), qclass, action)


def _decide(q, field_kind, bank, required, options, map_path):
    qclass = _classify_mod.classify(q)
    if qclass == QuestionClass.SKIP:
        return FieldDecision("skip", qclass=qclass)

    if qclass == QuestionClass.WORK_AUTH:
        act, key, ans, why = classify_question(q, "checkbox" if field_kind == "checkbox" else "select")
        if act == "answer":
            return FieldDecision("answer", ans, _WORK_AUTH_RULE_IDS.get(key, f"work_auth.{key}"),
                                 None, [], qclass, "facts")
        if act == "skip":
            return FieldDecision("skip", qclass=qclass)
        return FieldDecision("review", None, None, None, [why], qclass, "review")

    if qclass == QuestionClass.UNKNOWN:
        ambiguous, reasons = ambiguity.detect(q)
        real = [r for r in reasons if r != "truncated"]
        if ambiguous and real:
            return FieldDecision("review", None, None, None, real, qclass, "review")

    # A relocation ask naming a non-US country, or any destination that is not clearly a US place, is a
    # human decision: never answered from us_relocation.answer, optional or not.
    if mapper.relocation_destination_unclear(q):
        return FieldDecision("review", None, "location.us_relocation", None,
                             ["relocation_destination_unclear"], qclass, "review")

    topic = _deny_topic(q)
    if topic:
        return _unmapped(required, [f"deny_{topic}"], qclass)

    if _EEO_RE.search(q):
        declines = [o for o in (options or ()) if isinstance(o, str) and _DECLINE_RE.search(o)]
        if len(declines) == 1:
            return FieldDecision("answer", declines[0], "eeo.decline", None, ["eeo_decline"], qclass, "declined")
        return FieldDecision("blank", None, "eeo.decline", None, ["eeo_no_decline_option"], qclass, "blank")

    if field_kind == "checkbox":
        return _unmapped(required, ["checkbox_never_ticked"], qclass)

    rules = mapper.load_rules(map_path)
    rule = mapper.resolve(q, field_kind, bank, rules=rules)
    if rule is None:
        if required:
            return FieldDecision("review", None, None, None, ["required_unmapped"], qclass, "review")
        return FieldDecision("blank", None, None, None, ["unknown_blank"], qclass, "blank")
    if rule["id"] == "deny_list":
        return FieldDecision("review", None, "deny_list", None, ["deny_list"], qclass, "review")

    cands = mapper.matching_rules(q, field_kind, rules=rules)
    unknown = [c for c in cands if c["class"] == "unknown"]
    if unknown:
        return FieldDecision("review", None, unknown[0]["id"], None, ["deny_topic"], qclass, "review")
    if len(cands) > 1:
        _, reasons = ambiguity.detect(q, [(c["id"], c.get("bank_path")) for c in cands])
        if "rule_conflict" in reasons:
            return FieldDecision("review", None, rule["id"], None, ["rule_conflict"], qclass, "review")

    if rule["missing"]:
        action = "review" if (required or rule["on_missing"] == "review") else "blank"
        return FieldDecision(action, None, rule["id"], rule.get("bank_path"), ["bank_value_missing"], qclass, action)
    return FieldDecision("answer", rule["value"], rule["id"], rule.get("bank_path"), [], qclass, "bank")


def resolve_field(question_text, field_kind, bank, required, artifacts_dir=None, options=None,
                  job_id=None, attempt_id=None, field_ref=None, audit_path=None, emit=None,
                  map_path=None):
    """Decide one field. Returns a FieldDecision; logs it (audit.log_answer) when artifacts_dir is
    given. `options` is the list of option labels for select/radio fields (used for EEO declines).
    Audit write failures raise; skip decisions are not logged."""
    q = " ".join((question_text or "").split())
    decision = _decide(q, field_kind, bank, bool(required), options, map_path)
    if artifacts_dir is not None and decision.action != "skip":
        if job_id is None:
            raise ValueError("job_id is required when artifacts_dir is given")
        log_answer(job_id, attempt_id, field_ref, q, decision.qclass, decision.rule_id, decision.bank_path,
                   decision.source, decision.action, decision.reason_codes, decision.value, artifacts_dir,
                   audit_path=audit_path, emit=emit)
    return decision
