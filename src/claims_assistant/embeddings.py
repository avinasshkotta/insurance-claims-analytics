"""Semantic retrieval: hosted embeddings + a local vector cache.

Reference architecture for Layer 2 is chunk → embed → vector search. This module
implements that with an OpenAI-compatible embedding endpoint and a JSON vector
cache (swap for Chroma/pgvector in production — the interface is the same).
BM25 remains the fallback when no key or no index exists, so the system works
fully offline.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from . import config


def chunk_key(chunk: dict) -> str:
    """Stable identity: doc + section + content hash (content changes re-embed)."""
    digest = hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest()[:12]
    return f"{chunk['doc_id']}|{chunk['section']}|{digest}"


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def reciprocal_rank_fusion(rankings: list[list[dict]], k: int = 60) -> list[dict]:
    """Fuse ranked lists (BM25 + semantic) into one order."""
    scores: dict[str, float] = {}
    chunks: dict[str, dict] = {}
    for ranking in rankings:
        for rank, chunk in enumerate(ranking):
            key = chunk_key(chunk)
            chunks[key] = chunk
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
    return [chunks[key] for key in sorted(scores, key=lambda x: -scores[x])]


class EmbeddingIndex:
    """JSON-backed vector cache keyed by chunk identity."""

    def __init__(self, path: Path | None = None, model: str | None = None):
        self.path = path or config.EMBEDDINGS_PATH
        self.model = model or config.EMBEDDING_MODEL
        self._vectors: dict[str, list[float]] = {}
        self.load()

    # ---- persistence -------------------------------------------------------
    def load(self) -> int:
        if not self.path.exists():
            return 0
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return 0
        if data.get("model") == self.model:
            self._vectors = data.get("vectors", {})
        return len(self._vectors)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "model": self.model,
            "vectors": self._vectors,
        }), encoding="utf-8")

    # ---- build / query -----------------------------------------------------
    def __len__(self) -> int:
        return len(self._vectors)

    def covers(self, chunks: list[dict]) -> bool:
        return all(chunk_key(c) in self._vectors for c in chunks)

    def build(self, chunks: list[dict], embed_fn) -> int:
        """Embed any chunks not already cached. Returns number embedded."""
        missing = [c for c in chunks if chunk_key(c) not in self._vectors]
        if not missing:
            return 0
        vectors = embed_fn([c["text"] for c in missing])
        for chunk, vec in zip(missing, vectors):
            self._vectors[chunk_key(chunk)] = vec
        self.save()
        return len(missing)

    def vector(self, chunk: dict) -> list[float] | None:
        return self._vectors.get(chunk_key(chunk))

    def similarities(self, query_vector: list[float],
                     chunks: list[dict]) -> list[tuple[float, dict]]:
        scored = []
        for c in chunks:
            v = self.vector(c)
            if v:
                scored.append((cosine(query_vector, v), c))
        scored.sort(key=lambda x: -x[0])
        return scored
