from collections import Counter
import re

import pytest

from dewatermark import dewatermark


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Spearheaded migration", "Led migration"),
        ("leveraged tools", "used tools"),
        ("utilize tools", "use tools"),
        ("utilized tools", "used tools"),
        ("utilizing tools", "using tools"),
        ("orchestrated delivery", "ran delivery"),
        ("robust service", "reliable service"),
        ("seamless rollout", "smooth rollout"),
        ("cutting-edge system", "modern system"),
        ("proven track record", "strong record"),
        ("detail-oriented engineer", "thorough engineer"),
        ("fast-paced environment", "fast-moving team"),
        ("thought leader", "leader"),
        ("game-changer", "major improvement"),
        ("disrupt markets", "change markets"),
        ("disruptive product", "major product"),
        ("a ninja engineer", "a engineer"),
        ("a guru engineer", "a engineer"),
        ("a rockstar engineer", "a engineer"),
        ("synergy across teams", "working together across teams"),
        ("synergies across teams", "working together across teams"),
        ("a passionate engineer", "a engineer"),
        ("a results-driven engineer", "a engineer"),
    ],
)
def test_each_word_map_entry(source, expected):
    assert dewatermark(source) == expected


def test_whole_words_only():
    assert dewatermark("leveragedness and disruption") == (
        "leveragedness and disruption"
    )


def test_spaced_em_dash_becomes_comma():
    assert dewatermark("Built it — shipped it.") == "Built it, shipped it."


def test_unspaced_em_dash_becomes_hyphen():
    assert dewatermark("Built it—shipped it.") == "Built it - shipped it."


def test_no_em_dash_remains():
    assert "—" not in dewatermark("One — two—three")


def test_numbers_dates_percentages_and_money_are_unchanged():
    source = (
        "Led work from 2022-2024, improving revenue 35% from "
        "$1.2M to $2,500,000 on 10/09/2026."
    )
    result = dewatermark(source)
    facts = re.compile(
        r"\$[\d,.]+[kKmMbB]?|\d{1,4}[/-]\d{1,2}(?:[/-]\d{1,4})?"
        r"|\d{4}-\d{2,4}|\d[\d,.]*%|\d[\d,.]*[kKmMbB]?"
    )
    assert Counter(facts.findall(result)) == Counter(facts.findall(source))


def test_mapped_word_in_metric_context_is_skipped():
    assert dewatermark("leveraged 35% growth") == "leveraged 35% growth"
    assert dewatermark("$2M robust") == "$2M robust"


def test_proper_noun_is_skipped():
    assert dewatermark("Built the Robust Systems platform") == (
        "Built the Robust Systems platform"
    )


def test_headline_is_processed():
    assert dewatermark("Results-Driven Engineering Leader") == (
        "Engineering Leader"
    )


def test_triple_parallelism_is_untouched():
    source = "Built systems, trained teams, and improved operations."
    assert dewatermark(source) == source


def test_idempotent():
    source = "Passionate leader — leveraged robust, seamless pipelines."
    once = dewatermark(source)
    assert dewatermark(once) == once


def test_full_resume_smoke():
    source = """
# Engineering Leader

## Experience

- Spearheaded the rebuild — leveraged robust pipelines to deliver cutting-edge results.
- Increased revenue 35% from $1.2M to $2M during 2022-2024.
""".strip()

    result = dewatermark(source)
    banned = (
        "spearheaded",
        "leveraged",
        "robust",
        "cutting-edge",
    )

    assert "—" not in result
    assert not any(re.search(rf"\b{re.escape(word)}\b", result, re.I) for word in banned)
    assert (
        "- Led the rebuild, used reliable pipelines to deliver modern results."
        in result
    )
    assert "35%" in result
    assert "$1.2M" in result
    assert "$2M" in result
    assert "2022-2024" in result
