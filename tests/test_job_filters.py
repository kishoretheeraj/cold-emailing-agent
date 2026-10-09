"""job_filters: zero-token triage before any posting is saved (spec 2026-10-08 fifty-a-day §3.2).
Location cases follow Career-Ops' scan.mjs semantics and regressions."""

import json
from datetime import datetime, timedelta, timezone

import pytest

import job_filters

PREFS = job_filters.load_preferences({})
NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


# ── Titles ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title", [
    "Associate Product Manager", "Product Manager", "Product Manager II", "APM, Payments",
    "Product Analyst", "Technical Product Manager", "Product Manager, New Grad",
    "Business Analyst", "Program Manager", "Strategy & Operations Associate", "Product Owner",
    "Product Manager-in-Training", "Product Management Analyst",
    # Regressions from the 2026-10-08 Simplify feed run: real PM roles the first word lists dropped.
    "Product Manager Graduate - Sales and Operations Management Platform",
    "Senior Associate, Product Management", "Associate Product Line Manager",
])
def test_target_titles_pass(title):
    assert job_filters.title_reason(title, PREFS) is None


@pytest.mark.parametrize("title,reason", [
    ("Senior Product Manager", "seniority"),
    ("Sr. Product Manager", "seniority"),
    ("Staff Product Manager", "seniority"),
    ("Principal Product Manager", "seniority"),
    ("Director, Product Management", "seniority"),
    ("VP of Product", "seniority"),
    ("Head of Product", "seniority"),
    ("Group Product Manager", "seniority"),
    ("Product Lead", "seniority"),
    ("Product Management Intern", "seniority"),
    ("Software Development Engineer", "title"),
    ("Store Manager", "title"),
    ("Equipment Maintenance Technician", "title"),
    ("Product Designer", "title"),
    ("Product Marketing Manager", "title"),
    ("Account Executive", "title"),
    ("Sales Strategy Manager", "title"),
    ("Associate Product Line Manager - Temporary", "title"),
    ("", "title"),
    (None, "title"),
])
def test_other_titles_are_rejected_with_a_reason(title, reason):
    assert job_filters.title_reason(title, PREFS) == reason


def test_short_keywords_match_whole_words_only():
    prefs = job_filters.load_preferences({"job_search_preferences": json.dumps(
        {"titles": {"include": ["pm"], "exclude": ["sr"]}})})
    assert job_filters.title_reason("PM, Growth", prefs) is None
    assert job_filters.title_reason("Development Team", prefs) == "title"      # no "pm" word
    assert job_filters.title_reason("Srinivasan's PM team", prefs) is None   # "sr" is not a word here


def test_stem_keywords_match_word_prefixes():
    prefs = job_filters.load_preferences({"job_search_preferences": json.dumps(
        {"titles": {"include": ["stem:product"], "exclude": []}})})
    assert job_filters.title_reason("Productivity Analyst", prefs) is None
    assert job_filters.title_reason("Reproduction Analyst", prefs) == "title"


# ── Locations ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("location", [
    "San Francisco, CA", "New York, NY", "Boston, Massachusetts", "SF", "NYC", "Remote",
    "Remote in USA", "United States", "Dublin, OH", "Indianapolis, IN", "Indian Head, MD",
    "Austin, TX | Remote", "Seattle, WA; Toronto, ON", "", None,
])
def test_us_and_remote_locations_pass(location):
    assert job_filters.location_ok(location, None, "Product Manager", PREFS)


@pytest.mark.parametrize("location", [
    "Bangalore, India", "London, United Kingdom", "Toronto, ON, Canada", "Berlin, Germany",
    "Singapore", "Remote - Canada", "Manchester, UK", "Sydney, Australia",
])
def test_foreign_locations_are_rejected(location):
    assert not job_filters.location_ok(location, None, "Product Manager", PREFS)


def test_workday_rolled_up_location_is_read_from_the_url():
    url = "https://acme.wd5.myworkdayjobs.com/External/job/Hyderabad-Telangana-India/Product-Manager_R-65193"
    assert not job_filters.location_ok("5 Locations", url, "Product Manager", PREFS)
    url = "https://acme.wd5.myworkdayjobs.com/External/job/Austin-TX/Product-Manager_R-65193"
    assert job_filters.location_ok("5 Locations", url, "Product Manager", PREFS)


def test_remote_in_the_title_rescues_an_office_location_but_never_a_blocked_one():
    assert job_filters.location_ok("Ottawa, Ontario", None, "Product Manager - Remote", PREFS) is True
    assert job_filters.location_ok("Bengaluru, India", None, "Product Manager - Remote", PREFS) is False
    assert job_filters.location_ok("Ottawa, Ontario", None, "Remote Sensing Product Manager", PREFS) is False
    assert job_filters.location_ok("Ottawa, Ontario", None, "Non-Remote Product Manager", PREFS) is False


def test_locations_accept_a_list():
    assert job_filters.location_ok(["London, UK", "New York, NY"], None, "PM", PREFS)
    assert not job_filters.location_ok(["London, UK", "Paris, France"], None, "PM", PREFS)


# ── Posting age ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("posted,ok", [
    (NOW - timedelta(days=3), True),
    ((NOW - timedelta(days=29)).isoformat(), True),
    ((NOW - timedelta(days=31)).isoformat(), False),
    (int((NOW - timedelta(days=40)).timestamp()), False),
    (None, True),
    ("not a date", True),
])
def test_posting_age(posted, ok):
    assert job_filters.fresh_enough(posted, PREFS, now=NOW) is ok


# ── Sponsorship ────────────────────────────────────────────────────────────────

NEEDS = {"requires_visa_sponsorship": "Yes"}


@pytest.mark.parametrize("job,eligibility,ok", [
    ({"sponsorship": "Does Not Offer Sponsorship"}, NEEDS, False),
    ({"sponsorship": "U.S. Citizenship is Required"}, NEEDS, False),
    ({"sponsorship": "Offers Sponsorship"}, NEEDS, True),
    ({"sponsorship": "Other"}, NEEDS, True),
    ({"description": "We are unable to sponsor visas for this role."}, NEEDS, False),
    ({"description": "Must be a U.S. citizen."}, NEEDS, False),
    ({"sponsorship": "Does Not Offer Sponsorship"}, {"requires_visa_sponsorship": "No"}, True),
    ({"sponsorship": "Does Not Offer Sponsorship"}, {}, True),
])
def test_sponsorship(job, eligibility, ok):
    assert job_filters.sponsorship_ok(job, PREFS, eligibility) is ok


# ── One verdict per posting ────────────────────────────────────────────────────

def _job(**over):
    base = {"company": "Acme", "title": "Associate Product Manager", "url": "https://jobs.lever.co/a/1",
            "location": "New York, NY", "posted_at": (NOW - timedelta(days=2)).isoformat(),
            "description": "Own the roadmap.", "sponsorship": "Other"}
    return {**base, **over}


@pytest.mark.parametrize("over,reason", [
    ({}, None),
    ({"title": "Senior Product Manager"}, "seniority"),
    ({"title": "Data Engineer"}, "title"),
    ({"location": "London, UK"}, "location"),
    ({"posted_at": (NOW - timedelta(days=90)).isoformat()}, "stale"),
    ({"sponsorship": "Does Not Offer Sponsorship"}, "no_sponsorship"),
])
def test_reject_reason(over, reason):
    assert job_filters.reject_reason(_job(**over), PREFS, NEEDS, now=NOW) == reason


# ── Preferences row ────────────────────────────────────────────────────────────

def test_preferences_merge_over_defaults():
    prefs = job_filters.load_preferences({"job_search_preferences": json.dumps(
        {"max_posting_age_days": 7, "daily_submit_cap": 20})})
    assert prefs["max_posting_age_days"] == 7 and prefs["daily_submit_cap"] == 20
    assert prefs["titles"] == job_filters.DEFAULT_PREFERENCES["titles"]


@pytest.mark.parametrize("raw", ["not json", "[]", json.dumps({"daily_submit_cap": "lots"}),
                                 json.dumps({"titles": {"include": "pm"}})])
def test_bad_preferences_fall_back_to_defaults(raw):
    prefs = job_filters.load_preferences({"job_search_preferences": raw})
    assert prefs["daily_submit_cap"] == job_filters.DEFAULT_PREFERENCES["daily_submit_cap"]
    assert prefs["titles"] == job_filters.DEFAULT_PREFERENCES["titles"]
