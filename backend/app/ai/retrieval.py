"""Lightweight lexical retrieval over user-uploaded reference material (no external services)."""

import math
import re
from collections import Counter

_WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9\-]{2,}")
_STOP = set(
    [
        "the",
        "and",
        "for",
        "are",
        "with",
        "that",
        "this",
        "from",
        "have",
        "was",
        "were",
        "which",
        "their",
        "there",
        "been",
        "into",
        "its",
        "than",
        "then",
        "them",
        "they",
        "what",
        "when",
        "where",
        "who",
        "will",
        "would",
        "could",
        "should",
        "about",
        "also",
        "such",
        "more",
        "most",
        "other",
        "some",
        "these",
        "those",
        "your",
        "you",
        "our",
        "can",
        "may",
        "not",
        "but",
        "all",
        "any",
        "each",
        "how",
        "why",
        "use",
        "used",
        "using",
    ]
)


def _terms(text: str) -> list[str]:
    return [w for w in (m.lower() for m in _WORD.findall(text)) if w not in _STOP]


def chunk(text: str, size: int = 1200, overlap: int = 200) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    out, i = [], 0
    while i < len(text):
        end = min(len(text), i + size)
        if end < len(text):
            sp = text.rfind(". ", i + size // 2, end)
            if sp != -1:
                end = sp + 1
        out.append(text[i:end].strip())
        if end >= len(text):
            break
        i = max(end - overlap, i + 1)
    return out


def top_chunks(query: str, chunks: list[str], k: int = 4, max_chars: int = 6000) -> list[str]:
    """BM25 ranking of chunks for a question; returns the best chunks within a character budget."""
    if not chunks:
        return []
    docs = [Counter(_terms(c)) for c in chunks]
    n = len(docs)
    avgdl = sum(sum(d.values()) for d in docs) / n or 1.0
    df = Counter(t for d in docs for t in d)
    q = set(_terms(query))
    scores = []
    for i, d in enumerate(docs):
        dl = sum(d.values()) or 1
        s = 0.0
        for t in q:
            if t in d:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                tf = d[t]
                s += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * dl / avgdl))
        scores.append((s, i))
    scores.sort(reverse=True)
    out, used = [], 0
    for s, i in scores[:k]:
        if s <= 0:
            break
        if used + len(chunks[i]) > max_chars:
            break
        out.append(chunks[i])
        used += len(chunks[i])
    return out
