"""Answer tailoring: work-auth classifier (moved), question classes, ambiguity, mapper, audit."""
import copy
import hashlib
import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

from answers import FieldDecision, QuestionClass, ambiguity, classify_question, log_answer, mapper, resolve_field, workauth
from answers.classify import classify

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((Path(__file__).parent / "fixtures" / "answer_bank_schema.json").read_text())


# ── Helpers ────────────────────────────────────────────────────────────────────
def _leaves(node, prefix=""):
    for key, val in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(val, dict):
            yield from _leaves(val, path)
        else:
            yield path, val


def _schema_lookup(dotted):
    """Leaf type name for a dotted path in the keys-only fixture, or None when absent."""
    node = SCHEMA
    for seg in dotted.split("."):
        if not isinstance(node, dict) or seg not in node:
            return None
        node = node[seg]
    return node if isinstance(node, str) else None


def _build_bank():
    bank = {}
    for path, kind in _leaves(SCHEMA):
        node = bank
        *parents, leaf = path.split(".")
        for seg in parents:
            node = node.setdefault(seg, {})
        node[leaf] = [] if kind.startswith("list") else f"test-{path}"
    bank["identity"]["phone"] = "(555) 010-0100"
    bank["identity"]["email"] = "person@example.test"
    return bank


BANK = _build_bank()


def rf(q, kind="input", required=False, bank=None, **kw):
    return resolve_field(q, kind, copy.deepcopy(BANK) if bank is None else bank, required, **kw)


def A(key, ans):
    return ("answer", key, ans, "")


def R(reason):
    return ("review", None, None, reason)


SKIP_RESULT = ("skip", None, None, "unrelated")
YES_AUTH, YES_SP, NO_SP = A("work_auth", "Yes"), A("sponsorship", "Yes"), A("sponsorship", "No")


# ── workauth: phrasing table ───────────────────────────────────────────────────
WORKAUTH_CASES = [
    # authorization, answered Yes
    ("Are you legally authorized to work in the United States?", "select", YES_AUTH),
    ("Are you authorized to work in the US?", "select", YES_AUTH),
    ("Are you authorised to work in the U.S.?", "select", YES_AUTH),
    ("ARE YOU LEGALLY AUTHORIZED TO WORK IN THE UNITED STATES?", "select", YES_AUTH),
    ("  Are   you   authorized \n to work in the US ?", "select", YES_AUTH),
    ("Are you authorized to work in the United States of America?", "select", YES_AUTH),
    ("Are you authorized to work in the USA?", "select", YES_AUTH),
    ("Are you authorized to work in the U.S.A.?", "select", YES_AUTH),
    ("Are you legally authorized to work in the United States?*", "select", YES_AUTH),
    ("Work authorization: are you authorized to work in the US?", "select", YES_AUTH),
    ("Are you currently authorized to work in the US?", "select", YES_AUTH),
    ("I am legally authorized to work in the United States", "checkbox", YES_AUTH),
    # authorization, review
    ("Are you eligible to work in the United States?", "select", R("auth_country_unclear")),
    ("Do you have the legal right to work in the US?", "select", R("auth_country_unclear")),
    ("Are you legally permitted to work in the US?", "select", R("auth_country_unclear")),
    ("Do you have work authorization in the US?", "select", R("auth_country_unclear")),
    ("Please select your work authorization status", "select", R("auth_country_unclear")),
    ("Are you legally authorized to work in the United States? (Yes/No)", "select", R("negation_ambiguous")),
    ("Are you not authorized to work in the US?", "select", R("negation_ambiguous")),
    ("Are you authorized to work in the US now or in the future?", "select", R("future_authorization")),
    ("Will you be authorized to work in the US?", "select", R("future_authorization")),
    ("Are you authorized to work in the US without restrictions?", "select", R("stronger_status_qualifier")),
    ("Are you a U.S. citizen or permanent resident authorized to work in the US?", "select", R("stronger_status_qualifier")),
    ("Are you authorized to work in Canada?", "select", R("foreign_country")),
    ("Are you authorized to work in the United Kingdom?", "select", R("foreign_country")),
    ("Are you authorized to work in the US or the EU?", "select", R("foreign_country")),
    ("Are you authorized to work in India?", "select", R("foreign_country")),
    ("Are you authorized to work in Peru?", "select", R("foreign_country")),
    ("Are you authorized to work in the US and at least 18 years old?", "select", R("extra_conditions")),
    ("I certify that I am authorized to work in the United States.", "select", R("attestation")),
    ("I acknowledge that I am legally authorized to work in the US", "select", R("attestation")),
    ("I agree to the privacy policy", "select", R("attestation")),
    # authorization and sponsorship in one question
    ("Are you authorized to work in the US without sponsorship?", "select", R("auth_and_sponsor_ambiguous")),
    ("Are you authorized to work in the US and do you require sponsorship?", "select", R("auth_and_sponsor_ambiguous")),
    ("Are you authorized to work in the US? If not, will you require sponsorship?", "select", R("auth_and_sponsor_ambiguous")),
    ("Are you authorized to work in the US without needing sponsorship?", "select", R("auth_and_sponsor_ambiguous")),
    # sponsorship, answered Yes
    ("Will you now or in the future require sponsorship?", "select", YES_SP),
    ("Will you now or in the future require visa sponsorship to work in the US?", "select", YES_SP),
    ("Do you require sponsorship?", "select", YES_SP),
    ("Do you require visa sponsorship?", "select", YES_SP),
    ("Do you currently or in the future require sponsorship?", "select", YES_SP),
    ("Will you ever require sponsorship?", "select", YES_SP),
    ("Will you require sponsorship at any point in the future?", "select", YES_SP),
    ("Will you need sponsorship in the future?", "select", YES_SP),
    ("Is sponsorship required?", "select", YES_SP),
    ("Will you require visa sponsorship from our company?", "select", YES_SP),
    ("Do you need sponsorship now or in the future?", "select", YES_SP),
    ("Will you require employment sponsorship in the future?", "select", YES_SP),
    ("DO YOU REQUIRE SPONSORSHIP?", "select", YES_SP),
    ("do you require sponsorship", "select", YES_SP),
    ("Do you require sponsorship?   ", "select", YES_SP),
    ("I will require sponsorship now or in the future", "checkbox", YES_SP),
    ("Do you require sponsorship?", "checkbox", YES_SP),
    # sponsorship, review
    ("Do you currently require sponsorship?", "select", R("current_scope_ambiguous")),
    ("Do you now require sponsorship?", "select", R("current_scope_ambiguous")),
    ("Do you require sponsorship right now?", "select", R("current_scope_ambiguous")),
    ("Will you currently need sponsorship?", "select", R("current_scope_ambiguous")),
    ("Will you presently require sponsorship?", "select", R("current_scope_ambiguous")),
    ("Will you require sponsorship now?", "select", R("current_scope_ambiguous")),
    ("Do you not require sponsorship?", "select", R("negated_sponsorship")),
    ("I do not require visa sponsorship", "select", R("negated_sponsorship")),
    ("I do not require sponsorship", "checkbox", R("negated_sponsorship")),
    ("Will you not need sponsorship?", "select", R("negation_ambiguous")),
    ("I do not need sponsorship", "checkbox", R("negation_ambiguous")),
    ("Does the company sponsor visas?", "select", R("sponsorship_unrecognized")),
    ("Sponsorship", "select", R("sponsorship_unrecognized")),
    ("Have you ever been sponsored by this company?", "select", R("sponsorship_unrecognized")),
    ("Will you require sponsorship to work in Canada?", "select", R("foreign_country")),
    ("Will you require sponsorship for permanent residence?", "select", R("stronger_status_qualifier")),
    ("Will you now or in the future require sponsorship and are you willing to relocate?", "select", R("extra_conditions")),
    ("Will you require sponsorship (H-1B)?", "select", R("extra_conditions")),
    ("Do you require sponsorship for an H-1B visa?", "select", R("extra_conditions")),
    # plain "without sponsorship" (operator-approved: No)
    ("Are you able to work in the US without sponsorship?", "select", NO_SP),
    ("Can you work in the US without sponsorship?", "select", NO_SP),
    ("Can you work without sponsorship?", "select", NO_SP),
    ("Are you able to work in the United States without visa sponsorship?", "select", NO_SP),
    ("Are you able to work in the US without sponsorship now or in the future?", "select", NO_SP),
    ("Can you currently work without sponsorship?", "select", R("current_scope_ambiguous")),
    ("Can you work in the US without sponsorship now?", "select", R("current_scope_ambiguous")),
    ("Will you be able to work without sponsorship now?", "select", R("current_scope_ambiguous")),
    ("Are you able to work in the US without sponsorship and with no restrictions?", "select", R("stronger_status_qualifier")),
    ("Are you unable to work without sponsorship?", "select", R("negation_ambiguous")),
    ("Can you work in the US without sponsorship?", "checkbox", R("checkbox_no_answer")),
    # not work-auth
    ("Are you a US citizen?", "select", SKIP_RESULT),
    ("Are you willing to relocate?", "select", SKIP_RESULT),
    ("What is your name?", "select", SKIP_RESULT),
    ("Do you need a visa to work in the US?", "select", SKIP_RESULT),
    ("", "select", SKIP_RESULT),
    (None, "select", SKIP_RESULT),
    ("   ", "select", SKIP_RESULT),
]


def test_workauth_table_is_large_enough():
    assert len(WORKAUTH_CASES) >= 60


@pytest.mark.parametrize("question,kind,expected", WORKAUTH_CASES)
def test_classify_question_table(question, kind, expected):
    assert classify_question(question, kind) == expected


def test_will_does_not_defeat_current_scope_review():
    # "will" is future wording, but a current marker still scopes the question to today's status.
    assert classify_question("Will you currently need sponsorship?") == R("current_scope_ambiguous")
    assert classify_question("Will you be able to work without sponsorship now?") == R("current_scope_ambiguous")
    # Only an explicit "now or in the future" combines the two and answers Yes.
    assert classify_question("Will you now or in the future require sponsorship?") == YES_SP


def test_work_auth_facts_are_the_fixed_truth():
    assert workauth.WORK_AUTH_FACTS == {
        "authorized_us_via_opt": True, "needs_sponsorship_future": True, "needs_sponsorship_now": False}


def test_moved_code_parity_with_apply_pipeline():
    ap = pytest.importorskip("apply_pipeline")
    assert ap.classify_question is workauth.classify_question
    assert ap.pick_option_ex is workauth.pick_option_ex
    assert ap.pick_option is workauth.pick_option
    assert ap.FACTS == workauth.WORK_AUTH_FACTS
    assert ap.FACTS is not workauth.WORK_AUTH_FACTS


@pytest.mark.parametrize("options,answer,key,expected", [
    (["Yes", "No"], "Yes", "work_auth", ("Yes", "ok")),
    (["yes", "NO"], "No", "sponsorship", ("NO", "ok")),
    (["Yes, I am authorized to work in the US", "No"], "Yes", "work_auth", ("Yes, I am authorized to work in the US", "ok")),
    (["Yes, and I hold a green card", "No"], "Yes", "work_auth", (None, "assertion")),
    (["Maybe", "Unsure"], "Yes", "work_auth", (None, "absent")),
    (["Yes", "yes"], "Yes", "work_auth", (None, "ambiguous")),
    (["Yes, I will require sponsorship", "No"], "Yes", "sponsorship", ("Yes, I will require sponsorship", "ok")),
    # "No, I do not require sponsorship" contradicts the future sponsorship need: no known tail, so review.
    (["Yes", "No, I do not require sponsorship"], "No", "sponsorship", (None, "assertion")),
    (["Yes", "No - I do not now require sponsorship"], "No", "sponsorship", (None, "assertion")),
])
def test_pick_option_ex(options, answer, key, expected):
    assert workauth.pick_option_ex(options, answer, key) == expected
    assert workauth.pick_option(options, answer, key) == expected[0]


def test_no_sponsorship_no_tail_is_defined():
    assert ("sponsorship", "no") not in workauth._KNOWN_TAILS


# ── classify ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("", QuestionClass.SKIP),
    ("Submit application", QuestionClass.SKIP),
    ("Next", QuestionClass.SKIP),
    ("Save and continue", QuestionClass.SKIP),
    ("Upload resume", QuestionClass.SKIP),
    ("Personal Information", QuestionClass.SKIP),
    ("Email", QuestionClass.IDENTITY),
    ("Phone number", QuestionClass.IDENTITY),
    ("First Name", QuestionClass.IDENTITY),
    ("LinkedIn Profile URL", QuestionClass.IDENTITY),
    ("Are you willing to relocate?", QuestionClass.IDENTITY),
    ("Why do you want to work here?", QuestionClass.FREE_TEXT),
    ("What accomplishment are you most proud of?", QuestionClass.FREE_TEXT),
    ("Lorem ipsum dolor sit amet " * 12, QuestionClass.FREE_TEXT),
    ("Are you legally authorized to work in the United States?", QuestionClass.WORK_AUTH),
    ("Will you now or in the future require sponsorship?", QuestionClass.WORK_AUTH),
    ("Are you able to work in the US without sponsorship?", QuestionClass.WORK_AUTH),
    ("Do you currently require sponsorship?", QuestionClass.WORK_AUTH),
    ("Are you authorized to work in Canada?", QuestionClass.UNKNOWN),
    ("Are you a permanent resident authorized to work in the US?", QuestionClass.UNKNOWN),
    ("Will you require sponsorship and are you willing to relocate?", QuestionClass.UNKNOWN),
    ("What is your expected salary?", QuestionClass.UNKNOWN),
    ("Foo", QuestionClass.UNKNOWN),
])
def test_classify(text, expected):
    assert classify(text) == expected


# ── ambiguity ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("", (True, ["truncated"])),
    ("Email", (True, ["truncated"])),
    ("Do you require sponsorship?", (False, [])),
    ("Will you now or in the future require sponsorship?", (False, [])),
    ("Are you able to work in the US without sponsorship?", (False, [])),
    ("Do you have no preference on team size?", (False, [])),
    ("Are you authorized to work in Canada?", (True, ["foreign_country"])),
    ("Are you willing to relocate to Canada?", (True, ["foreign_country"])),
    ("Are you a permanent resident willing to relocate?", (True, ["status_qualifier"])),
    ("Will you require sponsorship and are you willing to relocate?", (True, ["multi_topic"])),
    ("Do you currently require sponsorship, will you need it later?", (True, ["scope_mismatch"])),
])
def test_detect(text, expected):
    assert ambiguity.detect(text) == expected


def test_detect_multi_topic_with_negation():
    ok, reasons = ambiguity.detect("Are you authorized to work in the US without sponsorship?")
    assert ok and {"multi_topic", "negation"} <= set(reasons)


def test_now_or_in_the_future_is_not_a_scope_mismatch():
    ok, reasons = ambiguity.detect("Will you now or in the future require sponsorship for a visa extension?")
    assert "scope_mismatch" not in reasons and not ok


def test_rule_conflict_signal():
    q = "Your email address"
    assert ambiguity.detect(q, [("a", "identity.email"), ("b", "identity.linkedin")]) == (True, ["rule_conflict"])
    assert ambiguity.detect(q, [("a", "identity.email"), ("b", "identity.email")]) == (False, [])
    assert ambiguity.detect(q, ["identity.email", "identity.phone"]) == (True, ["rule_conflict"])


# ── mapper ─────────────────────────────────────────────────────────────────────
def test_every_bank_path_exists_in_schema():
    missing = [(r["id"], r["bank_path"]) for r in mapper.load_rules()
               if r.get("bank_path") and _schema_lookup(r["bank_path"]) is None]
    assert not missing, f"bank_path not in the answer-bank schema: {missing}"


def test_schema_lookup_detects_an_absent_path():
    assert _schema_lookup("identity.email") == "str"
    assert _schema_lookup("identity.nope") is None
    assert _schema_lookup("identity") is None


def test_no_rule_maps_dob():
    for rule in mapper.load_rules():
        path = rule.get("bank_path") or ""
        assert "dob" not in path.lower().split(".")


def test_dob_value_is_never_returned():
    for q in ("Date of birth", "DOB", "Birthday", "What is your date of birth?"):
        for required in (True, False):
            d = rf(q, required=required)
            assert d.value is None and d.action in ("review", "blank")
            assert d.reason_codes == ["deny_dob"]
    assert mapper.resolve("Date of birth", "input", BANK) is None


def _write_map(tmp_path, rules):
    p = tmp_path / "map.json"
    p.write_text(json.dumps({"version": 1, "rules": rules}))
    return p


def _rule(**over):
    base = {"id": "x.rule", "class": "identity", "match": {"any": ["foo"], "none": []}, "field_kinds": [],
            "bank_path": "identity.email", "transform": "none", "on_missing": "blank",
            "if_required_and_missing": "review", "allow_llm": False, "notes": ""}
    base.update(over)
    return base


@pytest.mark.parametrize("over", [
    {"bank_path": "identity.dob"},
    {"bank_path": "Identity.DOB"},
    {"allow_llm": True},
    {"transform": "rot13"},
    {"on_missing": "guess"},
    {"if_required_and_missing": "blank"},
    {"class": "mystery"},
    {"match": {"any": ["(unclosed"], "none": []}},
])
def test_load_rejects_bad_rules(tmp_path, over):
    with pytest.raises(ValueError):
        mapper.load_rules(_write_map(tmp_path, [_rule(**over)]))


def test_load_rejects_duplicate_ids(tmp_path):
    with pytest.raises(ValueError):
        mapper.load_rules(_write_map(tmp_path, [_rule(), _rule()]))


def test_map_path_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ANSWERS_MAP_PATH", str(_write_map(tmp_path, [_rule()])))
    assert [r["id"] for r in mapper.load_rules()] == ["x.rule"]


@pytest.mark.parametrize("q,rule_id,value", [
    ("Your email address", "identity.email", "person@example.test"),
    ("Phone number", "identity.phone", "+15550100100"),
    ("First name", "identity.first_name", "test-identity.first_name"),
    ("Last name", "identity.last_name", "test-identity.last_name"),
    ("Full name", "identity.full_name", "test-identity.full_name"),
    ("First and last name", "identity.full_name", "test-identity.full_name"),
    ("LinkedIn Profile URL", "identity.linkedin", "test-identity.linkedin"),
    ("GitHub URL", "identity.github", "test-identity.github"),
    ("What email is associated with your LinkedIn account?", "identity.linkedin_email", "test-linkedin_email.answer"),
    ("Which email did you use to apply?", "identity.application_email", "test-application_email.answer"),
    ("Are you willing to relocate within the US?", "location.us_relocation", "test-us_relocation.answer"),
    ("Can you work in NYC 5 days a week in person?", "location.nyc_hybrid",
     "test-location_preferences.nyc_in_person_5days"),
])
def test_mapper_resolve(q, rule_id, value):
    got = mapper.resolve(q, "input", copy.deepcopy(BANK))
    assert got["id"] == rule_id and got["value"] == value and got["missing"] is True or (
        got["id"] == rule_id and got["value"] == value and got["missing"] is False)


def test_mapper_missing_value():
    got = mapper.resolve("LinkedIn URL", "input", {})
    assert got["id"] == "identity.linkedin" and got["value"] is None and got["missing"] is True


def test_mapper_respects_field_kinds():
    assert mapper.resolve("Email", "file", BANK) is None


def test_mapper_deny_list_from_bank():
    bank = copy.deepcopy(BANK)
    bank["unknown_do_not_invent"] = ["favorite color"]
    got = mapper.resolve("What is your favorite color?", "input", bank)
    assert got["id"] == "deny_list" and got["class"] == "unknown" and got["on_missing"] == "review"
    assert got["value"] is None and got["missing"] is True


# ── mapper: third-party fields and scoped location rules ───────────────────────
@pytest.mark.parametrize("q", [
    "Reference first name",
    "Reference last name",
    "Reference email",
    "Reference phone number",
    "Emergency contact phone number",
    "Supervisor email",
    "Hiring manager email",
    "Previous employer phone",
    "Recommender LinkedIn URL",
])
def test_identity_rules_never_match_third_party_fields(q):
    got = mapper.resolve(q, "input", copy.deepcopy(BANK))
    assert got is None or not got["id"].startswith("identity.")


def test_reference_first_name_is_not_the_candidates_name():
    d = rf("Reference first name")
    assert d.value != BANK["identity"]["first_name"]
    assert d.value is None and d.action in ("blank", "review")
    d = rf("Reference first name", required=True)
    assert d.value is None and d.action == "review"


def test_own_first_name_still_maps():
    assert mapper.resolve("First name", "input", copy.deepcopy(BANK))["id"] == "identity.first_name"


@pytest.mark.parametrize("q", [
    "Have you worked in NYC in person?",
    "Did you work on-site in New York previously?",
    "Have you previously worked in person in NYC five days a week?",
])
def test_nyc_history_questions_are_not_willingness_questions(q):
    got = mapper.resolve(q, "input", copy.deepcopy(BANK))
    assert got is None or got["id"] != "location.nyc_hybrid"
    d = rf(q)
    assert d.rule_id != "location.nyc_hybrid"
    assert d.value != "test-location_preferences.nyc_in_person_5days"


def test_nyc_willingness_question_still_maps():
    got = mapper.resolve("Are you willing to work in NYC in person 5 days a week?", "input", copy.deepcopy(BANK))
    assert got["id"] == "location.nyc_hybrid"


@pytest.mark.parametrize("q", [
    "Are you willing to relocate to Peru?",
    "Are you open to relocating to Venezuela?",
    "Would you relocate to Canada?",
])
def test_non_us_relocation_is_not_answered_with_the_us_answer(q):
    got = mapper.resolve(q, "input", copy.deepcopy(BANK))
    assert got is None or got["id"] != "location.us_relocation"
    d = rf(q, "select")
    assert d.rule_id != "location.us_relocation" and d.value is None
    assert d.action in ("review", "blank")


@pytest.mark.parametrize("q", [
    "Does this role offer relocation assistance?",
    "Relocation package details",
])
def test_relocation_questions_not_about_the_candidate_do_not_map(q):
    got = mapper.resolve(q, "input", copy.deepcopy(BANK))
    assert got is None or got["id"] != "location.us_relocation"


def test_us_relocation_none_list_gets_the_shared_country_pattern():
    rule = next(r for r in mapper.load_rules() if r["id"] == "location.us_relocation")
    assert workauth._COUNTRY_RE in rule["_none"]


@pytest.mark.parametrize("value,name,expected", [
    ("+1 555-010-0100", "phone_e164", "+15550100100"),
    ("(555) 010-0100", "phone_e164", "+15550100100"),
    ("15550100100", "phone_e164", "+15550100100"),
    ("", "phone_e164", None),
    ("abc", "phone_e164", None),
    ("Ada Lovelace King", "split_first", "Ada"),
    ("Ada Lovelace King", "split_last", "King"),
    ("Ada", "split_last", None),
    ("", "split_first", None),
    (True, "yes_no", "Yes"),
    (False, "yes_no", "No"),
    ("yes", "yes_no", "Yes"),
    ("No", "yes_no", "No"),
    ("false", "yes_no", "No"),
    ("maybe", "yes_no", None),
    (1, "yes_no", "Yes"),
    (0, "yes_no", "No"),
    (None, "yes_no", None),
    ("keep", "none", "keep"),
])
def test_transforms(value, name, expected):
    assert mapper.apply_transform(value, name) == expected


# ── resolve_field ──────────────────────────────────────────────────────────────
def test_identity_answered_from_bank():
    d = rf("Email address", required=True)
    assert isinstance(d, FieldDecision)
    assert (d.action, d.value, d.rule_id, d.bank_path, d.source) == (
        "answer", "person@example.test", "identity.email", "identity.email", "bank")


def test_required_unknown_reviews_optional_unknown_blank():
    q = "What is your favorite color?"
    req, opt = rf(q, required=True), rf(q, required=False)
    assert (req.action, req.reason_codes) == ("review", ["required_unmapped"])
    assert (opt.action, opt.reason_codes) == ("blank", ["unknown_blank"])


def test_optional_mapped_rule_with_missing_bank_value_is_blank_required_is_review():
    assert rf("LinkedIn URL", bank={}).action == "blank"
    assert rf("LinkedIn URL", bank={}, required=True).action == "review"
    assert rf("Are you willing to relocate?", bank={}).action == "review"  # rule says on_missing review


def test_work_auth_delegation():
    q_auth = "Are you legally authorized to work in the United States?"
    d = rf(q_auth, "select", True)
    assert (d.action, d.value, d.rule_id, d.source) == ("answer", "Yes", "work_auth.authorization", "facts")
    d = rf("Will you now or in the future require sponsorship?", "select", True)
    assert (d.action, d.value, d.rule_id) == ("answer", "Yes", "work_auth.sponsorship")
    d = rf("Are you able to work in the US without sponsorship?", "select", True)
    assert (d.action, d.value) == ("answer", "No")
    d = rf("Do you currently require sponsorship?", "select", True)
    assert (d.action, d.value, d.reason_codes) == ("review", None, ["current_scope_ambiguous"])
    d = rf("I am legally authorized to work in the United States", "checkbox", True)
    assert (d.action, d.value) == ("answer", "Yes")


def test_ambiguous_unknown_routes_to_review():
    d = rf("Are you authorized to work in Canada?", "select")
    assert (d.action, d.reason_codes, d.qclass) == ("review", ["foreign_country"], QuestionClass.UNKNOWN)
    d = rf("Will you require sponsorship and are you willing to relocate?", "select")
    assert (d.action, d.reason_codes) == ("review", ["multi_topic"])
    assert rf("Are you willing to relocate to Canada?", "select").action == "review"


@pytest.mark.parametrize("q,reason", [
    ("What are your salary expectations?", "deny_salary"),
    ("Do you hold a security clearance?", "deny_clearance"),
    ("Have you ever been convicted of a felony?", "deny_criminal"),
    ("What is your social security number?", "deny_ssn"),
    ("What is your visa status?", "deny_visa_status"),
    ("Are you a US citizen?", "deny_visa_status"),
])
def test_denied_topics(q, reason):
    req, opt = rf(q, required=True), rf(q, required=False)
    assert (req.action, req.reason_codes, req.value) == ("review", [reason], None)
    assert (opt.action, opt.reason_codes, opt.value) == ("blank", [reason], None)


def test_bank_deny_list_forces_review_even_when_optional():
    bank = copy.deepcopy(BANK)
    bank["unknown_do_not_invent"] = ["favorite color"]
    d = rf("What is your favorite color?", bank=bank, required=False)
    assert (d.action, d.reason_codes) == ("review", ["deny_list"])


def test_rule_conflict_reviews():
    d = rf("Best phone number or email to reach you", required=True)
    assert d.action == "review" and d.reason_codes == ["rule_conflict"]


def test_eeo_single_decline_option_is_answered():
    d = rf("Gender", "select", options=["Male", "Female", "Decline to self-identify"])
    assert (d.action, d.value, d.source) == ("answer", "Decline to self-identify", "declined")
    d = rf("Veteran Status", "radio", True, options=["I am a veteran", "I do not wish to answer"])
    assert (d.action, d.value) == ("answer", "I do not wish to answer")


@pytest.mark.parametrize("options", [
    None, [], ["Male", "Female"], ["Decline to self-identify", "I prefer not to answer"]])
def test_eeo_without_exactly_one_decline_is_blank(options):
    d = rf("Gender", "select", True, options=options)
    assert (d.action, d.value) == ("blank", None)


def test_checkbox_never_ticked():
    q = "I would like to receive job alerts"
    assert rf(q, "checkbox", False).action == "blank"
    d = rf(q, "checkbox", True)
    assert (d.action, d.value, d.reason_codes) == ("review", None, ["checkbox_never_ticked"])


def test_skip_decision():
    assert rf("Submit application").action == "skip"


# ── audit ──────────────────────────────────────────────────────────────────────
Q, V = "UNIQUE-QUESTION-TEXT about something?", "UNIQUE-VALUE-XYZ"


def _log(tmp_path, **over):
    kw = dict(job_id="job1", attempt_id="att1", field_ref="#f1", question=Q, qclass="identity",
              rule_id="identity.email", bank_path="identity.email", source="bank", action="answer",
              reason_codes=[], value=V, artifacts_dir=str(tmp_path / "art"), audit_path=str(tmp_path / "audit.jsonl"))
    kw.update(over)
    return log_answer(**kw)


def test_audit_one_entry_per_call_and_privacy(tmp_path):
    entry = _log(tmp_path)
    _log(tmp_path, field_ref="#f2")
    job_file = tmp_path / "art" / "job1" / "answers.json"
    audit = tmp_path / "audit.jsonl"
    entries = json.loads(job_file.read_text())
    lines = audit.read_text().splitlines()
    assert len(entries) == 2 and len(lines) == 2
    assert entries[0]["question"] == Q                       # private file holds the text
    assert entries[0]["question_hash"] == entry["question_hash"] and len(entry["question_hash"]) == 64
    assert entries[0]["value_hash"] is not None and entry["job_id"] == "job1"
    for text in (audit.read_text(), job_file.read_text()):
        assert V not in text                                 # no raw values anywhere
    assert Q not in audit.read_text() and "question\"" not in audit.read_text()
    for path in (job_file, audit):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_audit_global_jsonl_never_holds_the_raw_field_ref(tmp_path):
    ref = "input[name=email][value=x@y.z]"
    entry = _log(tmp_path, field_ref=ref)
    audit_text = (tmp_path / "audit.jsonl").read_text()
    job_text = (tmp_path / "art" / "job1" / "answers.json").read_text()
    assert ref not in audit_text and "x@y.z" not in audit_text
    assert '"field_ref"' not in audit_text
    assert json.loads(audit_text.splitlines()[0])["field_ref_hash"] == hashlib.sha256(ref.encode()).hexdigest()
    assert entry["field_ref_hash"] == hashlib.sha256(ref.encode()).hexdigest() and "field_ref" not in entry
    assert json.loads(job_text)[0]["field_ref"] == ref       # the private per-job file keeps the raw selector


def test_audit_none_field_ref_has_null_hash(tmp_path):
    assert _log(tmp_path, field_ref=None)["field_ref_hash"] is None


def test_audit_none_value_has_null_hash(tmp_path):
    assert _log(tmp_path, value=None, action="blank", source="blank")["value_hash"] is None


def test_audit_emit_gets_hashes_only(tmp_path):
    calls = []
    entry = _log(tmp_path, emit=lambda *a, **k: calls.append((a, k)))
    assert calls == [(("answer_decided", "job1"),
                      {"question_hash": entry["question_hash"], "action": "answer", "rule_id": "identity.email"})]
    assert Q not in repr(calls) and V not in repr(calls)


def test_audit_skip_is_not_logged(tmp_path):
    _log(tmp_path, action="skip", source="blank")
    assert not (tmp_path / "art").exists() and not (tmp_path / "audit.jsonl").exists()


@pytest.mark.parametrize("over", [{"source": "magic"}, {"action": "maybe"}, {"job_id": "../evil"}, {"job_id": "a/b"}])
def test_audit_validates_inputs(tmp_path, over):
    with pytest.raises(ValueError):
        _log(tmp_path, **over)


def test_audit_write_failures_raise(tmp_path):
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    with pytest.raises(OSError):
        _log(tmp_path, audit_path=str(blocker / "x.jsonl"))


def test_resolve_field_writes_audit(tmp_path):
    d = rf("Email address", required=True, artifacts_dir=str(tmp_path / "art"), job_id="job1",
           attempt_id="a1", field_ref="#email", audit_path=str(tmp_path / "audit.jsonl"))
    assert d.action == "answer"
    job_text = (tmp_path / "art" / "job1" / "answers.json").read_text()
    audit_text = (tmp_path / "audit.jsonl").read_text()
    assert "Email address" in job_text and "Email address" not in audit_text
    assert "person@example.test" not in job_text + audit_text
    rf("Submit application", artifacts_dir=str(tmp_path / "art"), job_id="job1",
       audit_path=str(tmp_path / "audit.jsonl"))
    assert len(json.loads((tmp_path / "art" / "job1" / "answers.json").read_text())) == 1


def test_resolve_field_needs_job_id_for_audit(tmp_path):
    with pytest.raises(ValueError):
        rf("Email address", artifacts_dir=str(tmp_path))


# ── bin/bank_schema_dump.py ────────────────────────────────────────────────────
def _load_dump_module():
    spec = importlib.util.spec_from_file_location("bank_schema_dump", ROOT / "bin" / "bank_schema_dump.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bank_schema_dump_prints_keys_and_types_only():
    mod = _load_dump_module()
    bank = {"identity": {"email": "SECRET-VALUE", "n": 3}, "unknown_do_not_invent": ["SECRET-TOPIC"], "empty": {}}
    lines = mod.dump(bank)
    assert lines == ["empty: dict", "identity.email: str", "identity.n: int", "unknown_do_not_invent: list[str]"]
    assert "SECRET" not in "\n".join(lines)


def test_bank_schema_dump_main(tmp_path, capsys):
    mod = _load_dump_module()
    p = tmp_path / "bank.json"
    p.write_text(json.dumps({"a": {"b": "SECRET-VALUE"}}))
    assert mod.main(["--bank", str(p)]) == 0
    out = capsys.readouterr().out
    assert out == "a.b: str\n"
