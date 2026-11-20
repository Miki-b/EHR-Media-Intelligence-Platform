"""Pure ranking helpers: hybrid scoring, per-resource collapse, snippets.

Score = 0.7 * cosine similarity + 0.3 * normalised BM25. The vector part
captures meaning ("poorly controlled blood sugar" ~ "HbA1c 9.8%"); the BM25
part keeps exact clinical tokens ("CXR", "HbA1c") decisive when they appear.
Both parts live in [0, 1], so the blend does too.
"""

import re
from dataclasses import dataclass, field

from app.search.embed import tokenize

VECTOR_WEIGHT = 0.7
BM25_WEIGHT = 0.3
SNIPPET_CHARS = 220

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Candidate:
    chunk_id: str
    text: str
    metadata: dict
    vector: float = 0.0  # cosine similarity in [0, 1]
    bm25: float = 0.0  # normalised BM25 in [0, 1]
    score: float = field(init=False, default=0.0)

    def blend(self) -> "Candidate":
        self.score = round(
            min(1.0, max(0.0, VECTOR_WEIGHT * self.vector + BM25_WEIGHT * self.bm25)), 4
        )
        return self


def cosine_from_distance(distance: float) -> float:
    """Chroma returns cosine distance (1 - similarity); clamp into [0, 1]."""
    return min(1.0, max(0.0, 1.0 - distance))


def normalise(scores: list[float]) -> list[float]:
    top = max(scores, default=0.0)
    return [s / top if top > 0 else 0.0 for s in scores]


def collapse_by_resource(candidates: list[Candidate]) -> list[Candidate]:
    """Keep the best-scoring window per resource, highest score first."""
    best: dict[str, Candidate] = {}
    for c in candidates:
        rid = c.metadata.get("resource_id", c.chunk_id)
        if rid not in best or c.score > best[rid].score:
            best[rid] = c
    return sorted(best.values(), key=lambda c: (-c.score, c.metadata.get("date", "")))


def snippet(text: str, query: str, limit: int = SNIPPET_CHARS) -> str:
    """The sentence sharing most tokens with the query, extended with its
    neighbours until `limit` characters."""
    sentences = [s.strip() for s in _SENTENCE.split(text) if s.strip()]
    if not sentences:
        return text[:limit]
    terms = set(tokenize(query))
    hits = [len(terms & set(tokenize(s))) for s in sentences]
    start = max(range(len(sentences)), key=lambda i: (hits[i], -i))
    out = sentences[start]
    i = start + 1
    while i < len(sentences) and len(out) + len(sentences[i]) + 1 <= limit:
        out = f"{out} {sentences[i]}"
        i += 1
    if len(out) > limit:
        out = out[: limit - 1].rsplit(" ", 1)[0] + "…"
    return out
