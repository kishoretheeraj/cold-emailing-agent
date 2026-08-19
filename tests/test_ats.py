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
