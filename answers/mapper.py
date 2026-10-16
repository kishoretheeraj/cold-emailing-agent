"""Question -> answer-bank mapping driven by config/answer_map.json.

Rule schema: {id, class, match:{any:[regex], none:[regex]}, field_kinds:[...], bank_path (dotted,
optional), transform (none|phone_e164|split_first|split_last|yes_no), on_missing (blank|review),
if_required_and_missing (always "review"), allow_llm (always false in v1), notes}. Rules are
checked in file order and the first match wins. The map path comes from ANSWERS_MAP_PATH, else
<repo>/config/answer_map.json. Rules are validated at load; identity.dob can never be mapped.

The JSON map is static, so one rule is extended in code at load: location.us_relocation gets the full
answers.workauth country pattern AND the wider _RELOC_COUNTRY_RE appended to its "none" list. Its static
none-list names only a few countries and cities; the shared lists mean any named non-US country falls
through to review. A relocation question that names a destination which is not clearly a US place is
also kept off this rule (relocation_destination_unclear), so it is never answered from us_relocation.answer.
"""
import json
import os
import re
from collections.abc import Mapping

from .workauth import _COUNTRIES, _COUNTRY_RE

DEFAULT_MAP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "answer_map.json")

CLASSES = ("work_auth", "identity", "free_text", "unknown")
TRANSFORMS = ("none", "phone_e164", "split_first", "split_last", "yes_no")
ON_MISSING = ("blank", "review")
_NEVER_MAPPED = ("identity.dob",)
_TRUTHY = frozenset(("yes", "y", "true", "1"))
_FALSY = frozenset(("no", "n", "false", "0"))
_COUNTRY_GUARDED_RULES = ("location.us_relocation",)

# Countries and territories a relocation question may name. Wider than the work-auth list on purpose:
# a miss here answers "willing to relocate" with the US answer for a place the operator never meant.
_RELOC_EXTRA = (
    "cuba", "jamaica", "haiti", "dominican republic", "puerto rico", "guam", "bahamas", "bermuda",
    "iceland", "luxembourg", "monaco", "qatar", "kuwait", "saudi arabia", "oman", "bahrain", "jordan",
    "lebanon", "cyprus", "malta", "estonia", "latvia", "lithuania", "slovakia", "slovenia", "croatia",
    "serbia", "bulgaria", "albania", "morocco", "tunisia", "algeria", "ghana", "senegal", "ethiopia",
    "tanzania", "uganda", "rwanda", "peru", "ecuador", "bolivia", "paraguay", "uruguay", "venezuela",
    "guatemala", "honduras", "nicaragua", "costa rica", "panama", "el salvador",
)
_RELOC_COUNTRIES = frozenset(_COUNTRIES) | frozenset(_RELOC_EXTRA)
_RELOC_COUNTRY_RE = re.compile(
    r"(?<![\w.])(?:" + "|".join(re.escape(c) for c in sorted(_RELOC_COUNTRIES, key=len, reverse=True))
    + r")(?![\w])", re.I)
_RELOC_ASK_RE = re.compile(r"\byou\b.*\brelocat\w*|\brelocat\w*.*\byou\b", re.I)
# "relocate to|in|within|across|throughout <place>" in any case, captured up to "?" or
# end-of-string; _RELOC_SPLIT_RE splits it into individual destinations, each of which must
# be clearly US for the question to be answerable.
_RELOC_DEST_RE = re.compile(
    r"\brelocat\w*\s+(?:to|in|within|across|throughout)\s+(?:the\s+)?([a-z][^?]*?)(?:\?|$)", re.I)
_RELOC_SPLIT_RE = re.compile(r"\s*(?:,|/|;|\bor\b|\band\b)\s*", re.I)
_US_DEST_RE = re.compile(
    r"(?<![\w.])(?:us|u\.s\.|usa|united states|texas|california|new york|nyc|boston|austin|seattle|denver|"
    r"chicago|hanover|new hampshire)(?![\w])", re.I)

_DENY_ENTRY = {
    "id": "deny_list", "class": "unknown", "match": {"any": [], "none": []}, "field_kinds": [],
    "bank_path": None, "transform": "none", "on_missing": "review", "if_required_and_missing": "review",
    "allow_llm": False, "notes": "topic listed in the bank's unknown_do_not_invent",
}
_cache = {}


def map_path():
    return os.environ.get("ANSWERS_MAP_PATH") or DEFAULT_MAP_PATH


def _norm(text):
    return " ".join((text or "").split())


def _bad(rule_id, msg):
    raise ValueError(f"answer_map rule {rule_id!r}: {msg}")


def _compile_list(rule_id, patterns, which):
    if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
        _bad(rule_id, f"match.{which} must be a list of regex strings")
    out = []
    for p in patterns:
        try:
            out.append(re.compile(p, re.I))
        except re.error as exc:
            _bad(rule_id, f"match.{which} has an invalid regex: {exc}")
    return out


def _validate_rule(rule, seen):
    if not isinstance(rule, dict):
        raise ValueError("answer_map: every rule must be an object")
    rid = rule.get("id")
    if not isinstance(rid, str) or not rid:
        raise ValueError("answer_map: rule without an id")
    if rid in seen:
        _bad(rid, "duplicate id")
    seen.add(rid)
    if rule.get("class") not in CLASSES:
        _bad(rid, f"class must be one of {CLASSES}")
    match = rule.get("match")
    if not isinstance(match, dict):
        _bad(rid, "match must be an object")
    kinds = rule.get("field_kinds", [])
    if not isinstance(kinds, list) or not all(isinstance(k, str) for k in kinds):
        _bad(rid, "field_kinds must be a list of strings")
    path = rule.get("bank_path")
    if path is not None:
        segs = path.split(".") if isinstance(path, str) else []
        if not segs or not all(segs):
            _bad(rid, "bank_path must be a dotted path")
        if path.lower() in _NEVER_MAPPED or any(s.lower() == "dob" for s in segs):
            _bad(rid, "date of birth is never mapped")
    if rule.get("transform", "none") not in TRANSFORMS:
        _bad(rid, f"transform must be one of {TRANSFORMS}")
    if rule.get("on_missing", "blank") not in ON_MISSING:
        _bad(rid, f"on_missing must be one of {ON_MISSING}")
    if rule.get("if_required_and_missing", "review") != "review":
        _bad(rid, "if_required_and_missing must be 'review'")
    if rule.get("allow_llm", False) is not False:
        _bad(rid, "allow_llm must be false in v1")
    out = dict(rule)
    out.setdefault("field_kinds", [])
    out.setdefault("bank_path", None)
    out.setdefault("transform", "none")
    out.setdefault("on_missing", "blank")
    out.setdefault("if_required_and_missing", "review")
    out.setdefault("allow_llm", False)
    out.setdefault("notes", "")
    out["_any"] = _compile_list(rid, match.get("any", []), "any")
    out["_none"] = _compile_list(rid, match.get("none", []), "none")
    if rid in _COUNTRY_GUARDED_RULES:
        out["_none"] = [*out["_none"], _COUNTRY_RE, _RELOC_COUNTRY_RE]
    return out


def load_rules(path=None):
    """Load and validate the rules (cached by path + mtime). Raises ValueError on a bad map."""
    p = os.fspath(path) if path else map_path()
    st = os.stat(p)
    key = (p, st.st_mtime_ns, st.st_size)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    with open(p, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("rules"), list):
        raise ValueError("answer_map: expected {'version': 1, 'rules': [...]}")
    seen = set()
    rules = [_validate_rule(r, seen) for r in data["rules"]]
    _cache[key] = rules
    return rules


def _public(rule):
    return {k: v for k, v in rule.items() if not k.startswith("_")}


def relocation_destination_unclear(question_text):
    """True for a relocation question addressed to 'you' that names a non-US country or any destination
    that is not clearly a US place (every listed destination must be US). A question naming no
    destination at all is not unclear."""
    q = _norm(question_text)
    if not _RELOC_ASK_RE.search(q):
        return False
    if _RELOC_COUNTRY_RE.search(q):
        return True
    dests = []
    for m in _RELOC_DEST_RE.finditer(q):
        dests.extend(d for d in _RELOC_SPLIT_RE.split(m.group(1)) if d.strip())
    if not dests:
        return False
    return any(not _US_DEST_RE.search(d) for d in dests)


def _matches(rule, q, field_kind):
    kinds = rule.get("field_kinds") or ()
    if kinds and field_kind is not None and field_kind not in kinds:
        return False
    if not any(p.search(q) for p in rule["_any"]):
        return False
    if rule.get("id") in _COUNTRY_GUARDED_RULES and relocation_destination_unclear(q):
        return False
    return not any(p.search(q) for p in rule["_none"])


def get_path(bank, dotted):
    """Dotted lookup into nested mappings; None when any segment is absent."""
    node = bank
    for seg in dotted.split("."):
        if not isinstance(node, Mapping) or seg not in node:
            return None
        node = node[seg]
    return node


def _present(v):
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, tuple, dict)):
        return len(v) > 0
    return True


def _yes_no(value):
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return "Yes" if value else "No"
    s = str(value).strip().lower()
    if s in _TRUTHY:
        return "Yes"
    if s in _FALSY:
        return "No"
    return None


def apply_transform(value, name):
    """Apply one named transform; None means the value cannot be produced (treated as missing)."""
    if value is None:
        return None
    if name == "none":
        return value
    if name == "yes_no":
        return _yes_no(value)
    text = str(value).strip()
    if name == "phone_e164":
        digits = re.sub(r"\D", "", text)
        if not digits:
            return None
        if text.startswith("+"):
            return "+" + digits
        if len(digits) == 10:
            return "+1" + digits
        return "+" + digits
    if name == "split_first":
        parts = text.split()
        return parts[0] if parts else None
    if name == "split_last":
        parts = text.split()
        return parts[-1] if len(parts) > 1 else None
    raise ValueError(f"unknown transform {name!r}")


def _deny_regex(bank):
    topics = bank.get("unknown_do_not_invent") if isinstance(bank, Mapping) else None
    if not isinstance(topics, (list, tuple)):
        return None
    parts = [re.escape(t.strip()) for t in topics if isinstance(t, str) and t.strip()]
    if not parts:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)", re.I)


def _materialize(rule, bank):
    out = _public(rule)
    value = None
    path = rule.get("bank_path")
    if path:
        raw = get_path(bank, path)
        if _present(raw):
            value = apply_transform(raw, rule["transform"])
        if isinstance(value, str) and not value.strip():
            value = None
    out["value"] = value
    out["missing"] = value is None
    return out


def resolve(question_text, field_kind, bank, rules=None):
    """First matching rule (file order) plus "value" and "missing", or None. A topic in the bank's
    unknown_do_not_invent list short-circuits to a forced-review {"id": "deny_list"} entry."""
    q = _norm(question_text)
    bank = bank if isinstance(bank, Mapping) else {}
    deny = _deny_regex(bank)
    if deny is not None and deny.search(q):
        return {**_DENY_ENTRY, "value": None, "missing": True}
    for rule in (rules if rules is not None else load_rules()):
        if _matches(rule, q, field_kind):
            return _materialize(rule, bank)
    return None


def matching_rules(question_text, field_kind, rules=None):
    """Every rule matching the question (public fields only), in file order."""
    q = _norm(question_text)
    return [_public(r) for r in (rules if rules is not None else load_rules()) if _matches(r, q, field_kind)]
