import pytest
import preflight


def _contact(name="Alice", company="Acme"):
    return {"name": name, "company": company}


def _prompts(forbidden="", watchlist=""):
    return {"forbidden_phrases": forbidden, "guardrail_company_list": watchlist}


# ── check_placeholder_braces ───────────────────────────────────────────────────

def test_braces_pass():
    assert preflight.check_placeholder_braces("Hi Alice, great to meet you.", _contact(), {}) is None

def test_braces_fail():
    result = preflight.check_placeholder_braces("Hi {FIRST NAME}, I loved {COMPANY}.", _contact(), {})
    assert result is not None
    assert "unfilled_braces" in result
    assert "{FIRST NAME}" in result

def test_braces_lowercase_ignored():
    # {name} is a template var that was substituted — lowercase should not flag
    assert preflight.check_placeholder_braces("I reached out to {name}.", _contact(), {}) is None


# ── check_unfilled_brackets ───────────────────────────────────────────────────

def test_brackets_pass():
    assert preflight.check_unfilled_brackets("Congrats on the Series B.", _contact(), {}) is None

def test_brackets_fail():
    result = preflight.check_unfilled_brackets("Hi [First Name], at [Company Name].", _contact(), {})
    assert result is not None
    assert "unfilled_brackets" in result

def test_brackets_lowercase_ignored():
    assert preflight.check_unfilled_brackets("see [attached].", _contact(), {}) is None


# ── check_first_name_presence ─────────────────────────────────────────────────

def test_first_name_present():
    assert preflight.check_first_name_presence("Hi Alice, loved your talk.", _contact(), {}) is None

def test_first_name_missing():
    result = preflight.check_first_name_presence("Saw your work — impressive.", _contact(), {})
    assert result is not None
    assert "first_name_missing" in result

def test_first_name_no_contact_name():
    assert preflight.check_first_name_presence("Hi there.", {"name": ""}, {}) is None

def test_first_name_case_insensitive():
    assert preflight.check_first_name_presence("Hi alice.", _contact(name="Alice"), {}) is None


# ── check_wrong_company ───────────────────────────────────────────────────────

def test_wrong_company_no_watchlist():
    assert preflight.check_wrong_company("Loved what Google is doing.", _contact(), _prompts()) is None

def test_wrong_company_match_own_company():
    # "Acme" in body and contact.company == "Acme" — should NOT flag
    assert preflight.check_wrong_company(
        "Your work at Acme is great.", _contact(company="Acme"),
        _prompts(watchlist="Acme\nGoogle")) is None

def test_wrong_company_flag_other():
    result = preflight.check_wrong_company(
        "I love what Google is doing.", _contact(company="Acme"),
        _prompts(watchlist="Google\nMeta"))
    assert result is not None
    assert "wrong_company" in result
    assert "Google" in result


# ── check_stale_year ──────────────────────────────────────────────────────────

from datetime import date

CURRENT_YEAR = date.today().year
PAST_YEAR = CURRENT_YEAR - 1

def test_stale_year_pass_current_year():
    body = f"looking forward to chatting in {CURRENT_YEAR}."
    assert preflight.check_stale_year(body, _contact(), {}) is None

def test_stale_year_pass_no_future_phrase():
    body = f"Back in {PAST_YEAR} the company launched."
    assert preflight.check_stale_year(body, _contact(), {}) is None

def test_stale_year_fail():
    body = f"looking forward to connecting in {PAST_YEAR}."
    result = preflight.check_stale_year(body, _contact(), {})
    assert result is not None
    assert "stale_year" in result
    assert str(PAST_YEAR) in result

@pytest.mark.parametrize("phrase", [
    "chat in", "meet in", "catch up", "talk in", "connect in", "will", "looking forward",
])
def test_stale_year_all_phrases(phrase):
    body = f"I would love to {phrase} {PAST_YEAR}."
    result = preflight.check_stale_year(body, _contact(), {})
    assert result is not None


# ── check_forbidden_phrases ───────────────────────────────────────────────────

def test_forbidden_empty_list():
    assert preflight.check_forbidden_phrases("I hope this finds you well.", _contact(), _prompts()) is None

def test_forbidden_match():
    result = preflight.check_forbidden_phrases(
        "I hope this finds you well.",
        _contact(), _prompts(forbidden="I hope this finds you well\nsynergy"))
    assert result is not None
    assert "forbidden_phrases" in result

def test_forbidden_no_match():
    assert preflight.check_forbidden_phrases(
        "Just wanted to reach out.", _contact(),
        _prompts(forbidden="synergy\nleveraging")) is None

def test_forbidden_case_insensitive():
    result = preflight.check_forbidden_phrases(
        "SYNERGY is key.", _contact(), _prompts(forbidden="synergy"))
    assert result is not None


# ── check() combined ──────────────────────────────────────────────────────────

def test_check_all_pass():
    body = "Hi Alice, saw what Acme has been building — impressive. Would love to chat."
    assert preflight.check(body, _contact(), {}) == []

def test_check_returns_multiple_failures():
    body = f"Hi [FIRST NAME], your work at Google in {PAST_YEAR} was great. looking forward to it."
    failures = preflight.check(body, _contact(company="Acme"), _prompts(watchlist="Google"))
    assert len(failures) >= 2

def test_check_empty_prompts():
    body = "Hi Alice, great work at Acme. Would love to chat."
    assert preflight.check(body, _contact(), None) == []
