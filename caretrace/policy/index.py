"""Hybrid BM25 + hashing-vector retrieval with reciprocal rank fusion.

The embedding interface matches a sentence-transformer backend. The default
backend is a local hashing embedder so the demo does not download model weights.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from caretrace.policy.chunk import Chunk

TOKEN = re.compile(r"[a-z0-9]{2,}")


class HashingEmbedder:
    """Character n-gram hashing embedder. Same role as bge-small-en, offline."""

    method = "hashing-ngram"

    def __init__(self, dims: int = 256) -> None:
        self.dims = dims

    def embed(self, texts: list[str]) -> np.ndarray:
        rows = np.zeros((len(texts), self.dims), dtype=np.float32)
        for index, text in enumerate(texts):
            tokens = TOKEN.findall(text.lower())
            for token in tokens:
                digest = hashlib.sha256(token.encode()).digest()
                bucket = int.from_bytes(digest[:4], "little") % self.dims
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                rows[index, bucket] += sign
            norm = np.linalg.norm(rows[index])
            if norm:
                rows[index] /= norm
        return rows


class PolicyIndex:
    """In-process hybrid index. Filter by payer_id before fusion."""

    def __init__(self) -> None:
        self.chunks: list[Chunk] = []
        self.embedder = HashingEmbedder()
        self._vectors: np.ndarray | None = None
        self._bm25: BM25Okapi | None = None
        self._tokens: list[list[str]] = []

    def add(self, chunks: list[Chunk]) -> None:
        self.chunks.extend(chunks)
        self._fit()

    def _fit(self) -> None:
        self._tokens = [TOKEN.findall(chunk.text.lower()) for chunk in self.chunks]
        self._bm25 = BM25Okapi(self._tokens or [[""]])
        self._vectors = self.embedder.embed([chunk.text for chunk in self.chunks] or [""])

    def search(
        self, query: str, payer_id: str | None = None, k: int = 5
    ) -> list[tuple[Chunk, float]]:
        """Fuse BM25 and vector ranks with reciprocal rank fusion."""
        if not self.chunks or self._bm25 is None or self._vectors is None:
            return []
        allowed = [
            index
            for index, chunk in enumerate(self.chunks)
            if payer_id is None or chunk.payer_id == payer_id
        ]
        if not allowed:
            return []
        query_tokens = TOKEN.findall(query.lower())
        bm25_scores = self._bm25.get_scores(query_tokens)
        query_vec = self.embedder.embed([query])[0]
        vector_scores = self._vectors @ query_vec
        bm25_order = sorted(allowed, key=lambda index: float(bm25_scores[index]), reverse=True)
        vector_order = sorted(allowed, key=lambda index: float(vector_scores[index]), reverse=True)
        fused = reciprocal_rank_fusion([bm25_order, vector_order])
        results = []
        for index in fused[:k]:
            results.append((self.chunks[index], fused_score(index, [bm25_order, vector_order])))
        return results

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                "chunk_id": chunk.chunk_id,
                "payer_id": chunk.payer_id,
                "policy_title": chunk.policy_title,
                "effective_date": chunk.effective_date,
                "url": chunk.url,
                "page": chunk.page,
                "section": chunk.section,
                "text": chunk.text,
            }
            for chunk in self.chunks
        ]
        path.write_text(json.dumps(payload), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> PolicyIndex:
        index = cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        index.add([Chunk(**item) for item in raw])
        return index


def reciprocal_rank_fusion(rankings: list[list[int]], k: int = 60) -> list[int]:
    """Fuse ranked id lists. Higher fused score is better."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda doc_id: scores[doc_id], reverse=True)


def fused_score(doc_id: int, rankings: list[list[int]], k: int = 60) -> float:
    score = 0.0
    for ranking in rankings:
        if doc_id in ranking:
            score += 1.0 / (k + ranking.index(doc_id) + 1)
    return score


def stale(effective_date: str | None, today: str = "2026-10-20", months: int = 18) -> bool:
    """True when the policy effective date is older than the window."""
    if not effective_date:
        return False
    from datetime import date

    start = date.fromisoformat(effective_date)
    current = date.fromisoformat(today)
    return (current - start).days > months * 30
