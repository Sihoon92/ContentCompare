"""Reciprocal Rank Fusion (RRF).

서로 다른 검색기(임베딩, BM25)의 순위를 점수로 합친다. 점수 스케일이 달라도
순위만 쓰므로 안정적이다: ``score(d) = Σ_r w_r / (k + rank_r(d))``.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional, Sequence


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[int]],
    *,
    k: int = 60,
    weights: Optional[Sequence[float]] = None,
) -> list[tuple[int, float]]:
    """여러 순위(문서 인덱스의 best-first 리스트)를 RRF 로 융합.

    Returns: ``(doc_idx, fused_score)`` 를 점수 내림차순으로 정렬한 리스트.
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    fused: dict[int, float] = defaultdict(float)
    for w, ranking in zip(weights, rankings):
        for rank, idx in enumerate(ranking):
            fused[idx] += w / (k + rank + 1)  # rank 0-based → +1
    return sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
