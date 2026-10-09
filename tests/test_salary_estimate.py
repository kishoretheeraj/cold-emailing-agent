"""salary_estimate.estimate(): level walk, floor, rounding, employer matching, never raises."""

import pytest

import salary_estimate


def _row(name, state, filings, p25, p75, years=(2023, 2026)):
    return {"normalized_name": name, "worksite_state": state, "filings": filings, "wage_p25": p25,
            "wage_median": (p25 + p75) // 2, "wage_p75": p75, "fiscal_years": list(years)}


def _job(company="Stripe", role="Product Manager", location="San Francisco, CA"):
    return {"company": company, "role": role, "posting_snapshot": {"location": location}}


@pytest.fixture
def wages(mocker):
    mocker.patch("salary_estimate.db.get_company_intel_by_normalized_names", return_value=[])
    get = mocker.patch("salary_estimate.db.get_h1b_wage_stats", return_value=[])
    return get


def test_uses_the_employers_own_filings_in_the_posting_state_first(wages):
    wages.return_value = [_row("stripe", "CA", 12, 171200, 213800), _row("stripe", "*", 40, 150000, 200000),
                          _row("*", "CA", 900, 140000, 190000), _row("*", "*", 5000, 120000, 170000)]

    result = salary_estimate.estimate(_job())

    assert result["text"] == "$170,000 - $215,000"
    assert (result["low"], result["high"]) == (170000, 215000)
    assert "Stripe's H-1B filings in CA" in result["basis"]
    assert "12 product manager filings, FY2023-FY2026" in result["basis"]
    assert wages.call_args[0] == (["*", "stripe"], "product_manager")


@pytest.mark.parametrize("rows,expected_basis", [
    ([_row("stripe", "CA", 2, 1, 1), _row("stripe", "*", 5, 150000, 200000)], "Stripe's H-1B filings (all states)"),
    ([_row("stripe", "*", 2, 1, 1), _row("*", "CA", 50, 140000, 190000)], "all H-1B filings in CA"),
    ([_row("*", "CA", 9, 1, 1), _row("*", "*", 50, 120000, 170000)], "all H-1B filings nationally"),
])
def test_falls_back_level_by_level_when_filings_are_too_few(wages, rows, expected_basis):
    wages.return_value = rows
    assert expected_basis in salary_estimate.estimate(_job())["basis"]


def test_remote_posting_skips_state_levels(wages):
    wages.return_value = [_row("*", "CA", 50, 1, 1), _row("*", "*", 50, 120000, 170000)]
    result = salary_estimate.estimate(_job(location="United States"))
    assert "nationally" in result["basis"]


def test_never_answers_below_the_operator_floor(wages):
    wages.return_value = [_row("*", "*", 50, 70000, 95000)]
    result = salary_estimate.estimate(_job(location="United States"))
    assert result["text"] == "$100,000"
    assert result["low"] == result["high"] == 100000


def test_returns_none_without_data_or_outside_a_role_family(wages):
    assert salary_estimate.estimate(_job()) is None
    assert salary_estimate.estimate(_job(role="Software Engineer")) is None
    wages.assert_called_once()  # the non-PM role never queried


def test_uses_a_confirmed_company_intel_match_for_the_employer_key(mocker):
    mocker.patch("salary_estimate.db.get_company_intel_by_normalized_names", return_value=[
        {"normalized_name": "axs", "match_status": "confirmed", "matched_employer_id": 7}])
    mocker.patch("salary_estimate.db.get_employer_h1b_normalized_name", return_value="axs group")
    get = mocker.patch("salary_estimate.db.get_h1b_wage_stats",
                       return_value=[_row("axs group", "AZ", 5, 130000, 160000)])

    result = salary_estimate.estimate(_job(company="AXS", location="Scottsdale, AZ"))

    assert get.call_args[0][0] == ["*", "axs group"]
    assert "employer matched as 'axs group'" in result["basis"]


def test_auto_company_intel_match_is_not_used(mocker):
    mocker.patch("salary_estimate.db.get_company_intel_by_normalized_names", return_value=[
        {"normalized_name": "axs", "match_status": "auto", "matched_employer_id": 7}])
    lookup = mocker.patch("salary_estimate.db.get_employer_h1b_normalized_name")
    get = mocker.patch("salary_estimate.db.get_h1b_wage_stats", return_value=[])

    salary_estimate.estimate(_job(company="AXS"))

    lookup.assert_not_called()
    assert get.call_args[0][0] == ["*", "axs"]


def test_needs_review_company_intel_match_is_not_used(mocker):
    mocker.patch("salary_estimate.db.get_company_intel_by_normalized_names", return_value=[
        {"normalized_name": "axs", "match_status": "needs_review", "matched_employer_id": 7}])
    lookup = mocker.patch("salary_estimate.db.get_employer_h1b_normalized_name")
    get = mocker.patch("salary_estimate.db.get_h1b_wage_stats", return_value=[])

    salary_estimate.estimate(_job(company="AXS"))

    lookup.assert_not_called()
    assert get.call_args[0][0] == ["*", "axs"]


def test_never_raises(mocker):
    mocker.patch("salary_estimate.db.get_company_intel_by_normalized_names", side_effect=RuntimeError("db down"))
    assert salary_estimate.estimate(_job()) is None
    assert salary_estimate.estimate({}) is None


@pytest.mark.parametrize("location,expected", [
    ("San Francisco, CA", "CA"),
    ("New York, NY, United States", "NY"),
    (["Chicago, IL", "Remote"], "IL"),
    (["New York, NY", "Austin, TX"], None),
    (["Austin, TX", "Austin, TX"], "TX"),
    ("Remote, US", None),
    ("United States", None),
    ("Remote", None),
    (None, None),
])
def test_state_from_location(location, expected):
    assert salary_estimate.state_from_location(location) == expected


def test_reads_a_json_string_snapshot(wages):
    wages.return_value = [_row("stripe", "NY", 5, 150000, 200000)]
    job = dict(_job(), posting_snapshot='{"location": "New York, NY"}')
    assert "in NY" in salary_estimate.estimate(job)["basis"]
