"""Tests for ats -- public ATS career-page job fetching. All HTTP is mocked."""

import pytest

import ats
import config


# ── Slug derivation ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("company,expected_first", [
    ("Stripe", "stripe"),
    ("stripe", "stripe"),
    ("  Airbnb  ", "airbnb"),
    ("Acme Corp, Inc.", "acme"),
    ("Databricks Inc", "databricks"),
    ("Amazon.com", "amazoncom"),
    ("Notion Labs", "notionlabs"),
])
def test_slug_candidates_first_is_naive_lowercase_join(company, expected_first):
    assert ats._slug_candidates(company)[0] == expected_first


@pytest.mark.parametrize("company", ["", None, "   ", 123, ",,,", ["Stripe"]])
def test_slug_candidates_empty_for_garbage(company):
    assert ats._slug_candidates(company) == []


def test_slug_candidates_adds_hyphenated_variant():
    assert ats._slug_candidates("Notion Labs") == ["notionlabs", "notion-labs"]


def test_slug_candidates_single_token_has_no_second_variant():
    assert ats._slug_candidates("Figma") == ["figma"]


def test_slug_candidates_respects_cap(mocker):
    mocker.patch.object(config, "ATS_MAX_SLUG_CANDIDATES", 1)
    assert ats._slug_candidates("Notion Labs") == ["notionlabs"]


def test_slug_candidates_keeps_suffix_when_it_is_the_only_token():
    assert ats._slug_candidates("Corp") == ["corp"]


def test_slug_candidates_strips_stacked_suffixes():
    assert ats._slug_candidates("Acme Corp LLC")[0] == "acme"


# ── HTML stripping ─────────────────────────────────────────────────────────────

def test_strip_html_unescapes_and_drops_tags():
    escaped = "&lt;p&gt;We are hiring &amp;amp; growing.&lt;/p&gt;"
    assert ats._strip_html(escaped) == "We are hiring & growing."


def test_strip_html_collapses_whitespace():
    assert ats._strip_html("<div>a\n\n  b</div>") == "a b"


@pytest.mark.parametrize("value", ["", None, 123, {"a": 1}])
def test_strip_html_returns_empty_for_non_string(value):
    assert ats._strip_html(value) == ""


# ── Provider payload parsing ───────────────────────────────────────────────────

_GREENHOUSE_PAYLOAD = {
    "jobs": [
        {
            "title": "Senior Backend Engineer",
            "location": {"name": "Remote, US"},
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
            "content": "&lt;p&gt;Build payments infra &amp;amp; APIs.&lt;/p&gt;",
        },
        {
            "title": "Product Designer",
            "location": {"name": "New York"},
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
            "content": "&lt;p&gt;Design things.&lt;/p&gt;",
        },
    ]
}

_ASHBY_PAYLOAD = {
    "jobs": [
        {
            "title": "Backend Engineer",
            "location": "Remote",
            "jobUrl": "https://jobs.ashbyhq.com/acme/1",
            "descriptionPlain": "Plain description text.",
            "descriptionHtml": "<p>HTML description text.</p>",
        },
        {
            "title": "Data Scientist",
            "location": "SF",
            "jobUrl": "https://jobs.ashbyhq.com/acme/2",
            "descriptionHtml": "<p>Only HTML here.</p>",
        },
    ]
}

_LEVER_PAYLOAD = [
    {
        "text": "Backend Engineer",
        "categories": {"location": "Remote", "team": "Platform"},
        "hostedUrl": "https://jobs.lever.co/acme/1",
        "descriptionPlain": "Lever plain text.",
    },
    {
        "text": "Recruiter",
        "categories": {"location": "NYC"},
        "hostedUrl": "https://jobs.lever.co/acme/2",
        "description": "<p>Lever html only.</p>",
    },
]


def test_parse_greenhouse_normalizes_shape():
    jobs = ats._parse_greenhouse(_GREENHOUSE_PAYLOAD)
    assert jobs[0] == {
        "title": "Senior Backend Engineer",
        "location": "Remote, US",
        "url": "https://boards.greenhouse.io/acme/jobs/1",
        "description": "Build payments infra & APIs.",
        "source": "greenhouse",
    }
    assert len(jobs) == 2


def test_parse_ashby_prefers_plain_description():
    jobs = ats._parse_ashby(_ASHBY_PAYLOAD)
    assert jobs[0]["description"] == "Plain description text."
    assert jobs[0]["source"] == "ashby"
    assert jobs[0]["url"] == "https://jobs.ashbyhq.com/acme/1"


def test_parse_ashby_falls_back_to_html_description():
    jobs = ats._parse_ashby(_ASHBY_PAYLOAD)
    assert jobs[1]["description"] == "Only HTML here."


def test_parse_lever_reads_top_level_list():
    jobs = ats._parse_lever(_LEVER_PAYLOAD)
    assert jobs[0]["title"] == "Backend Engineer"
    assert jobs[0]["location"] == "Remote"
    assert jobs[0]["url"] == "https://jobs.lever.co/acme/1"
    assert jobs[0]["description"] == "Lever plain text."
    assert jobs[0]["source"] == "lever"


def test_parse_lever_falls_back_to_html_description():
    assert ats._parse_lever(_LEVER_PAYLOAD)[1]["description"] == "Lever html only."


@pytest.mark.parametrize("parser", [
    ats._parse_greenhouse, ats._parse_ashby, ats._parse_lever,
])
@pytest.mark.parametrize("payload", [
    None, {}, [], "not a payload", 42, {"jobs": None}, {"jobs": "nope"},
    {"jobs": [None, "string", 7]}, [None, "string"],
])
def test_parsers_tolerate_garbage_payloads(parser, payload):
    assert parser(payload) == []


@pytest.mark.parametrize("parser,payload", [
    (ats._parse_greenhouse, {"jobs": [{"title": "Engineer"}]}),
    (ats._parse_ashby, {"jobs": [{"title": "Engineer"}]}),
    (ats._parse_lever, [{"text": "Engineer"}]),
])
def test_parsers_tolerate_missing_keys(parser, payload):
    job = parser(payload)[0]
    assert job["title"] == "Engineer"
    assert job["location"] == ""
    assert job["url"] == ""
    assert job["description"] == ""


def test_parsers_drop_entries_without_a_title():
    assert ats._parse_greenhouse({"jobs": [{"location": {"name": "NY"}}]}) == []


def test_description_is_truncated(mocker):
    mocker.patch.object(config, "ATS_MAX_DESCRIPTION_CHARS", 10)
    payload = {"jobs": [{"title": "Engineer", "content": "x" * 500}]}
    assert ats._parse_greenhouse(payload)[0]["description"] == "x" * 10
