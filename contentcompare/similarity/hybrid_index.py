"""하이브리드 검색 인덱스 (임베딩 + BM25 → RRF → MMR).

흐름:
  1. 임베딩 코사인 순위 + BM25 어휘 순위를 각각 recall_k 개 뽑는다.
  2. RRF 로 두 순위를 융합(``fusion=cosine`` 이면 임베딩 단독).
  3. MMR + per_doc_cap 으로 다양성을 확보하며 top_k 를 고른다.

대규모로 가면 내부의 numpy 브루트포스를 hnswlib/FAISS 로만 바꾸면 되도록
검색 인터페이스(:meth:`add` / :meth:`search`)는 단순하게 유지한다.
"""

from __future__ import annotations

import math
from typing import Optional

from ..llm.base import EmbeddingClient
from ..models import Candidate, DocItem
from .bm25 import BM25
from .fusion import reciprocal_rank_fusion
from .mmr import mmr_select
from .tokenize import tokenize


class HybridIndex:
    def __init__(
        self,
        embedder: EmbeddingClient,
        *,
        fusion: str = "rrf",
        rrf_k: int = 60,
        mmr_lambda: float = 0.5,
        per_doc_cap: Optional[int] = 4,
        min_score: float = 0.0,
    ) -> None:
        self.embedder = embedder
        self.fusion = fusion
        self.rrf_k = rrf_k
        self.mmr_lambda = mmr_lambda
        self.per_doc_cap = per_doc_cap
        self.min_score = min_score

        self._items: list[DocItem] = []
        self._vectors: list[list[float]] = []
        self._tokens: list[list[str]] = []
        self._bm25 = BM25()
        self._dirty = False  # 코퍼스 변경 후 BM25 재색인 필요 여부

    # ------------------------------------------------------------------ #
    def add(self, items: list[DocItem], *, batch_size: int = 64) -> None:
        """items 를 임베딩+토큰화해 인덱스에 추가한다."""
        items = [it for it in items if not it.is_empty()]
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            vectors = self.embedder.embed([it.text for it in batch])
            for it, vec in zip(batch, vectors):
                self._items.append(it)
                self._vectors.append(_normalize(list(vec)))
                self._tokens.append(tokenize(it.text))
        if items:
            self._dirty = True

    def _ensure_bm25(self) -> None:
        if self._dirty:
            self._bm25.index(self._tokens)
            self._dirty = False

    # ------------------------------------------------------------------ #
    def search(
        self,
        query_text: str,
        *,
        recall_k: int = 30,
        top_k: int = 10,
        mmr_lambda: Optional[float] = None,
        per_doc_cap: Optional[int] = None,
    ) -> list[Candidate]:
        """query_text 와 관련된 후보를 RRF→MMR 로 골라 반환한다."""
        if not self._items:
            return []
        self._ensure_bm25()
        lam = self.mmr_lambda if mmr_lambda is None else mmr_lambda
        cap = self.per_doc_cap if per_doc_cap is None else per_doc_cap

        q_vec = _normalize(list(self.embedder.embed([query_text])[0]))
        cos = [_dot(q_vec, v) for v in self._vectors]

        if self.fusion == "cosine":
            # 임베딩 단독: min_score 필터 후 점수=코사인.
            order = sorted(range(len(cos)), key=lambda i: cos[i], reverse=True)
            pool = [i for i in order if cos[i] >= self.min_score][:recall_k]
            relevance = {i: cos[i] for i in pool}
        else:
            # RRF: 임베딩 순위 + BM25 순위 융합.
            emb_rank = sorted(range(len(cos)), key=lambda i: cos[i], reverse=True)[:recall_k]
            bm = self._bm25.scores(tokenize(query_text))
            bm_rank = [i for i in sorted(range(len(bm)), key=lambda i: bm[i], reverse=True)
                       if bm[i] > 0][:recall_k]
            fused = reciprocal_rank_fusion([emb_rank, bm_rank], k=self.rrf_k)
            pool = [idx for idx, _ in fused]
            relevance = dict(fused)

        if not pool:
            return []

        chosen = mmr_select(
            pool,
            relevance,
            vec_of=lambda i: self._vectors[i],
            doc_of=lambda i: self._items[i].doc_id,
            lambda_=lam,
            top_k=top_k,
            per_doc_cap=cap,
        )
        return [Candidate(item=self._items[i], score=round(relevance[i], 6)) for i in chosen]

    def __len__(self) -> int:
        return len(self._items)


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
