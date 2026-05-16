"""Tests for research.get_research_brief -- pipeline integration and cache behavior."""

from datetime import datetime, timezone, timedelta

import pytest

import config
import db
import research


_CONTACT = {
    "name": "Jane Doe",
    "company": "Acme Corp",
    "role": "VP Engineering",
    "detail": "Leads 50-person engineering org",
    "notes": "Met at SaaStr",
    "dartmouth": False,
    "tier": 1,
}
_SENDER = "Name: Kishore"


def _fresh_cached(brief_text="cached brief"):
    return {
        "brief_text": brief_text,
        "brief_json": {},
        "cached_at": datetime.now(timezone.utc) - timedelta(days=3),
    }


def _stale_cached(brief_text="old brief"):
    return {
        "brief_text": brief_text,
        "brief_json": {},
        "cached_at": datetime.now(timezone.utc) - timedelta(days=10),
    }


# ── Pre-flight checks ──────────────────────────────────────────────────────────


def test_returns_empty_when_tavily_key_unset(mocker):
    mocker.patch.object(config, "TAVILY_API_KEY", None)
    mock_tavily = mocker.patch.object(research, "_run_tavily")
    result = research.get_research_brief(_CONTACT, _SENDER, {})
    assert result == ""
    mock_tavily.assert_not_called()


def test_returns_empty_when_name_empty(mocker):
    mocker.patch.object(config, "TAVILY_API_KEY", "test-key")
    contact = dict(_CONTACT, name="")
    result = research.get_research_brief(contact, _SENDER, {})
    assert result == ""


def test_returns_empty_when_company_empty(mocker):
    mocker.patch.object(config, "TAVILY_API_KEY", "test-key")
    contact = dict(_CONTACT, company="")
    result = research.get_research_brief(contact, _SENDER, {})
    assert result == ""


# ── Cache behavior ─────────────────────────────────────────────────────────────


def test_fresh_cache_hit_returns_cached_brief_without_tavily(mocker):
    mocker.patch.object(config, "TAVILY_API_KEY", "test-key")
    mocker.patch.object(db, "get_research_cache", return_value=_fresh_cached("the cached brief"))
    mock_tavily = mocker.patch.object(research, "_run_tavily")
    mock_claude = mocker.patch.object(research, "_call_claude")

    result = research.get_research_brief(_CONTACT, _SENDER, {})

    assert result == "the cached brief"
    mock_tavily.assert_not_called()
    mock_claude.assert_not_called()


def test_stale_cache_hit_runs_fresh_research_and_updates_cache(mocker):
    mocker.patch.object(config, "TAVILY_API_KEY", "test-key")
    mocker.patch.object(db, "get_research_cache", return_value=_stale_cached("old brief"))
    mocker.patch.object(research, "_generate_queries", return_value=["q1"])
    mocker.patch.object(research, "_run_tavily", return_value=[{"query": "q1", "result": {}}])
    mocker.patch.object(research, "_curate_brief", return_value="fresh brief")
    mock_set = mocker.patch.object(db, "set_research_cache", return_value=True)

    result = research.get_research_brief(_CONTACT, _SENDER, {})

    assert result == "fresh brief"
    mock_set.assert_called_once()


# ── Happy path ─────────────────────────────────────────────────────────────────


def test_happy_path_queries_tavily_curates_caches(mocker):
    mocker.patch.object(config, "TAVILY_API_KEY", "test-key")
    mocker.patch.object(db, "get_research_cache", return_value=None)
    mocker.patch.object(research, "_generate_queries", return_value=["q1", "q2"])
    mocker.patch.object(
        research, "_run_tavily",
        return_value=[{"query": "q1", "result": {"results": [], "answer": "ans"}}],
    )
    mocker.patch.object(research, "_curate_brief", return_value="curated brief")
    mock_set = mocker.patch.object(db, "set_research_cache", return_value=True)

    result = research.get_research_brief(_CONTACT, _SENDER, {})

    assert result == "curated brief"
    mock_set.assert_called_once()
    assert mock_set.call_args.args[3] == "curated brief"


def test_query_gen_returns_empty_but_fallback_succeeds(mocker):
    mocker.patch.object(config, "TAVILY_API_KEY", "test-key")
    mocker.patch.object(db, "get_research_cache", return_value=None)
    mocker.patch.object(research, "_generate_queries", return_value=[])
    mock_tavily = mocker.patch.object(research, "_run_tavily")
    fallback_result = [{"query": "Acme Corp news 2026", "result": {"results": [], "answer": "news"}}]
    mocker.patch.object(research, "_run_hardcoded_fallback", return_value=fallback_result)
    mocker.patch.object(research, "_curate_brief", return_value="fallback brief")
    mocker.patch.object(db, "set_research_cache", return_value=True)

    result = research.get_research_brief(_CONTACT, _SENDER, {})

    assert result == "fallback brief"
    mock_tavily.assert_not_called()
    research._curate_brief.assert_called_once()


def test_empty_queries_and_empty_fallback_curate_returns_empty(mocker):
    mocker.patch.object(config, "TAVILY_API_KEY", "test-key")
    mocker.patch.object(db, "get_research_cache", return_value=None)
    mocker.patch.object(research, "_generate_queries", return_value=[])
    mocker.patch.object(research, "_run_tavily")
    mocker.patch.object(research, "_run_hardcoded_fallback", return_value=[])
    mock_curate = mocker.patch.object(research, "_curate_brief", return_value="")
    mocker.patch.object(db, "set_research_cache", return_value=True)

    result = research.get_research_brief(_CONTACT, _SENDER, {})

    assert result == ""
    mock_curate.assert_called_once()


def test_empty_brief_is_cached_to_prevent_repeat_research(mocker):
    mocker.patch.object(config, "TAVILY_API_KEY", "test-key")
    mocker.patch.object(db, "get_research_cache", return_value=None)
    mocker.patch.object(research, "_generate_queries", return_value=["q1"])
    mocker.patch.object(research, "_run_tavily", return_value=[{"query": "q1", "result": {}}])
    mocker.patch.object(research, "_curate_brief", return_value="")
    mock_set = mocker.patch.object(db, "set_research_cache", return_value=True)

    result = research.get_research_brief(_CONTACT, _SENDER, {})

    assert result == ""
    mock_set.assert_called_once()
    assert mock_set.call_args.args[3] == ""


# ── Never raises ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("patch_module,patch_attr,exc", [
    ("db", "get_research_cache", ValueError("db read error")),
    ("research", "_generate_queries", RuntimeError("query gen error")),
    ("research", "_run_tavily", Exception("tavily error")),
    ("research", "_curate_brief", Exception("curator error")),
    ("db", "set_research_cache", Exception("db write error")),
])
def test_never_raises_on_failure(patch_module, patch_attr, exc, mocker):
    mocker.patch.object(config, "TAVILY_API_KEY", "test-key")

    mocker.patch.object(db, "get_research_cache", return_value=None)
    mocker.patch.object(research, "_generate_queries", return_value=["q1"])
    mocker.patch.object(research, "_run_tavily", return_value=[{"query": "q1", "result": {}}])
    mocker.patch.object(research, "_curate_brief", return_value="brief")
    mocker.patch.object(db, "set_research_cache", return_value=True)

    mod = db if patch_module == "db" else research
    mocker.patch.object(mod, patch_attr, side_effect=exc)

    result = research.get_research_brief(_CONTACT, _SENDER, {})
    assert result == ""


def test_never_raises_when_get_client_fails(mocker):
    mocker.patch.object(config, "TAVILY_API_KEY", "test-key")
    mocker.patch.object(db, "get_research_cache", return_value=None)
    mocker.patch.object(research, "_generate_queries", return_value=["q1"])
    mocker.patch.object(research, "_get_client", side_effect=RuntimeError("no key"))
    mocker.patch.object(db, "set_research_cache", return_value=True)

    result = research.get_research_brief(_CONTACT, _SENDER, {})
    assert result == ""


# ── Cache key normalization ────────────────────────────────────────────────────


def test_cache_key_is_case_insensitive_and_stripped():
    key1 = research._cache_key("John Smith", "Palm Desert Networks")
    key2 = research._cache_key("john smith", "PALM DESERT NETWORKS")
    key3 = research._cache_key("  John Smith  ", "  Palm Desert Networks  ")
    assert key1 == key2 == key3
