import pytest

import entity_resolution as er


# ── normalize() ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw_a,raw_b", [
    ("Amazon.com Services, LLC", "Amazon.com Services LLC"),
    ("Google LLC", "Google, LLC"),
    ("Acme Inc.", "ACME INC"),
    ("Acme, Inc.", "acme inc"),
])
def test_normalize_suffix_and_punctuation_variants_collapse(raw_a, raw_b):
    assert er.normalize(raw_a) == er.normalize(raw_b)


@pytest.mark.parametrize("raw,must_contain", [
    ("3M Company", "3m"),
    ("H&M Hennes & Mauritz", "h and m"),
    ("AT&T Inc.", "at and t"),
])
def test_normalize_preserves_short_alphanumeric_tokens(raw, must_contain):
    assert must_contain in er.normalize(raw)


def test_normalize_strips_leading_and_trailing_noise_words():
    assert er.normalize("The Home Depot") == "home depot"


def test_normalize_does_not_strip_noise_word_from_middle():
    # "Johnson and Johnson" must not collapse to "johnson johnson"
    assert er.normalize("Johnson and Johnson") == "johnson and johnson"


def test_normalize_empty_string_is_safe():
    assert er.normalize("") == ""
    assert er.normalize(None) == ""


def test_normalize_strips_compound_suffix():
    assert er.normalize("Foo Bar Corp Inc") == "foo bar"


def test_normalize_period_does_not_fuse_adjacent_tokens():
    """Regression: punctuation must be replaced with a space, not deleted --
    "Amazon.com Services LLC" must normalize to "amazon com services" (with
    a space) to match the "amazon" KNOWN_ALIAS_GROUPS entry, not fuse into
    the unreachable "amazoncom services"."""
    assert er.normalize("Amazon.com Services, LLC") == "amazon com services"
    assert er.canonicalize_alias_group(er.normalize("Amazon.com Services, LLC")) == "amazon"


# ── resolve() / classify() ──────────────────────────────────────────────────────

def test_resolve_empty_query_or_corpus_returns_no_candidates():
    assert er.resolve("", ["acme"]) == []
    assert er.resolve("acme", []) == []


def test_resolve_returns_candidates_above_review_floor():
    corpus = ["amazon com services", "microsoft corporation", "totally unrelated widgets"]
    candidates = er.resolve("amazon com services", corpus)
    assert candidates
    assert candidates[0].normalized_name == "amazon com services"
    assert candidates[0].score >= er.AUTO_THRESHOLD


def test_classify_no_candidates_is_unknown_never_a_false_negative():
    # Governance-critical: a query with zero corpus entries above REVIEW_FLOOR
    # must resolve to "unknown", never fabricate a "doesn't sponsor" result.
    status, top = er.classify("some obscure startup", [])
    assert status == "unknown"
    assert top is None


def test_classify_multi_token_high_score_is_auto():
    corpus = ["stripe data services"]
    candidates = er.resolve("stripe data services", corpus)
    status, top = er.classify("stripe data services", candidates)
    assert status == "auto"
    assert top.normalized_name == "stripe data services"


def test_classify_exact_single_token_match_is_auto():
    # An identical string after normalization is unambiguous -- unlike a
    # fuzzy subset match, it should not be blocked by the single-token guard.
    corpus = ["acme"]
    candidates = er.resolve("acme", corpus)
    status, top = er.classify("acme", candidates)
    assert status == "auto"


def test_classify_single_token_query_never_reaches_auto_band():
    # token_set_ratio false-positives on short/single-token queries against
    # unrelated longer names sharing a token. A single-token query must
    # always land in needs_review, never auto, regardless of score.
    corpus = ["apple hospitality reit"]
    candidates = er.resolve("apple", corpus)
    assert candidates and candidates[0].score >= er.AUTO_THRESHOLD
    status, top = er.classify("apple", candidates)
    assert status == "needs_review"


# ── alias consolidation ──────────────────────────────────────────────────────

def test_canonicalize_alias_group_maps_known_family_members():
    assert er.canonicalize_alias_group("amazon web services") == "amazon"
    assert er.canonicalize_alias_group("amazon data services") == "amazon"
    assert er.canonicalize_alias_group("amazon") == "amazon"


def test_canonicalize_alias_group_passes_through_unknown_names():
    assert er.canonicalize_alias_group("totally unrelated widgets") == "totally unrelated widgets"


def test_classify_mid_band_score_is_needs_review():
    corpus = ["acme industries"]
    candidates = er.resolve("acme indutsries co", corpus)
    status, top = er.classify("acme indutsries co", candidates)
    assert status == "needs_review"
    assert er.REVIEW_FLOOR <= top.score < er.AUTO_THRESHOLD
