"""Tests for research._curate_brief."""

import pytest

import research


_CONTACT = {
    "name": "Jane Doe",
    "company": "Acme Corp",
    "role": "VP Engineering",
    "detail": "Leads 50-person engineering org",
}

_RAW_RESULTS = [
    {
        "query": "Jane Doe Acme Corp interview 2026",
        "result": {
            "answer": "Jane Doe spoke at SaaStr 2026",
            "results": [
                {
                    "title": "Jane Doe on scaling teams",
                    "content": "In her SaaStr talk, Jane Doe discussed...",
                    "url": "https://saastr.com/jane-doe-talk",
                }
            ],
        },
    }
]


def test_returns_curated_brief(mocker):
    mocker.patch.object(research, "_call_claude", return_value="Person:\n- Spoke at SaaStr 2026 (saastr.com)")
    result = research._curate_brief(_CONTACT, _RAW_RESULTS, {})
    assert result == "Person:\n- Spoke at SaaStr 2026 (saastr.com)"


def test_returns_empty_when_no_raw_results(mocker):
    mock_claude = mocker.patch.object(research, "_call_claude")
    result = research._curate_brief(_CONTACT, [], {})
    assert result == ""
    mock_claude.assert_not_called()


def test_returns_empty_on_no_reliable_brief(mocker):
    mocker.patch.object(research, "_call_claude", return_value="NO_RELIABLE_BRIEF")
    result = research._curate_brief(_CONTACT, _RAW_RESULTS, {})
    assert result == ""


def test_returns_empty_on_no_reliable_brief_with_whitespace(mocker):
    mocker.patch.object(research, "_call_claude", return_value="  NO_RELIABLE_BRIEF  ")
    result = research._curate_brief(_CONTACT, _RAW_RESULTS, {})
    assert result == ""


def test_truncates_formatted_results_to_6000_chars(mocker):
    mock_claude = mocker.patch.object(research, "_call_claude", return_value="Person:\n- A fact")
    big_results = [
        {
            "query": f"query {i}",
            "result": {
                "answer": "a" * 400,
                "results": [{"title": "T", "content": "c" * 300, "url": "https://x.com/p"}],
            },
        }
        for i in range(20)
    ]
    research._curate_brief(_CONTACT, big_results, {})
    call_args = mock_claude.call_args
    prompt = call_args.args[0]
    assert "raw_results" not in prompt or len(prompt) < 15000


def test_returns_empty_on_stray_brace_format_error():
    """Regression: a stray '{}' in a live-edited research_curate_prompt raises
    IndexError (not KeyError) during .format() -- must still degrade to ""
    rather than propagate past the intended 'log + return empty' path."""
    bad_prompts = {"research_curate_prompt": "{name} at {company}: {raw_results} {}"}
    result = research._curate_brief(_CONTACT, _RAW_RESULTS, bad_prompts)
    assert result == ""


def test_returns_empty_on_call_claude_raise(mocker):
    mocker.patch.object(research, "_call_claude", side_effect=Exception("API error"))
    result = research._curate_brief(_CONTACT, _RAW_RESULTS, {})
    assert result == ""


def test_raw_content_included_in_curator_prompt(mocker):
    captured = {}

    def capture(prompt, **kwargs):
        captured["prompt"] = prompt
        return "Person:\n- A fact"

    mocker.patch.object(research, "_call_claude", side_effect=capture)
    raw_results = [
        {
            "query": "Jane Doe Acme Corp",
            "result": {
                "answer": "brief answer",
                "results": [
                    {
                        "title": "Jane Doe profile",
                        "content": "short snippet",
                        "url": "https://example.com/jane",
                        "raw_content": "This is the full page text about Jane Doe.",
                    }
                ],
            },
        }
    ]
    research._curate_brief(_CONTACT, raw_results, {})
    assert "This is the full page text about Jane Doe." in captured["prompt"]


def test_all_results_per_query_included_not_just_last(mocker):
    """Regression: every result in a query's results list must be formatted
    into the prompt, not just the last one."""
    captured = {}

    def capture(prompt, **kwargs):
        captured["prompt"] = prompt
        return "Person:\n- A fact"

    mocker.patch.object(research, "_call_claude", side_effect=capture)
    raw_results = [
        {
            "query": "Jane Doe Acme Corp",
            "result": {
                "answer": "brief answer",
                "results": [
                    {"title": "First hit", "content": "first content", "url": "https://a.com/1"},
                    {"title": "Second hit", "content": "second content", "url": "https://b.com/2"},
                ],
            },
        }
    ]
    research._curate_brief(_CONTACT, raw_results, {})
    prompt = captured["prompt"]
    assert "First hit" in prompt
    assert "Second hit" in prompt


def test_empty_results_for_first_query_does_not_raise(mocker):
    """Regression: a query with zero results must not raise NameError from
    referencing an unset variable, and must not leak values from other queries."""
    captured = {}

    def capture(prompt, **kwargs):
        captured["prompt"] = prompt
        return "Person:\n- A fact"

    mocker.patch.object(research, "_call_claude", side_effect=capture)
    raw_results = [
        {"query": "no hits query", "result": {"answer": "none", "results": []}},
        {
            "query": "Jane Doe Acme Corp",
            "result": {
                "answer": "brief answer",
                "results": [{"title": "Real hit", "content": "content", "url": "https://a.com/1"}],
            },
        },
    ]
    result = research._curate_brief(_CONTACT, raw_results, {})
    assert result == "Person:\n- A fact"
    assert "Real hit" in captured["prompt"]


def test_prompt_contains_contact_fields_for_disambiguation(mocker):
    captured = {}

    def capture(prompt, **kwargs):
        captured["prompt"] = prompt
        return "NO_RELIABLE_BRIEF"

    mocker.patch.object(research, "_call_claude", side_effect=capture)
    research._curate_brief(_CONTACT, _RAW_RESULTS, {})

    prompt = captured["prompt"]
    assert "Jane Doe" in prompt
    assert "Acme Corp" in prompt
    assert "VP Engineering" in prompt
    assert "Leads 50-person engineering org" in prompt
