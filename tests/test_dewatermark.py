"""Tests for deterministic resume-text dewatermarking."""

from __future__ import annotations

import re

import pytest

import dewatermark as module
from dewatermark import dewatermark, strip_unicode_marks


NEW_WORD_SWAPS = {
    "additionally": "also",
    "furthermore": "also",
    "moreover": "also",
    "approximately": "about",
    "demonstrate": "show",
    "demonstrated": "showed",
    "extensive": "wide",
    "facilitate": "help",
    "facilitated": "helped",
    "facilitates": "helps",
    "facilitating": "helping",
    "obtain": "get",
    "obtained": "got",
    "obtains": "gets",
    "obtaining": "getting",
    "regarding": "about",
    "commence": "start",
    "commenced": "started",
    "commences": "starts",
    "commencing": "starting",
    "enhance": "improve",
    "enhanced": "improved",
    "enhances": "improves",
    "enhancing": "improving",
}


def test_original_smoke_case() -> None:
    text = (
        "Spearheaded a cutting-edge program — leveraged robust systems "
        "in a fast-paced environment."
    )

    assert dewatermark(text) == (
        "Led a modern program, used reliable systems in a fast-moving team."
    )


def test_removes_zero_width_characters() -> None:
    text = "Led\u200b cross\u200cfunctional\u200d work\ufeff with\u2060 teams."
    assert strip_unicode_marks(text) == "Led crossfunctional work with teams."


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("one\u00a0two", "one two"),
        ("one\u2000two", "one two"),
        ("one\u2005two", "one two"),
        ("one\u200atwo", "one two"),
        ("one\u202ftwo", "one two"),
        ("one\u3000two", "one two"),
    ],
)
def test_normalizes_unicode_whitespace(source: str, expected: str) -> None:
    assert strip_unicode_marks(source) == expected


def test_maps_fullwidth_ascii_to_ascii() -> None:
    assert strip_unicode_marks("ＡＢＣ１２３！") == "ABC123!"


def test_maps_cyrillic_and_greek_lookalikes_to_latin() -> None:
    assert strip_unicode_marks("раураl ΑΒΕ Οffісе") == "paypal ABE Office"


@pytest.mark.parametrize(("source", "expected"), NEW_WORD_SWAPS.items())
def test_each_new_word_swap(source: str, expected: str) -> None:
    assert dewatermark(f"We {source} outcomes.") == f"We {expected} outcomes."


ORIGINAL_WORD_SWAPS = {
    "spearheaded": "led",
    "leveraged": "used",
    "utilize": "use",
    "utilized": "used",
    "utilizing": "using",
    "orchestrated": "ran",
    "robust": "reliable",
    "seamless": "smooth",
    "cutting-edge": "modern",
    "proven track record": "strong record",
    "detail-oriented": "thorough",
    "fast-paced environment": "fast-moving team",
    "thought leader": "leader",
    "game-changer": "major improvement",
    "disrupt": "change",
    "disruptive": "major",
    "synergy": "working together",
    "synergies": "working together",
}

ORIGINAL_WORD_REMOVALS = ["ninja", "guru", "rockstar", "passionate", "results-driven"]


@pytest.mark.parametrize(("source", "expected"), ORIGINAL_WORD_SWAPS.items())
def test_each_original_word_swap(source: str, expected: str) -> None:
    assert dewatermark(f"We {source} outcomes.") == f"We {expected} outcomes."


@pytest.mark.parametrize("word", ORIGINAL_WORD_REMOVALS)
def test_each_original_word_removal(word: str) -> None:
    assert word not in dewatermark(f"We are {word} about outcomes.").lower()


def test_new_swaps_preserve_initial_capitalization() -> None:
    assert dewatermark("Furthermore, we enhanced delivery.") == (
        "Also, we improved delivery."
    )


def test_twenty_word_bullet_does_not_exceed_cap() -> None:
    text = (
        "- Additionally demonstrated extensive skills to facilitate teams and "
        "obtain approximately 25% growth regarding earlier goals across many "
        "projects successfully today"
    )
    before = re.findall(r"\b[\w%]+\b", text)
    result = dewatermark(text)
    after = re.findall(r"\b[\w%]+\b", result)

    assert len(before) == 20
    assert len(after) <= 20


def test_preserves_numbers_dates_and_currency() -> None:
    text = (
        "Additionally enhanced revenue from $1.2M to $1.5M by 25% "
        "during 2024-2025, after the 06/15/2024 launch."
    )
    result = dewatermark(text)

    for fact in ("$1.2M", "$1.5M", "25%", "2024-2025", "06/15/2024"):
        assert fact in result


def test_returns_original_input_if_fact_guard_detects_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "Enhanced revenue by 25%."
    monkeypatch.setattr(
        module,
        "_replace_words",
        lambda value: value.replace("25%", "26%"),
    )

    assert dewatermark(text) == text


def test_protects_mapped_language_in_metric_context() -> None:
    assert dewatermark("Achieved robust 25% growth.") == (
        "Achieved robust 25% growth."
    )


def test_protects_title_case_term_inside_proper_name() -> None:
    assert dewatermark("Joined Acme Robust Systems.") == (
        "Joined Acme Robust Systems."
    )


def test_cleans_spacing_after_removed_words() -> None:
    assert dewatermark("A passionate, results-driven leader.") == "A leader."


def test_handles_em_dashes() -> None:
    assert dewatermark("Led delivery — improved quality—reduced cost.") == (
        "Led delivery, improved quality - reduced cost."
    )


@pytest.mark.parametrize(
    "text",
    [
        "Additionally enhanced a robust process — obtaining about 25% growth.",
        "A passionate, results-driven leader.",
        "Ａdditionally\u00a0demonstrated extensive experience.",
        "Joined Acme Robust Systems in 2024-2025.",
    ],
)
def test_is_idempotent(text: str) -> None:
    once = dewatermark(text)
    assert dewatermark(once) == once
