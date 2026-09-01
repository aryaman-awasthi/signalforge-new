"""Lexical ranking for the knowledge index (REQ-035).

BM25 is here, in the domain, for two reasons. It is the *floor*: kb-index ranks
with it whether or not an embedding model is reachable, so a model outage
degrades result quality and never the feature. And it is deterministic: the same
corpus and the same query produce the same order on every machine, which is what
lets a drill rehearse a search and an evaluation suite assert on it.

Scoring is the standard Okapi BM25 with k1=1.5, b=0.75. Nothing here is clever;
what matters is that it is honest about a small corpus - a term in every document
scores zero, and the implementation says so rather than producing noise.
"""

from __future__ import annotations

import math
import re
from typing import Iterable, Sequence

K1 = 1.5
B = 0.75

TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_+.-]*")

STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
        "has", "have", "how", "if", "in", "into", "is", "it", "its", "of", "on",
        "or", "that", "the", "then", "there", "these", "this", "to", "was",
        "were", "what", "when", "which", "who", "will", "with",
    }
)


def tokenize(text: str) -> tuple[str, ...]:
    """Lowercase word tokens, stopwords dropped.

    Punctuation inside a token is kept after the first character so that
    `checkout-api`, `p99.9` and `v1.2.3` survive as single terms - in incident
    text those are the words that matter most.
    """
    tokens: list[str] = []
    for raw in TOKEN_PATTERN.findall(text.lower()):
        # Trailing punctuation is sentence structure, not part of the word:
        # "deploy." has to match "deploy". Leading-position punctuation is
        # already excluded by the pattern, and a version like `v1.2.3` or a
        # percentile like `p99.9` ends in a digit, so it survives untouched.
        token = raw.rstrip("._+-")
        if token and token not in STOPWORDS:
            tokens.append(token)
    return tuple(tokens)


def bm25_scores(
    query_tokens: Sequence[str], corpus: Sequence[Sequence[str]]
) -> list[float]:
    """One score per document, in corpus order. Never negative."""
    if not corpus or not query_tokens:
        return [0.0] * len(corpus)

    lengths = [len(document) for document in corpus]
    average_length = sum(lengths) / len(corpus) or 1.0
    total = len(corpus)

    scores = [0.0] * total
    for term in set(query_tokens):
        containing = sum(1 for document in corpus if term in document)
        if containing == 0:
            continue
        # The +1 inside the log keeps idf non-negative. Without it, a term
        # present in more than half the corpus scores negative and a document
        # is *penalised* for containing the word that was searched for - which
        # on a small corpus of runbooks about one system is most of them.
        idf = math.log(1 + (total - containing + 0.5) / (containing + 0.5))
        for index, document in enumerate(corpus):
            frequency = document.count(term)
            if not frequency:
                continue
            denominator = frequency + K1 * (
                1 - B + B * (lengths[index] / average_length)
            )
            scores[index] += idf * (frequency * (K1 + 1)) / denominator
    return scores


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity clamped to [0, 1]; 0 when either side is empty or a
    zero vector, so a missing embedding is never mistaken for a perfect match."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    if norm == 0:
        return 0.0
    return max(0.0, min(1.0, dot / norm))


def normalize(scores: Iterable[float]) -> list[float]:
    """Scale to [0, 1] so a lexical score and a cosine can be blended. An
    all-equal set maps to zero rather than to one: no document stood out."""
    values = list(scores)
    if not values:
        return []
    highest = max(values)
    lowest = min(values)
    if highest <= 0 or highest == lowest:
        return [0.0] * len(values)
    return [(value - lowest) / (highest - lowest) for value in values]


def snippet(body: str, query_tokens: Sequence[str], width: int = 220) -> str:
    """The window of the document a hit is actually about.

    Picked around the first query term rather than from the top of the document,
    because a runbook's first paragraph is the same boilerplate in every runbook.
    """
    text = " ".join(body.split())
    if len(text) <= width:
        return text
    lowered = text.lower()
    position = -1
    for term in query_tokens:
        found = lowered.find(term)
        if found != -1 and (position == -1 or found < position):
            position = found
    if position == -1:
        return text[:width].rstrip() + "..."
    start = max(0, position - width // 3)
    end = min(len(text), start + width)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return prefix + text[start:end].strip() + suffix
