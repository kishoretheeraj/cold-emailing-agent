"""Round-2 regressions: NYC willingness-only matching and relocation destination handling.

Uses a synthetic bank; no real answers or personal data.
"""
import pytest

from answers import mapper, resolve_field, workauth

BANK = {
    "us_relocation": {"answer": "synthetic-reloc"},
    "location_preferences": {"nyc_in_person_5days": "synthetic-nyc"},
}


def _rf(q, kind="select", required=False):
    return resolve_field(q, kind, dict(BANK), required)


def _rule_id(q):
    got = mapper.resolve(q, "input", dict(BANK))
    return got["id"] if got else None


# ── R2: nyc_hybrid ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    "Have you ever worked onsite in NYC?",
    "Have you ever worked in person in New York five days a week?",
    "Have you been onsite in NYC in the past?",
    "Did you work in person in NYC 5 days a week?",
    "What is your previous NYC in-person experience, 5 days a week?",
    "Describe your history of working on-site in New York.",
])
def test_nyc_history_questions_never_match_the_willingness_rule(q):
    assert _rule_id(q) != "location.nyc_hybrid"
    d = _rf(q, "input")
    assert d.rule_id != "location.nyc_hybrid" and d.value != "synthetic-nyc"


@pytest.mark.parametrize("q", [
    "NYC in person 5 days?",
    "NYC office, five days a week on-site",
    "New York on-site 5 days",
])
def test_nyc_bare_ask_without_a_willingness_verb_is_unknown(q):
    assert _rule_id(q) != "location.nyc_hybrid"
    d = _rf(q, "input", required=True)
    assert d.value is None and d.action == "review"
    d = _rf(q, "input", required=False)
    assert d.value is None and d.action in ("blank", "review")


@pytest.mark.parametrize("q", [
    "Are you willing to work in NYC in person 5 days a week?",
    "Are you able to work on-site in New York five days a week?",
    "Are you open to working in person in NYC 5 days per week?",
    "Are you comfortable with an on-site NYC schedule, 5 days a week?",
    "Can you work in NYC 5 days a week in person?",
    "Are you willing to work in person in NYC 5 days a week?",
])
def test_nyc_willingness_questions_still_map(q):
    assert _rule_id(q) == "location.nyc_hybrid"
    d = _rf(q, "input")
    assert (d.action, d.value) == ("answer", "synthetic-nyc")


@pytest.mark.parametrize("q", [
    "Can you describe your experience working onsite in NYC?",
    "Tell us about your experience working in person in New York.",
    "Do you have experience working on-site in NYC?",
])
def test_nyc_experience_questions_do_not_match_the_willingness_rule(q):
    assert _rule_id(q) != "location.nyc_hybrid"
    d = _rf(q, "input")
    assert d.rule_id != "location.nyc_hybrid" and d.value != "synthetic-nyc"
    assert d.action in ("blank", "review")


def test_willingness_to_work_onsite_outside_nyc_does_not_match_nyc_rule():
    assert _rule_id("Are you willing to work onsite in Boston?") != "location.nyc_hybrid"


# ── R3: relocation countries and destinations ──────────────────────────────────

def test_reloc_country_set_is_a_superset_of_the_workauth_list_plus_extras():
    assert set(workauth._COUNTRIES) <= mapper._RELOC_COUNTRIES
    for name in ("cuba", "jamaica", "haiti", "dominican republic", "puerto rico", "guam", "bahamas",
                 "bermuda", "iceland", "luxembourg", "monaco", "qatar", "kuwait", "saudi arabia", "oman",
                 "bahrain", "jordan", "lebanon", "cyprus", "malta", "estonia", "latvia", "lithuania",
                 "slovakia", "slovenia", "croatia", "serbia", "bulgaria", "albania", "morocco", "tunisia",
                 "algeria", "ghana", "senegal", "ethiopia", "tanzania", "uganda", "rwanda", "peru",
                 "ecuador", "bolivia", "paraguay", "uruguay", "venezuela", "guatemala", "honduras",
                 "nicaragua", "costa rica", "panama", "el salvador"):
        assert name in mapper._RELOC_COUNTRIES


def test_us_relocation_none_list_has_both_country_patterns():
    rule = next(r for r in mapper.load_rules() if r["id"] == "location.us_relocation")
    assert workauth._COUNTRY_RE in rule["_none"] and mapper._RELOC_COUNTRY_RE in rule["_none"]


@pytest.mark.parametrize("country", ["Cuba", "Jamaica", "Haiti", "Qatar", "Bolivia", "El Salvador", "Rwanda"])
@pytest.mark.parametrize("required", [False, True])
def test_relocating_to_a_non_us_country_goes_to_review(country, required):
    q = f"Are you willing to relocate to {country}?"
    assert _rule_id(q) != "location.us_relocation"
    d = _rf(q, "select", required)
    assert d.action == "review" and d.value is None
    # either the relocation guard or the ambiguity detector's foreign-country signal;
    # both mean human review, never the US-relocation answer.
    assert d.reason_codes == ["relocation_destination_unclear"] or d.reason_codes == ["foreign_country"]


@pytest.mark.parametrize("q", [
    "Are you willing to relocate to Zurich?",
    "Are you willing to relocate to Lagos?",
    "Would you relocate to Greater Tokyo?",
])
def test_relocating_to_an_unrecognized_destination_goes_to_review(q):
    d = _rf(q, "select")
    assert (d.action, d.value, d.reason_codes) == ("review", None, ["relocation_destination_unclear"])
    assert _rule_id(q) != "location.us_relocation"


@pytest.mark.parametrize("q", [
    "Are you willing to relocate to zurich?",
    "Are you willing to relocate within Zimbabwe?",
    "Are you willing to relocate to Boston or Zurich?",
    "Are you willing to relocate to boston, zurich?",
    "Are you willing to relocate across lagos?",
    "Are you willing to relocate to New York or New Hampshire or Zurich?",
])
def test_round3_relocation_destination_variants_go_to_review(q):
    assert mapper.relocation_destination_unclear(q) is True
    assert _rule_id(q) != "location.us_relocation"
    d = _rf(q, "select")
    assert d.action == "review" and d.value is None


@pytest.mark.parametrize("q", [
    "Are you willing to relocate to Texas?",
    "Are you willing to relocate to the US?",
    "Are you willing to relocate to New York?",
    "Would you relocate to Austin?",
    "Are you open to relocating to Boston?",
    "Are you willing to relocate to Hanover, New Hampshire?",
    "Are you willing to relocate to Boston or Austin?",
    "Are you willing to relocate to boston?",
    "Are you willing to relocate within the US?",
])
def test_relocating_to_a_clearly_us_destination_is_answered(q):
    d = _rf(q, "select")
    assert (d.action, d.value, d.rule_id) == ("answer", "synthetic-reloc", "location.us_relocation")


def test_relocation_without_a_destination_is_answered():
    d = _rf("Are you willing to relocate?", "select")
    assert (d.action, d.value, d.rule_id) == ("answer", "synthetic-reloc", "location.us_relocation")


def test_relocation_destination_unclear_ignores_questions_not_about_the_candidate():
    assert mapper.relocation_destination_unclear("Does this role offer relocation assistance to Cuba?") is False
    assert mapper.relocation_destination_unclear("Are you willing to relocate?") is False
