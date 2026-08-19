"""Tests for research._generate_queries."""

import json
import logging
import pytest

import config
import research


_CONTACT = {
    "name": "Jane Doe",
    "company": "Acme Corp",
    "role": "VP Engineering",
    "detail": "Leads 50-person engineering org",
    "notes": "Met at SaaS conference",
    "dartmouth": False,
    "tier": 1,
}
_SENDER = "Name: Kishore\nProgram: MEM, Dartmouth"


def test_returns_parsed_list(mocker):
    queries = ["Jane Doe Acme Corp interview 2026", "Acme Corp news 2026"]
    mocker.patch.object(research, "_call_claude", return_value=json.dumps(queries))
    result = research._generate_queries(_CONTACT, _SENDER, {})
    assert result == queries


def test_truncates_list_to_max_queries(mocker):
    queries = [f"query number {i}" for i in range(8)]
    mocker.patch.object(research, "_call_claude", return_value=json.dumps(queries))
    result = research._generate_queries(_CONTACT, _SENDER, {})
    assert len(result) == config.RESEARCH_MAX_QUERIES


def test_truncates_long_query_strings(mocker):
    long_q = "a" * 120
    mocker.patch.object(research, "_call_claude", return_value=json.dumps([long_q]))
    result = research._generate_queries(_CONTACT, _SENDER, {})
    assert len(result) == 1
    assert len(result[0]) == config.RESEARCH_MAX_QUERY_LEN


def test_drops_empty_strings(mocker):
    mocker.patch.object(
        research, "_call_claude",
        return_value=json.dumps(["valid query", "", "another query"]),
    )
    result = research._generate_queries(_CONTACT, _SENDER, {})
    assert result == ["valid query", "another query"]


def test_returns_empty_list_on_stray_brace_format_error(mocker):
    """Regression: a stray '{}' in a live-edited prompt raises IndexError
    (not KeyError) during .format() -- must still degrade to [] rather than
    propagate past the intended 'log + return empty' path."""
    bad_prompts = {"research_query_prompt": "{name} at {company}: {}"}
    result = research._generate_queries(_CONTACT, _SENDER, bad_prompts)
    assert result == []


def test_returns_empty_list_on_call_claude_raise(mocker):
    mocker.patch.object(research, "_call_claude", side_effect=Exception("API error"))
    result = research._generate_queries(_CONTACT, _SENDER, {})
    assert result == []


def test_returns_empty_list_on_malformed_json(mocker):
    mocker.patch.object(research, "_call_claude", return_value="not json at all")
    result = research._generate_queries(_CONTACT, _SENDER, {})
    assert result == []


def test_returns_empty_list_when_json_is_dict(mocker):
    mocker.patch.object(research, "_call_claude", return_value=json.dumps({"key": "val"}))
    result = research._generate_queries(_CONTACT, _SENDER, {})
    assert result == []


def test_returns_empty_list_when_json_is_int(mocker):
    mocker.patch.object(research, "_call_claude", return_value="5")
    result = research._generate_queries(_CONTACT, _SENDER, {})
    assert result == []


def test_substitutes_unknown_for_none_fields(mocker):
    contact = {"name": "Test Person", "company": "Test Corp"}
    captured = {}

    def capture(prompt, **kwargs):
        captured["prompt"] = prompt
        return "[]"

    mocker.patch.object(research, "_call_claude", side_effect=capture)
    research._generate_queries(contact, _SENDER, {})

    assert "None" not in captured["prompt"]
    assert "unknown" in captured["prompt"]


def test_logs_research_q_marker_with_correct_count(mocker, caplog):
    mocker.patch.object(
        research, "_call_claude",
        return_value=json.dumps(["query one", "query two"]),
    )
    with caplog.at_level(logging.INFO, logger="research"):
        research._generate_queries(_CONTACT, _SENDER, {})

    messages = [r.message for r in caplog.records]
    assert any("[RESEARCH-Q]" in m for m in messages)
    assert any("queries=2" in m for m in messages)


def test_passes_sender_profile_as_system(mocker):
    captured = {}

    def capture(prompt, **kwargs):
        captured.update(kwargs)
        return "[]"

    mocker.patch.object(research, "_call_claude", side_effect=capture)
    research._generate_queries(_CONTACT, _SENDER, {})
    assert captured.get("system") == _SENDER
