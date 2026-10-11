"""Deterministic bullet ranking against extracted requirements. No LLM.

Backends: sentence-transformers embeddings (cosine) when importable, otherwise a pure-Python TF-IDF cosine.
Ties break on original bullet order, so the same input always yields the same output.
"""

import math
import re
from collections import Counter

TOP_K = 6
_STOP = frozenset(
    "a an and are as at be by for from in is it of on or that the to with we you your our will can".split())
_model = None


# ── Text ───────────────────────────────────────────────────────────────────────

def _tokens(text: str) -> list[str]:
    toks = (t.rstrip(".") for t in re.findall(r"[a-z0-9][a-z0-9+#.]*", text.lower()))
    return [t for t in toks if t and t not in _STOP]


def _query_text(requirements: dict) -> str:
    must = list(requirements.get("must_have") or [])
    parts = must + must + list(requirements.get("nice_to_have") or []) + list(requirements.get("keywords") or [])
    parts.append(str(requirements.get("role_title") or ""))
    return " ".join(parts)


def _bullet_text(bullet: dict) -> str:
    return f"{bullet.get('role', '')} {bullet.get('text', '')} {' '.join(bullet.get('tags') or [])}"


# ── Scoring ────────────────────────────────────────────────────────────────────

def _tfidf_scores(query: str, docs: list[str]) -> list[float]:
    corpus = [_tokens(d) for d in docs]
    q = _tokens(query)
    n = len(corpus) + 1
    df = Counter()
    for toks in corpus + [q]:
        df.update(set(toks))
    idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}

    def vec(toks):
        return {t: (1 + math.log(c)) * idf[t] for t, c in Counter(toks).items()}

    def norm(v):
        return math.sqrt(sum(x * x for x in v.values()))

    qv = vec(q)
    qn = norm(qv)
    scores = []
    for toks in corpus:
        v = vec(toks)
        vn = norm(v)
        if not qn or not vn:
            scores.append(0.0)
            continue
        scores.append(sum(w * v.get(t, 0.0) for t, w in qv.items()) / (qn * vn))
    return scores


def _embedding_scores(query: str, docs: list[str]) -> list[float]:
    global _model
    if _model is None:
        import config
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(getattr(config, "JOB_PICK_EMBEDDING_MODEL", "all-MiniLM-L6-v2"))
    vecs = _model.encode([query] + docs, normalize_embeddings=True)
    return [float(v @ vecs[0]) for v in vecs[1:]]


# ── Ranking ────────────────────────────────────────────────────────────────────

def rank_bullets(requirements: dict, bullets: list[dict], top_k: int = TOP_K, backend: str = "auto") -> list[dict]:
    """Return copies of the top_k bullets most relevant to the requirements, best first, each with a
    "score". backend is "auto" (embeddings, falling back to TF-IDF on any failure), "embedding" or "tfidf"."""
    if not bullets:
        return []
    query = _query_text(requirements)
    docs = [_bullet_text(b) for b in bullets]
    scores = None
    if backend in ("auto", "embedding"):
        try:
            scores = [round(s, 4) for s in _embedding_scores(query, docs)]
        except Exception:
            if backend == "embedding":
                raise
    if scores is None:
        scores = [round(s, 6) for s in _tfidf_scores(query, docs)]
    order = sorted(range(len(bullets)), key=lambda i: (-scores[i], i))
    return [{**bullets[i], "score": scores[i]} for i in order[:top_k]]
