import re

from rapidfuzz import fuzz, process

# ── Normalization ───────────────────────────────────────────────────────────────

_LEGAL_SUFFIXES = [
    "incorporated", "inc", "llc", "l.l.c", "corporation", "corp",
    "company", "co", "limited", "ltd", "llp", "l.l.p", "lp", "l.p",
    "plc", "pc", "pa", "na",
]

# Sorted longest-first so "l.l.c" doesn't get shadowed by a shorter partial match.
_LEGAL_SUFFIXES.sort(key=len, reverse=True)

_NOISE_WORDS = {"the", "and"}

_PUNCTUATION_RE = re.compile(r"[.,'’]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize(raw_name):
    if not raw_name:
        return ""

    name = raw_name.strip().lower()
    name = name.replace("&", " and ")
    name = _PUNCTUATION_RE.sub("", name)
    name = _WHITESPACE_RE.sub(" ", name).strip()

    tokens = name.split(" ")

    # Strip legal suffixes iteratively from the end (handles compound
    # suffixes like "... Inc USA" -> "... USA" -> still stripping noise).
    changed = True
    while changed and tokens:
        changed = False
        last = tokens[-1]
        if last in _LEGAL_SUFFIXES:
            tokens.pop()
            changed = True

    # Strip leading/trailing noise words ("the", "and") but never collapse
    # a name down to nothing and never strip from the middle (order matters
    # for names like "Johnson and Johnson").
    while tokens and tokens[0] in _NOISE_WORDS:
        tokens.pop(0)
    while tokens and tokens[-1] in _NOISE_WORDS:
        tokens.pop()

    return " ".join(tokens)


# ── Matching ─────────────────────────────────────────────────────────────────────

AUTO_THRESHOLD = 93
REVIEW_FLOOR = 80

# Small, curated map of known multi-legal-entity employer families. Stage 1
# does not attempt to auto-consolidate distinct legal entities algorithmically
# (e.g. "Amazon.com Services LLC" vs "Amazon Web Services Inc") -- this dict
# is the pragmatic v1 answer, expandable by editing it, not by re-architecting.
# Keys and values are already-normalized names; every value in a group
# consolidates onto its key during ingestion aggregation.
KNOWN_ALIAS_GROUPS = {
    "amazon": [
        "amazon com services",
        "amazon web services",
        "amazon data services",
    ],
}


def canonicalize_alias_group(normalized_name):
    for canonical, members in KNOWN_ALIAS_GROUPS.items():
        if normalized_name == canonical or normalized_name in members:
            return canonical
    return normalized_name


class MatchCandidate:
    def __init__(self, normalized_name, score):
        self.normalized_name = normalized_name
        self.score = score

    def __repr__(self):
        return f"MatchCandidate({self.normalized_name!r}, {self.score!r})"

    def __eq__(self, other):
        if not isinstance(other, MatchCandidate):
            return NotImplemented
        return self.normalized_name == other.normalized_name and self.score == other.score


def resolve(normalized_query, corpus, limit=5):
    if not normalized_query or not corpus:
        return []

    matches = process.extract(
        normalized_query,
        corpus,
        scorer=fuzz.token_set_ratio,
        limit=limit,
        score_cutoff=REVIEW_FLOOR,
    )
    return [MatchCandidate(name, score) for name, score, _ in matches]


def classify(normalized_query, candidates):
    if not candidates:
        return "unknown", None

    top = candidates[0]

    # An exact string match after normalization carries no token_set_ratio
    # ambiguity -- it's the same identity, not a fuzzy subset match -- so it
    # always auto-classifies regardless of token count.
    if normalized_query == top.normalized_name:
        return "auto", top

    is_single_token = " " not in normalized_query.strip()

    # token_set_ratio is known to false-positive on short/single-token
    # queries against unrelated longer names that happen to share a token,
    # and (more broadly) on any query whose tokens are a strict subset of a
    # longer corpus name's tokens (it scores subset matches near 100
    # regardless of how much extra text the corpus name has). The
    # single-token guard below catches the worst case; residual subset-match
    # risk for multi-token queries is expected to surface as needs_review
    # volume during post-launch threshold calibration, not solved here.
    if top.score >= AUTO_THRESHOLD and not is_single_token:
        return "auto", top

    return "needs_review", top
