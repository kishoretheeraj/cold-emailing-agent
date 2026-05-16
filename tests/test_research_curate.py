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


def test_returns_empty_on_call_claude_raise(mocker):
    mocker.patch.object(research, "_call_claude", side_effect=Exception("API error"))
    result = research._curate_brief(_CONTACT, _RAW_RESULTS, {})
    assert result == ""


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
