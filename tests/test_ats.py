"""Tests for ats -- public ATS career-page job fetching. All HTTP is mocked."""

import urllib.error

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


# ── Provider cascade ───────────────────────────────────────────────────────────

def _routed_http(mocker, by_host):
    """Mock _http_get_json, returning a payload per provider host, 404 otherwise."""
    calls = []

    def _fake(url):
        calls.append(url)
        for host, payload in by_host.items():
            if host in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    mocker.patch.object(ats, "_http_get_json", side_effect=_fake)
    return calls


def test_greenhouse_hit_short_circuits_the_cascade(mocker):
    calls = _routed_http(mocker, {"greenhouse": _GREENHOUSE_PAYLOAD})
    jobs = ats.fetch_jobs("Acme")
    assert [j["source"] for j in jobs] == ["greenhouse", "greenhouse"]
    assert all("greenhouse" in url for url in calls)


def test_greenhouse_miss_falls_through_to_ashby(mocker):
    calls = _routed_http(mocker, {"ashbyhq": _ASHBY_PAYLOAD})
    jobs = ats.fetch_jobs("Acme")
    assert [j["source"] for j in jobs] == ["ashby", "ashby"]
    assert any("greenhouse" in url for url in calls)
    assert not any("lever" in url for url in calls)


def test_ashby_miss_falls_through_to_lever(mocker):
    _routed_http(mocker, {"lever": _LEVER_PAYLOAD})
    assert [j["source"] for j in ats.fetch_jobs("Acme")] == ["lever", "lever"]


def test_all_providers_miss_returns_empty(mocker):
    _routed_http(mocker, {})
    assert ats.fetch_jobs("Acme") == []


def test_empty_job_list_is_treated_as_a_miss(mocker):
    calls = _routed_http(mocker, {"greenhouse": {"jobs": []}, "ashbyhq": _ASHBY_PAYLOAD})
    assert [j["source"] for j in ats.fetch_jobs("Acme")] == ["ashby", "ashby"]
    assert any("greenhouse" in url for url in calls)


def test_every_slug_candidate_is_tried(mocker):
    calls = _routed_http(mocker, {"notion-labs": _GREENHOUSE_PAYLOAD})
    assert ats.fetch_jobs("Notion Labs")
    assert any("notionlabs" in url for url in calls)
    assert any("notion-labs" in url for url in calls)


def test_greenhouse_url_requests_full_content(mocker):
    calls = _routed_http(mocker, {"greenhouse": _GREENHOUSE_PAYLOAD})
    ats.fetch_jobs("Acme")
    assert "content=true" in calls[0]


def test_role_relevance_ranks_matching_titles_first(mocker):
    _routed_http(mocker, {"greenhouse": _GREENHOUSE_PAYLOAD})
    jobs = ats.fetch_jobs("Acme", role="Product Design Lead")
    assert jobs[0]["title"] == "Product Designer"


def test_no_role_preserves_source_order(mocker):
    _routed_http(mocker, {"greenhouse": _GREENHOUSE_PAYLOAD})
    assert ats.fetch_jobs("Acme")[0]["title"] == "Senior Backend Engineer"


def test_result_is_capped_at_max_jobs(mocker):
    mocker.patch.object(config, "ATS_MAX_JOBS", 1)
    _routed_http(mocker, {"greenhouse": _GREENHOUSE_PAYLOAD})
    assert len(ats.fetch_jobs("Acme")) == 1


def test_disabled_flag_skips_all_http(mocker):
    mocker.patch.object(config, "ATS_ENABLED", False)
    http = mocker.patch.object(ats, "_http_get_json")
    assert ats.fetch_jobs("Acme") == []
    http.assert_not_called()


@pytest.mark.parametrize("company", ["", None, "   ", 42])
def test_unusable_company_skips_all_http(mocker, company):
    http = mocker.patch.object(ats, "_http_get_json")
    assert ats.fetch_jobs(company) == []
    http.assert_not_called()


@pytest.mark.parametrize("failure", [
    urllib.error.URLError("dns failure"),
    urllib.error.HTTPError("http://x", 500, "Server Error", None, None),
    urllib.error.HTTPError("http://x", 404, "Not Found", None, None),
    TimeoutError("timed out"),
    ValueError("Expecting value: line 1 column 1"),
    OSError("connection reset"),
])
def test_fetch_jobs_never_raises_on_transport_failure(mocker, failure):
    mocker.patch.object(ats, "_http_get_json", side_effect=failure)
    assert ats.fetch_jobs("Acme", role="Engineer") == []


@pytest.mark.parametrize("payload", [None, "text", 42, {"jobs": "nope"}, {"unexpected": 1}])
def test_fetch_jobs_never_raises_on_garbage_payload(mocker, payload):
    mocker.patch.object(ats, "_http_get_json", return_value=payload)
    assert ats.fetch_jobs("Acme") == []


def test_fetch_jobs_never_raises_when_ranking_explodes(mocker):
    _routed_http(mocker, {"greenhouse": _GREENHOUSE_PAYLOAD})
    mocker.patch.object(ats, "_rank_jobs", side_effect=RuntimeError("boom"))
    assert ats.fetch_jobs("Acme") == []


def test_role_of_only_stopwords_falls_back_to_source_order(mocker):
    _routed_http(mocker, {"greenhouse": _GREENHOUSE_PAYLOAD})
    assert ats.fetch_jobs("Acme", role="Senior Director")[0]["title"] == "Senior Backend Engineer"


def test_single_word_function_role_still_ranks(mocker):
    _routed_http(mocker, {"greenhouse": _GREENHOUSE_PAYLOAD})
    assert ats.fetch_jobs("Acme", role="Designer")[0]["title"] == "Product Designer"


def test_max_jobs_param_overrides_config_default(mocker):
    _routed_http(mocker, {"greenhouse": _GREENHOUSE_PAYLOAD})
    assert len(ats.fetch_jobs("Acme", max_jobs=1)) == 1


def test_max_jobs_none_falls_back_to_config_default(mocker):
    mocker.patch.object(config, "ATS_MAX_JOBS", 1)
    _routed_http(mocker, {"greenhouse": _GREENHOUSE_PAYLOAD})
    assert len(ats.fetch_jobs("Acme")) == 1
