"""Keyword (BM25) scoring and the hybrid blend with semantic similarity.

Semantic similarity alone misses exact terms ("FDP", "DX402") and keywords alone miss
paraphrases ("can a duty run long on a short day"), so searches blend the two as
``alpha * keyword + (1 - alpha) * semantic``. Both sides are rescaled to 0-1 first so a
0.5/0.5 blend really does weigh them equally.

If one side is flat (every candidate scored the same, normal for keywords when a paraphrase
shares no words with any document) it is dropped and the other side scores alone. Blending
a flat side in would cap every score at ``alpha``, and a perfect paraphrase could never
clear a threshold above that.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

K1 = 1.5
B = 0.75
TOKEN_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "should",
        "so",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "this",
        "to",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    ]
)


def tokens(text: str) -> list[str]:
    """Lower-case words and ids, stopwords removed. A hyphenated id is kept whole and also
    split into its parts, so ``RULE-FDP-01`` matches a query for "FDP"."""
    out = []
    for token in TOKEN_RE.findall(text.lower()):
        out.append(token)
        if "-" in token:
            out.extend(part for part in token.split("-") if part)
    return [t for t in out if t not in STOPWORDS]


def bm25(query: str, documents: Sequence[str]) -> list[float]:
    """Okapi BM25 score of ``query`` against each document, in document order."""
    docs = [tokens(d) for d in documents]
    n = len(docs)
    if n == 0:
        return []
    avg_len = sum(len(d) for d in docs) / n or 1.0
    df = Counter(term for d in docs for term in set(d))
    query_terms = tokens(query)
    scores = []
    for d in docs:
        tf = Counter(d)
        score = 0.0
        for term in query_terms:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            norm = tf[term] * (K1 + 1) / (tf[term] + K1 * (1 - B + B * len(d) / avg_len))
            score += idf * norm
        scores.append(score)
    return scores


def normalize(scores: Sequence[float]) -> list[float]:
    """Min-max to 0-1 so two unrelated scales can be blended."""
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-9:
        return [0.0 for _ in scores]
    return [(s - lo) / (hi - lo) for s in scores]


def is_flat(scores: Sequence[float]) -> bool:
    return max(scores) - min(scores) < 1e-9


def blend(keyword: Sequence[float], semantic: Sequence[float], alpha: float = 0.5) -> list[float]:
    """``alpha * keyword + (1 - alpha) * semantic``, each normalised. A flat side is dropped.

    A single candidate has both sides flat and scores 0. Blended scores only rank candidates
    against each other; for absolute quality use the hit's raw semantic similarity.
    """
    if len(keyword) != len(semantic):
        raise ValueError("score lists must be the same length")
    if not keyword:
        return []
    keyword_flat, semantic_flat = is_flat(keyword), is_flat(semantic)
    if keyword_flat and semantic_flat:
        return [0.0 for _ in keyword]
    if keyword_flat:
        return normalize(semantic)
    if semantic_flat:
        return normalize(keyword)
    k, s = normalize(keyword), normalize(semantic)
    return [alpha * a + (1 - alpha) * b for a, b in zip(k, s, strict=True)]
