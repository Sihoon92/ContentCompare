"""임베딩 벡터 인덱스.

대상 문서들의 DocItem 을 임베딩해 보관하고, 기준 항목 텍스트와 코사인 유사도가
높은 top-k 후보를 반환한다. 기본 구현은 numpy 기반(소~중규모용)이며, 대규모는
FAISS 등으로 교체할 수 있도록 인터페이스를 단순하게 유지한다.
"""

from __future__ import annotations

import math

from ..llm.base import EmbeddingClient
from ..models import Candidate, DocItem


class VectorIndex:
    def __init__(self, embedder: EmbeddingClient) -> None:
        self.embedder = embedder
        self._items: list[DocItem] = []
        self._vectors: list[list[float]] = []

    def add(self, items: list[DocItem], *, batch_size: int = 64) -> None:
        """items 를 임베딩해 인덱스에 추가한다."""
        items = [it for it in items if not it.is_empty()]
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            vectors = self.embedder.embed([it.text for it in batch])
            for it, vec in zip(batch, vectors):
                self._items.append(it)
                self._vectors.append(_normalize(vec))

    def search(self, query_text: str, *, top_k: int, min_score: float) -> list[Candidate]:
        """query_text 와 유사한 후보를 점수 내림차순으로 반환(min_score 이상만)."""
        if not self._items:
            return []
        q = _normalize(self.embedder.embed([query_text])[0])
        scored: list[Candidate] = []
        for item, vec in zip(self._items, self._vectors):
            score = _dot(q, vec)  # 정규화 후 내적 = 코사인 유사도
            if score >= min_score:
                scored.append(Candidate(item=item, score=score))
        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:top_k]

    def __len__(self) -> int:
        return len(self._items)


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
