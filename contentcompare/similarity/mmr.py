"""MMR(Maximal Marginal Relevance) 재정렬 + 문서별 상한.

관련도(relevance)와 다양성(이미 뽑힌 것과의 비유사성)을 함께 고려해 후보를
고른다. 같은 대상 문서가 결과를 독식하지 않도록 ``per_doc_cap`` 도 적용한다.

    MMR = argmax_d  λ·rel(d) − (1−λ)·max_{s∈S} sim(d, s)
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, Optional, Sequence


def mmr_select(
    cand_idxs: Sequence[int],
    relevance: dict[int, float],
    vec_of: Callable[[int], Sequence[float]],
    doc_of: Callable[[int], str],
    *,
    lambda_: float = 0.5,
    top_k: int = 10,
    per_doc_cap: Optional[int] = None,
) -> list[int]:
    """후보 인덱스들을 MMR 로 재정렬해 상위 ``top_k`` 를 반환.

    relevance 는 임의 스케일이어도 되며 내부에서 [0,1] 로 정규화한다.
    vec_of 가 반환하는 벡터는 **정규화(단위벡터)** 되어 있다고 가정(내적=코사인).
    """
    if not cand_idxs:
        return []

    rels = [relevance.get(i, 0.0) for i in cand_idxs]
    rmin, rmax = min(rels), max(rels)
    span = rmax - rmin

    def norm_rel(idx: int) -> float:
        if span == 0:
            return 1.0
        return (relevance.get(idx, 0.0) - rmin) / span

    selected: list[int] = []
    remaining = list(cand_idxs)
    doc_counts: dict[str, int] = defaultdict(int)

    while remaining and len(selected) < top_k:
        best_idx: Optional[int] = None
        best_score = float("-inf")
        for idx in remaining:
            if per_doc_cap and doc_counts[doc_of(idx)] >= per_doc_cap:
                continue
            redundancy = 0.0
            if selected:
                redundancy = max(_dot(vec_of(idx), vec_of(s)) for s in selected)
            score = lambda_ * norm_rel(idx) - (1 - lambda_) * redundancy
            if score > best_score:
                best_score = score
                best_idx = idx
        if best_idx is None:  # 남은 후보가 전부 상한에 걸림
            break
        selected.append(best_idx)
        remaining.remove(best_idx)
        doc_counts[doc_of(best_idx)] += 1
    return selected


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
