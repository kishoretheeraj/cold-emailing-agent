"""Deterministically remove specified AI-stylistic tells from resume text."""

from __future__ import annotations

from collections import Counter
import re


_WORD_MAP = {
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
    "ninja": "",
    "guru": "",
    "rockstar": "",
    "synergy": "working together",
    "synergies": "working together",
    "passionate": "",
    "results-driven": "",
}

_PATTERN = re.compile(
    r"(?<!\w)("
    + "|".join(re.escape(key) for key in sorted(_WORD_MAP, key=len, reverse=True))
    + r")(?!\w)",
    re.IGNORECASE,
)

_METRIC = r"(?:[$£€]\s*)?\d[\d,]*(?:\.\d+)?(?:\s*%|\s*[kKmMbB])?"
_NUMBER_OR_DATE = re.compile(
    r"""
    (?<!\w)
    (?:
        [$£€]\s*\d[\d,]*(?:\.\d+)?(?:[kKmMbB])?
        |\d{1,4}[/-]\d{1,2}(?:[/-]\d{1,4})?
        |\d{4}\s*[-–]\s*\d{2,4}
        |\d[\d,]*(?:\.\d+)?%
        |\d[\d,]*(?:\.\d+)?(?:[kKmMbB])?
    )
    (?!\w)
    """,
    re.VERBOSE,
)


def _numbers_and_dates(text: str) -> Counter[str]:
    return Counter(match.group(0) for match in _NUMBER_OR_DATE.finditer(text))


def _in_metric_context(text: str, start: int, end: int) -> bool:
    """Protect mapped language immediately attached to a numeric metric."""
    left = text[max(0, start - 40):start]
    right = text[end:min(len(text), end + 40)]
    return bool(
        re.search(rf"{_METRIC}\s*(?:[:/(\[]\s*)?$", left)
        or re.match(rf"^\s*(?:[:/)\]]\s*)?{_METRIC}", right)
    )


def _looks_like_proper_noun(text: str, start: int, end: int) -> bool:
    """Conservatively protect title-cased terms embedded in names."""
    value = text[start:end]
    if not value[:1].isupper():
        return False

    prefix = text[:start].rstrip()
    at_sentence_start = (
        not prefix
        or prefix.endswith((".", "!", "?", "\n", ":"))
        or bool(re.search(r"(?:^|\n)\s*[-*+]\s*$", text[:start]))
    )
    if at_sentence_start:
        return False

    following = text[end:]
    next_word = re.match(r"[\s\-]+([A-Z][A-Za-z0-9&']*)", following)
    previous_word = re.search(r"([A-Z][A-Za-z0-9&']*)[\s\-]+$", text[:start])
    return bool(next_word or previous_word)


def _match_case(replacement: str, original: str) -> str:
    if replacement and original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _replace_words(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        start, end = match.span()
        if (
            _in_metric_context(text, start, end)
            or _looks_like_proper_noun(text, start, end)
        ):
            return match.group(0)
        replacement = _WORD_MAP[match.group(0).lower()]
        return _match_case(replacement, match.group(0))

    return _PATTERN.sub(replace, text)


def _clean_removed_words(text: str) -> str:
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+([,.;:!?])", r"\1", text)
    text = re.sub(r"([(\[])[ \t]+", r"\1", text)
    text = re.sub(r"[ \t]+(?=\r?\n|$)", "", text)
    text = re.sub(r"(?m)^[ \t]+", "", text)
    return text


def dewatermark(text: str) -> str:
    """Return a conservative, deterministic stylistic cleanup of *text*."""
    original_facts = _numbers_and_dates(text)

    result = text.replace(" — ", ", ")
    result = result.replace("—", " - ")
    result = _replace_words(result)
    result = _clean_removed_words(result)

    if _numbers_and_dates(result) != original_facts:
        return text
    return result
