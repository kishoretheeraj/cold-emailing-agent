"""Code and link extraction from e-mail messages.

Pure functions only: no IMAP, no I/O. Everything here is deliberately strict —
a wrong OTP is worse than no OTP.
"""
from __future__ import annotations

import re
from email.message import EmailMessage, Message
from typing import Iterable
from urllib.parse import ParseResult, urlparse, urlsplit

from bs4 import BeautifulSoup

MAX_DISTINCT_CODES = 1  # any two distinct candidates in one message are a conflict

# Context is mandatory: a bare [0-9]{4,8} is never accepted.  Digits are ASCII only
# ([0-9], never \d, which also matches Arabic-Indic/fullwidth digits).
#
# Keyword chain: a "strong" code-ish word, optionally followed by more such words
# ("temporary access code", "one-time password").  Bare "code"/"pin" is accepted only
# when not preceded by a non-OTP qualifier (zip/promo/coupon/previous/...), so
# "zip code 90210" and "promo code 2024" never match.
_STRONG = (
    r"verification|security|login|sign-?in|one-time|otp|passcode|password|authorization|"
    r"authentication|temporary|2fa|mfa"
)
_CHAIN_WORDS = _STRONG + r"|access|confirmation|code|pin|token"
_NOT_QUALIFIERS = "".join(
    f"(?<!{q} )"
    for q in ("zip", "postal", "post", "promo", "coupon", "discount", "area", "country",
              "referral", "voucher", "tracking", "gift", "invite", "offer", "reward", "previous",
              "old", "expired", "last", "prior", "order", "product", "item", "source", "bin")
)
_KEYWORD = (
    rf"(?:(?:{_STRONG})(?:[\s-]+(?:{_CHAIN_WORDS}))*"
    rf"|{_NOT_QUALIFIERS}\b(?:code|pin))"
)
_SEP = r"[\s:.\-*_`\"'<>()\[\]]*"
_DIGITS = (
    r"(?P<digits>[0-9]{3}[ \-][0-9]{3}"            # 3-3
    r"|[0-9]{2}(?:[ \-][0-9]{2}){2,3}"             # 2-2-2, 2-2-2-2
    r"|[0-9]{4}[ \-][0-9]{4}"                      # 4-4
    r"|[0-9]{4,8})(?![0-9])"
    r"(?!\s*(?:minutes?|mins?|hours?|hrs?|days?|seconds?|secs?)\b)"
)
_CODE_RE = re.compile(rf"\b{_KEYWORD}\b(?:\s+is\b)?{_SEP}{_DIGITS}", re.IGNORECASE)
# "123456 is your verification code", "Use 482913 as your code", "G-123456 is your ..."
_REVERSE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Z]-)?(?P<digits>[0-9]{4,8})(?![0-9])\s+(?:is|as)\s+your\s+"
    r"(?:\w+\s+){0,3}?(?:code|otp|passcode|pin|verification)\b",
    re.IGNORECASE,
)
# Alphanumeric codes (Greenhouse style), at least one letter AND one digit, 6-10 chars.
_ALNUM_VALUE = (
    r"(?P<alnum>(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*[0-9])[A-Za-z0-9]{6,10})(?![A-Za-z0-9])"
)
_ALNUM_COLON_RE = re.compile(
    rf"\b(?:code|passcode|otp|token)\b[^.\n]{{0,80}}?:\s*[*_`\"'\[(<]*{_ALNUM_VALUE}", re.IGNORECASE
)
_ALNUM_IS_RE = re.compile(
    rf"\b(?:code|passcode|otp|token)\s+is\s*:?\s*[*_`\"'\[(<]*{_ALNUM_VALUE}", re.IGNORECASE
)
_SPACE_TRANS = {ord(c): " " for c in "\u00a0\u2007\u2009\u202f\u2002\u2003\u2004\u2005\u2006\u2008\u200a\u205f\u3000"}
_SPACE_TRANS.update({ord(c): None for c in "\u200b\u200c\u200d\u2060\ufeff"})
_SPLIT_DIGITS_RE = re.compile(r"(?<![0-9])[0-9](?:\s+[0-9](?![0-9]))+(?![0-9])")
_HIDDEN_STYLE_RE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?:px|pt|em|rem|%)?\s*(?:;|!|$)"
    r"|opacity\s*:\s*0(?:\.0+)?\s*(?:;|!|$)|max-height\s*:\s*0(?:px)?\s*(?:;|!|$)",
    re.IGNORECASE,
)
_CURRENCY_RE = re.compile(
    r"[$€£¥]\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP|CAD|AUD)\b",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}\b")
_PLAIN_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_LINK_KEYWORD_RE = re.compile(r"verify|confirm|activate|token", re.IGNORECASE)
_BLOCK_TAGS = ("br", "p", "div", "tr", "td", "th", "li", "h1", "h2", "h3", "h4", "h5", "h6")


class AmbiguousCode(Exception):
    """More than MAX_DISTINCT_CODES distinct candidates in one message."""


# --------------------------------------------------------------------------
# Message bodies
# --------------------------------------------------------------------------
def _is_hidden(tag) -> bool:
    """Invisible-to-the-reader elements: an attacker-controlled mail can plant decoy codes there."""
    attrs = tag.attrs or {}
    if "hidden" in attrs:
        return True
    if _HIDDEN_STYLE_RE.search(str(attrs.get("style", ""))):
        return True
    classes = attrs.get("class", [])
    classes = classes.split() if isinstance(classes, str) else classes
    return any("preheader" in c.lower() for c in classes)


def html_to_text(html: str) -> str:
    """Strip tags and rejoin text, so ``4<b>8</b>3920`` becomes ``483920``.

    Inline tags are removed with no separator; block-level tags become newlines
    so adjacent cells/paragraphs do not fuse into one digit run.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head", "title"]):
        tag.decompose()
    for tag in [t for t in soup.find_all(True) if _is_hidden(t)]:
        if not tag.decomposed:
            tag.decompose()
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    return soup.get_text("")


def _part_text(part: Message) -> str | None:
    try:
        if isinstance(part, EmailMessage):
            return part.get_content()
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            return payload.decode(part.get_content_charset() or "utf-8", "replace")
    except (LookupError, UnicodeError, KeyError):
        return None
    return None


def body_parts(msg: Message) -> tuple[list[str], list[str]]:
    """Return ``(plain_texts, html_sources)`` for all non-attachment text parts."""
    plain: list[str] = []
    html: list[str] = []
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        text = _part_text(part)
        if text:
            (plain if ctype == "text/plain" else html).append(text)
    return plain, html


def message_texts(msg: Message) -> list[str]:
    """All body texts as plain text, text/plain parts first, HTML stripped."""
    plain, html = body_parts(msg)
    return plain + [html_to_text(h) for h in html]


# --------------------------------------------------------------------------
# Codes
# --------------------------------------------------------------------------
def _clean(text: str) -> str:
    text = _CURRENCY_RE.sub(" ", text)
    return _PHONE_RE.sub(" ", text)


def _normalize(text: str) -> str:
    return text.translate(_SPACE_TRANS)


def _numeric_codes(text: str) -> list[str]:
    out: list[str] = []
    cleaned = _clean(text)
    for rx in (_CODE_RE, _REVERSE_RE):
        for m in rx.finditer(cleaned):
            code = re.sub(r"[ \-]", "", m.group("digits"))
            if 4 <= len(code) <= 8 and code not in out:
                out.append(code)
    return out


def _alnum_codes(text: str, hint_allows_is: bool) -> list[str]:
    out: list[str] = []
    for rx in (_ALNUM_COLON_RE, _ALNUM_IS_RE) if hint_allows_is else (_ALNUM_COLON_RE,):
        for m in rx.finditer(text):
            if m.group("alnum") not in out:
                out.append(m.group("alnum"))
    return out


def find_codes(texts: Iterable[str], hint: str | None = None) -> list[str]:
    """Distinct candidate codes in order of first appearance across ``texts``.

    Numeric codes first.  If none are found, retry with digit-per-cell runs joined
    ("4 8 3 9 2 0"), then alphanumeric codes (also tried first when ``hint`` mentions
    "alphanumeric").
    """
    texts = [_normalize(t) for t in texts]
    alnum_first = bool(hint and "alphanumeric" in hint.lower())

    def collect(variants: list[str]) -> list[str]:
        found: list[str] = []
        for t in variants:
            for c in _numeric_codes(t):
                if c not in found:
                    found.append(c)
        return found

    def collect_alnum(variants: list[str]) -> list[str]:
        found: list[str] = []
        for t in variants:
            for c in _alnum_codes(t, alnum_first):
                if c not in found:
                    found.append(c)
        return found

    if alnum_first:
        found = collect_alnum(texts)
        if found:
            return found
    found = collect(texts)
    if found:
        return found
    found = collect([_SPLIT_DIGITS_RE.sub(lambda m: re.sub(r"\s+", "", m.group(0)), t) for t in texts])
    if found:
        return found
    return [] if alnum_first else collect_alnum(texts)


def extract_code(texts: Iterable[str], hint: str | None = None) -> str | None:
    """The code in the message, ``None`` if none, ``AmbiguousCode`` if >1 distinct.

    Texts are all bodies of one message (plain first, then HTML); alternatives that
    disagree therefore also count as ambiguous.
    """
    codes = find_codes(texts, hint)
    if len(codes) > MAX_DISTINCT_CODES:
        raise AmbiguousCode(f"{len(codes)} distinct candidates")
    return codes[0] if codes else None


# --------------------------------------------------------------------------
# Domains and links
# --------------------------------------------------------------------------
def _norm_domain(domain: str) -> str:
    return domain.strip().lower().lstrip("*.").rstrip(".")


def domain_matches(host: str | None, allowed: Iterable[str]) -> bool:
    """True if ``host`` is an allowed domain or a subdomain of one.

    Compared on label boundaries (``evil-example.com`` and
    ``example.com.evil.io`` do not match ``example.com``); never a substring test.
    """
    if not host:
        return False
    host = host.lower().rstrip(".")
    for a in map(_norm_domain, allowed):
        if a and (host == a or host.endswith("." + a)):
            return True
    return False


def address_domain(address: str) -> str | None:
    """Domain part of a bare address like ``noreply@mail.example.com``."""
    _, sep, domain = address.rpartition("@")
    return domain.strip().lower() if sep else None


def _candidate_urls(plain: Iterable[str], html: Iterable[str]) -> list[str]:
    urls: list[str] = []
    for source in html:
        for a in BeautifulSoup(source, "html.parser").find_all("a", href=True):
            urls.append(str(a["href"]).strip())
    for text in plain:
        urls.extend(u.rstrip(".,;:!?") for u in _PLAIN_URL_RE.findall(text))
    return urls


_BAD_URL_CHARS_RE = re.compile(r"[\x00-\x20\x7f\\]")
_NOISE_LINK_RE = re.compile(
    r"unsubscribe|opt-?out|privacy|help|support|legal|terms|polic(?:y|ies)|preferences|contact|"
    r"about|faq|manage|view[-_]?in[-_]?browser|logo|\.(?:png|jpe?g|gif|svg|css|ico)(?:$|\?)",
    re.IGNORECASE,
)


def _safe_https_host(url: str, allowed: list[str]) -> "ParseResult | None":
    """Parsed URL if it is plain https on an allowlisted host and default port, else ``None``.

    Rejects backslashes, control characters and whitespace before parsing: parsers
    disagree on them (``https://evil.com\\@good.com/`` is host good.com to urlparse but
    evil.com to a browser).
    """
    if _BAD_URL_CHARS_RE.search(url):
        return None
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        alt_host = urlsplit(url.replace("\\", "/")).hostname
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() != "https" or host != alt_host or port not in (None, 443):
        return None
    return parsed if domain_matches(host, allowed) else None


def extract_link(msg: Message, allowed_hosts: Iterable[str]) -> str | None:
    """Best verification link whose host is on the allowlist, else ``None``.

    Only https URLs (default port) with an allowlisted hostname qualify.  Among those,
    the shortest path containing verify|confirm|activate|token (path or query) wins.
    If none has such a keyword, a link is returned only when exactly one
    non-noise (not unsubscribe/privacy/help/legal/...) candidate exists, for opaque
    magic links like ``/l/Xk29s``.  The URL is returned, never fetched.
    """
    allowed = list(allowed_hosts)
    plain, html = body_parts(msg)
    best: tuple[int, int, str] | None = None
    opaque: list[str] = []
    for order, url in enumerate(dict.fromkeys(_candidate_urls(plain, html))):
        parsed = _safe_https_host(url, allowed)
        if parsed is None:
            continue
        if not _LINK_KEYWORD_RE.search(parsed.path) and not _LINK_KEYWORD_RE.search(parsed.query):
            if parsed.path not in ("", "/") and not _NOISE_LINK_RE.search(url):
                opaque.append(url)
            continue
        key = (len(parsed.path), order, url)
        if best is None or key < best:
            best = key
    if best:
        return best[2]
    return opaque[0] if len(opaque) == 1 else None
