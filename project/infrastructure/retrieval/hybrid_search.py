"""Pure keyword, vector, and hybrid scoring helpers."""

from __future__ import annotations

import math
import re
from typing import List


def tokenize_query(query: str) -> List[str]:
    """Tokenize Chinese and ASCII query text for conservative matching."""
    return [
        token.lower()
        for token in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]+", query or "")
    ]


def keyword_score(content: str, tokens: List[str]) -> float:
    """Score token hits using the established project-KB formula."""
    lower = (content or "").lower()
    score = 0.0
    for token in tokens:
        hits = lower.count(token)
        if hits:
            score += 10 + hits * 2
    if tokens and all(token in lower for token in tokens[:3]):
        score += 8
    return score


def cosine_similarity(left: List[float], right: List[float]) -> float:
    """Return cosine similarity, or zero for invalid vectors."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(item * item for item in left))
    right_norm = math.sqrt(sum(item * item for item in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def vector_score(query_embedding: List[float], chunk_embedding: List[float]) -> float:
    """Scale non-negative cosine similarity to a 0-100 score."""
    return max(0.0, cosine_similarity(query_embedding, chunk_embedding)) * 100.0


def hybrid_score(keyword: float, vector: float, has_embedding: bool) -> float:
    """Combine scores while preserving keyword-only fallback semantics."""
    if not has_embedding:
        return keyword
    return 0.4 * keyword + 0.6 * vector
