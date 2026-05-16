"""Tests for research._run_tavily and research._run_hardcoded_fallback."""

from unittest.mock import MagicMock
import pytest

import research


_CONTACT = {"name": "Jane Doe", "company": "Acme Corp"}


def _make_client(results=None, answer="summary answer"):
    client = MagicMock()
    client.search.return_value = {
        "results": results if results is not None else [
            {"title": "Title", "content": "Content text", "url": "https://example.com/page"}
        ],
        "answer": answer,
    }
    return client


def test_run_tavily_returns_list_of_dicts(mocker):
    client = _make_client()
    mocker.patch.object(research, "_get_client", return_value=client)
    queries = ["Jane Doe Acme Corp interview 2026", "Acme Corp news 2026"]
    result = research._run_tavily(queries, _CONTACT)
    assert len(result) == 2
    assert result[0]["query"] == queries[0]
    assert "result" in result[0]


def test_run_tavily_skips_query_that_raises(mocker):
    client = MagicMock()
    client.search.side_effect = [
        Exception("timeout"),
        {"results": [{"title": "T", "content": "C", "url": "https://ex.com"}], "answer": "ans"},
    ]
    mocker.patch.object(research, "_get_client", return_value=client)
    result = research._run_tavily(["bad query", "good query"], _CONTACT)
    assert len(result) == 1
    assert result[0]["query"] == "good query"


def test_run_tavily_skips_query_returning_no_results(mocker):
    client = MagicMock()
    client.search.return_value = {"results": [], "answer": None}
    mocker.patch.object(research, "_get_client", return_value=client)
    result = research._run_tavily(["empty query"], _CONTACT)
    assert result == []


def test_run_tavily_empty_queries_returns_empty(mocker):
    mock_get = mocker.patch.object(research, "_get_client")
    result = research._run_tavily([], _CONTACT)
    assert result == []
    mock_get.assert_not_called()


def test_run_hardcoded_fallback_skips_empty_company(mocker):
    mock_get = mocker.patch.object(research, "_get_client")
    contact = {"name": "Jane", "company": ""}
    result = research._run_hardcoded_fallback(contact)
    assert result == []
    mock_get.assert_not_called()


def test_run_hardcoded_fallback_returns_results(mocker):
    client = _make_client()
    mocker.patch.object(research, "_get_client", return_value=client)
    result = research._run_hardcoded_fallback(_CONTACT)
    assert len(result) == 1
    assert "Acme Corp" in result[0]["query"]
