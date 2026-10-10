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

_PATTERN = re.compile(
    r"(?<!\w)("
    + "|".join(re.escape(key) for key in sorted(_WORD_MAP, key=len, reverse=True))
    + r")(?!\w)",
    re.IGNORECASE,
)

_ZERO_WIDTH = frozenset("\u200b\u200c\u200d\ufeff\u2060")
_CONFUSABLES = str.maketrans({
    # Cyrillic characters commonly used as Latin lookalikes.
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "І": "I",
    "Ј": "J", "К": "K", "М": "M", "О": "O", "Р": "P", "Ѕ": "S",
    "Т": "T", "Х": "X", "У": "Y",
    "а": "a", "с": "c", "е": "e", "і": "i", "ј": "j", "о": "o",
    "р": "p", "ѕ": "s", "х": "x", "у": "y",
    # Greek characters commonly used as Latin lookalikes.
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I",
    "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T",
    "Υ": "Y", "Χ": "X", "Ϲ": "C",
    "α": "a", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p",
    "τ": "t", "υ": "y", "χ": "x", "ϲ": "c",
})

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


def strip_unicode_marks(text: str) -> str:
    """Remove invisible marks and normalize confusable typography."""
    normalized: list[str] = []
    for character in text:
        if character in _ZERO_WIDTH:
            continue

        codepoint = ord(character)
        if 0xFF01 <= codepoint <= 0xFF5E:
            character = chr(codepoint - 0xFEE0)
        elif character.isspace() and character not in "\r\n":
            character = " "

        normalized.append(character)

    return "".join(normalized).translate(_CONFUSABLES)


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
    parts: list[str] = []
    last = 0
    for match in _PATTERN.finditer(text):
        start, end = match.span()
        parts.append(text[last:start])
        if (
            _in_metric_context(text, start, end)
            or _looks_like_proper_noun(text, start, end)
        ):
            parts.append(match.group(0))
            last = end
            continue
        replacement = _match_case(_WORD_MAP[match.group(0).lower()], match.group(0))
        if replacement:
            parts.append(replacement)
        elif following := re.match(r"[ \t]*,", text[end:]):
            # Swallow a comma left dangling after the removed word ("passionate,").
            end += following.end()
        else:
            # Drop a comma left dangling before the removed word (", passionate").
            parts[-1] = re.sub(r",[ \t]*$", "", parts[-1])
        last = end
    parts.append(text[last:])
    return "".join(parts)


def _clean_removed_words(text: str) -> str:
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+([,.;:!?])", r"\1", text)
    text = re.sub(r"([(\[])[ \t]+", r"\1", text)
    text = re.sub(r"[ \t]+(?=\r?\n|$)", "", text)
    text = re.sub(r"(?m)^[ \t]+", "", text)
    return text


def dewatermark(text: str) -> str:
    """Return a conservative, deterministic stylistic cleanup of *text*."""
    result = strip_unicode_marks(text)
    original_facts = _numbers_and_dates(result)

    result = result.replace(" — ", ", ")
    result = result.replace("—", " - ")
    result = _replace_words(result)
    result = _clean_removed_words(result)

    if _numbers_and_dates(result) != original_facts:
        return text
    return result
