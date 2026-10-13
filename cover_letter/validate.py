"""Grounding validation for letter.v1 content JSON.

validate_letter returns (is_valid, violations). Each violation is "code: short detail"; the code prefix is
what build_letter records as a review reason. Strictness is deliberate: a false positive costs a repair
pass or a review-queue entry, a false negative ships an invented claim.

Two layers tie the letter to its evidence. Numbers, names, titles and similar surface facts are checked
term by term. Claims are checked by meaning: every body sentence must share at least half of its content
words with one of the evidence items its paragraph cites (claim_ungrounded), so a fluent sentence that
merely avoids numbers and capitalized names cannot invent an accomplishment.

Known limits of the heuristics: tools and skills written in lowercase are only caught when they appear in
requirements["keywords"]; proper-noun detection relies on capitalization; claim grounding is lexical
overlap, not entailment.
"""

import re

SCHEMA_VERSION = "letter.v1"
MAX_HOOK = 350
MAX_PARAGRAPH = 700
MAX_CLOSING = 300
MIN_PARAGRAPHS = 2
MAX_PARAGRAPHS = 3

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?[%xXkKmM]?")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_CAP_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Za-z0-9&+#]*")
_WORD_RE = re.compile(r"[a-z0-9&+#]+")
_ALNUM_RE = re.compile(r"[a-z0-9]+")
_URL_RE = re.compile(r"https?://[^\s)>\]]+|www\.[^\s)>\]]+", re.I)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_AUTH_RE = re.compile(
    r"\b(salary|compensation|pay expectation|start date|available to start|visa|h-?1b|opt\b|sponsorship|"
    r"work authorization|authori[sz]ed to work|work permit|immigration|green card|citizenship|"
    r"employment authorization)\b", re.I)
_SLOP_RE = re.compile(
    r"\b(delve\w*|tapestry|landscape|furthermore|moreover|additionally|in today['\u2019]s fast-paced)\b", re.I)
_YEARS_RE = re.compile(
    r"\b((?:\d+\+?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty)"
    r"[\s-]*years?)\b", re.I)
_DEGREE_RE = re.compile(
    r"\b(bachelor(?:'s)?|master(?:'s| of)|masters|mba|ph\.?d|doctorate|associate degree|degree)\b", re.I)
_TITLE_RE = re.compile(
    r"\b(senior|principal|director|vice president|vp|chief|head of|cto|ceo|cfo|coo|founder|president)\b", re.I)
_MONTH_RE = re.compile(
    r"\b(January|February|March|April|June|July|August|September|October|November|December)\b")
_FIRST_PERSON_RE = re.compile(r"\b(I|I'm|I've|I'd|my|me)\b")
_STOP = frozenset("your with that this from they have will would about which their there been were what "
                  "when also into over more most each team role work".split())
# Claim grounding: a body sentence must share this fraction of its content words with one cited item.
_GROUND_MIN = 0.5
_GROUND_STOP = frozenset("the and for with from that this into over using used via across their its our was were "
                         "has have had are been being than then also per each all which who when while what "
                         "they them can will would could should about your not but any some such very just "
                         "more most like".split())


# ── Helpers ────────────────────────────────────────────────────────────────────

def _v(code, detail=""):
    detail = " ".join(str(detail).split())[:60]
    return f"{code}: {detail}" if detail else code


def _numbers(text):
    return {m.group(0).replace(",", "").lower() for m in _NUM_RE.finditer(str(text))}


def _words(text):
    return set(_WORD_RE.findall(str(text).lower()))


def _norm(text):
    return re.sub(r"[\s\-]+", " ", str(text).lower())


def _tokens(text, drop=frozenset()):
    return {t[:5] for t in _WORD_RE.findall(str(text).lower()) if len(t) > 3 and t not in _STOP} - drop


def _claim_words(text):
    return {t[:5] for t in _ALNUM_RE.findall(str(text).lower())
            if (len(t) > 2 or t.isdigit()) and t not in _GROUND_STOP}


def _url_key(url):
    return re.sub(r"^(?:https?://)?(?:www\.)?", "", str(url).strip().lower()).rstrip("/.,;")


def _schema_violations(content):
    out = []
    for key in ("schema_version", "job_id", "company", "role", "opening_hook", "body", "closing",
                "ungrounded_claims"):
        if key not in content:
            out.append(_v("schema", f"missing key {key}"))
    if out:
        return out
    if content["schema_version"] != SCHEMA_VERSION:
        out.append(_v("schema", "schema_version must be letter.v1"))
    for key in ("job_id", "company", "role"):
        if not isinstance(content[key], str):
            out.append(_v("schema", f"{key} must be a string"))
    for key, limit in (("opening_hook", MAX_HOOK), ("closing", MAX_CLOSING)):
        value = content[key]
        if not isinstance(value, str) or not value.strip():
            out.append(_v("schema", f"{key} must be a non-empty string"))
        elif len(value) > limit:
            out.append(_v("schema", f"{key} longer than {limit} chars"))
    body = content["body"]
    if not isinstance(body, list) or not MIN_PARAGRAPHS <= len(body) <= MAX_PARAGRAPHS:
        out.append(_v("schema", f"body must have {MIN_PARAGRAPHS}-{MAX_PARAGRAPHS} paragraphs"))
    else:
        for i, para in enumerate(body):
            if not isinstance(para, dict):
                out.append(_v("schema", f"body[{i}] must be an object"))
                continue
            text = para.get("text")
            if not isinstance(text, str) or not text.strip():
                out.append(_v("schema", f"body[{i}] text must be a non-empty string"))
            elif len(text) > MAX_PARAGRAPH:
                out.append(_v("schema", f"body[{i}] text longer than {MAX_PARAGRAPH} chars"))
            ids = para.get("evidence_ids")
            if not isinstance(ids, list) or not ids or not all(isinstance(e, str) and e.strip() for e in ids):
                out.append(_v("schema", f"body[{i}] needs a non-empty evidence_ids list of strings"))
    claims = content["ungrounded_claims"]
    if not isinstance(claims, list) or not all(isinstance(c, str) for c in claims):
        out.append(_v("schema", "ungrounded_claims must be a list of strings"))
    return out


# ── Validation ─────────────────────────────────────────────────────────────────

def validate_letter(content, evidence):
    """Check a letter.v1 dict against the evidence package. Returns (is_valid, violations); never raises."""
    try:
        return _validate(content, evidence)
    except Exception as exc:
        return False, [_v("validator_error", type(exc).__name__)]


def _validate(content, evidence):
    if not isinstance(content, dict):
        return False, [_v("schema", "content is not a JSON object")]
    violations = _schema_violations(content)
    if violations:
        return False, violations

    ev = evidence if isinstance(evidence, dict) else {}
    bank = ev.get("bank_facts") if isinstance(ev.get("bank_facts"), dict) else {}
    req = ev.get("requirements") if isinstance(ev.get("requirements"), dict) else {}
    bullets = {str(b["id"]): b for b in ev.get("bullets") or [] if isinstance(b, dict) and "id" in b}
    jd = str(ev.get("jd_text") or "")
    company = str(req.get("company") or content["company"] or "")

    def id_text(eid):
        if eid in bullets:
            return str(bullets[eid].get("text") or "")
        if eid.startswith("bank:") and eid[5:] in bank:
            return str(bank[eid[5:]] or "")
        return None

    body = content["body"]
    for para in body:
        for eid in para["evidence_ids"]:
            if id_text(eid) is None:
                violations.append(_v("evidence_id_unresolved", eid))

    hook, closing = content["opening_hook"], content["closing"]
    fields = [hook, *[p["text"] for p in body], closing]
    text = "\n".join(fields)

    # Numbers: each paragraph against its own cited evidence; hook and closing against all cited evidence.
    def allowed_numbers(ids):
        nums = set()
        for eid in ids:
            nums |= _numbers(id_text(eid) or "")
        return nums

    cited_all = [eid for p in body for eid in p["evidence_ids"]]
    checks = [(hook, cited_all)] + [(p["text"], p["evidence_ids"]) for p in body] + [(closing, cited_all)]
    for field_text, ids in checks:
        for n in sorted(_numbers(field_text) - allowed_numbers(ids)):
            violations.append(_v("number_ungrounded", n))

    # Claims: each body sentence must mostly restate one cited item. Paragraphs citing only bank facts are
    # exempt (bank facts are short strings) but stay under the number rule above.
    for para in body:
        ids = para["evidence_ids"]
        if all(eid.startswith("bank:") for eid in ids):
            continue
        cited = [_claim_words(t) for t in (id_text(eid) for eid in ids) if t]
        if not cited:
            continue  # unresolved ids are already reported
        for sent in _SENT_SPLIT.split(para["text"]):
            words = _claim_words(sent)
            if words and not any(len(words & c) / len(words) >= _GROUND_MIN for c in cited):
                violations.append(_v("claim_ungrounded", sent))

    sources = "\n".join([f"{b.get('text', '')} {b.get('role', '')}" for b in bullets.values()]
                        + [str(v) for v in bank.values()])
    sources_lower = sources.lower()
    sources_norm = _norm(sources)

    # Names, tools and titles: any capitalized mid-sentence term must come from the sources, the JD or the target.
    allowed = _words(sources) | _words(jd) | _words(company) | _words(req.get("role_title") or "") \
        | _words(content["role"]) | {"i"}
    for field_text in fields:
        for sent in _SENT_SPLIT.split(field_text):
            for m in _CAP_RE.finditer(sent):
                if re.search(r"[A-Za-z0-9]", sent[:m.start()]) and m.group(0).lower() not in allowed:
                    violations.append(_v("unknown_proper_noun", m.group(0)))

    for m in _DEGREE_RE.finditer(text):
        if m.group(1).lower() not in sources_lower:
            violations.append(_v("degree_not_in_sources", m.group(1)))
    role_words = _words(content["role"]) | _words(req.get("role_title") or "")
    for m in _TITLE_RE.finditer(text):
        word = m.group(1).lower()
        if word not in sources_lower and not (set(word.split()) & role_words):
            violations.append(_v("title_not_in_sources", word))
    for m in _MONTH_RE.finditer(text):
        if m.group(1).lower() not in sources_lower:
            violations.append(_v("date_not_in_sources", m.group(1)))
    for m in _YEARS_RE.finditer(text):
        if _norm(m.group(1)) not in sources_norm:
            violations.append(_v("years_not_in_sources", m.group(1)))

    # A first-person sentence naming a JD keyword the sources never mention is an unsupported skill claim.
    for kw in req.get("keywords") or []:
        kw = str(kw).strip()
        if len(kw) < 2 or re.search(r"\b" + re.escape(kw.lower()) + r"\b", sources_lower):
            continue
        pattern = re.compile(r"\b" + re.escape(kw) + r"\b", re.I)
        if any(pattern.search(s) and _FIRST_PERSON_RE.search(s)
               for f in fields for s in _SENT_SPLIT.split(f)):
            violations.append(_v("skill_not_in_sources", kw))

    for topic in ev.get("unknown_topics") or []:
        if str(topic).strip() and str(topic).lower() in text.lower():
            violations.append(_v("unknown_topic", topic))

    # Statements about the target company must borrow real wording from the job description.
    if company.strip():
        names = [re.escape(company.strip())]
        first = company.split()[0] if company.split() else ""
        if len(first) >= 4:
            names.append(re.escape(first))
        name_re = re.compile(r"\b(?:" + "|".join(names) + r")\b", re.I)
        drop = {t[:5] for t in _WORD_RE.findall(company.lower())}
        jd_tokens = _tokens(jd, drop)
        for field_text in fields:
            for sent in _SENT_SPLIT.split(field_text):
                if name_re.search(sent) and len(_tokens(sent, drop) & jd_tokens) < 3:
                    violations.append(_v("company_claim_ungrounded", sent))

    linkedin = _url_key(bank.get("linkedin") or "")
    for m in _URL_RE.finditer(text):
        if not linkedin or _url_key(m.group(0)) != linkedin:
            violations.append(_v("url_not_allowed", m.group(0)))
    email = str(bank.get("email") or "").lower()
    for m in _EMAIL_RE.finditer(text):
        if m.group(0).lower() != email:
            violations.append(_v("email_not_in_identity", m.group(0)))

    for word in ev.get("banned_words") or []:
        word = str(word).strip().lower()
        if word and re.search(r"\b" + re.escape(word) + r"\w*", text, re.I):
            violations.append(_v("banned_word", word))
    for m in _SLOP_RE.finditer(text):
        violations.append(_v("slop_phrase", m.group(0)))
    if "\u2014" in text or "\u2013" in text:
        violations.append(_v("dash_found", "em or en dash"))
    for m in _AUTH_RE.finditer(text):
        violations.append(_v("auth_or_pay_wording", m.group(0)))

    if content["ungrounded_claims"]:
        violations.append(_v("ungrounded_claims_nonempty", content["ungrounded_claims"][0]))

    deduped = list(dict.fromkeys(violations))
    return not deduped, deduped
