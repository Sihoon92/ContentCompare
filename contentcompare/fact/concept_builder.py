"""F7-1~F7-3 — 후보 쌍 생성 → 코드/온톨로지 확정 → LLM 판정.

**유사도는 여기서만 쓰인다.** 판정이 아니라 "LLM 에게 검토시킬 쌍을 좁히는" 용도이므로
임계값이 틀려도 손해가 작다 — 낮게 잡으면 호출이 늘고, 높게 잡으면 후보가 안 만들어져
``missing`` 이 된다(설계 §2.4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from .concept_models import (
    BY_CODE,
    BY_ONTOLOGY,
    SAME_AS,
    ConceptEdge,
    FactRef,
)
from .fact_matcher import EXACT, FactMatcher, norm_name
from .fact_models import Fact
from .fact_store import FactStore
from .ontology import Ontology

logger = logging.getLogger(__name__)


@dataclass
class CandidatePair:
    """LLM/코드가 관계를 정해야 할 fact 쌍 하나."""

    left_doc: str
    left: Fact
    right_doc: str
    right: Fact
    score: float = 0.0
    exact: bool = False
    """정규화 항목명이 완전히 같은가(코드가 바로 확정할 수 있는 신호)."""

    @property
    def left_ref(self) -> FactRef:
        return FactRef(self.left_doc, self.left.fact_id)

    @property
    def right_ref(self) -> FactRef:
        return FactRef(self.right_doc, self.right.fact_id)


def candidate_pairs(
    store: FactStore,
    *,
    embedder: Any = None,
    top_k: int = 5,
    min_score: float = 0.3,
) -> list[CandidatePair]:
    """기준 fact × 각 대상 문서에서 검토할 후보 쌍을 만든다(recall 전용)."""
    if not store.ready:
        return []
    ref_doc = store.reference
    pairs: list[CandidatePair] = []
    for target in store.targets:
        matcher = FactMatcher(
            target.facts.facts,
            embedder=embedder,
            top_k=top_k,
            min_score=min_score,
            review_score=min_score,  # F7 에서 needs_review 는 연결 주체로 대체된다
        )
        for ref_fact in ref_doc.facts.facts:
            for cand in matcher.search(ref_fact):
                pairs.append(CandidatePair(
                    left_doc=ref_doc.doc_name, left=ref_fact,
                    right_doc=target.doc_name, right=cand.fact,
                    score=cand.score, exact=(cand.method == EXACT),
                ))
    logger.info("[Concept] 후보 쌍 %d 건", len(pairs))
    return pairs


def resolve_known(
    pairs: list[CandidatePair], ontology: Ontology
) -> tuple[list[ConceptEdge], list[CandidatePair]]:
    """LLM 없이 정할 수 있는 것을 먼저 확정한다.

    우선순위는 **온톨로지(사람) > 정규화 이름 완전일치(코드)** 다. 사람이 "이 둘은
    다르다"고 확정했으면 이름이 같아도 잇지 않는다.
    """
    edges: list[ConceptEdge] = []
    remaining: list[CandidatePair] = []
    for pair in pairs:
        known = ontology.relation_for(pair.left.entity_name, pair.right.entity_name)
        if known is not None:
            relation, axis, reason = known
            edges.append(ConceptEdge(
                relation=relation, left=pair.left_ref, right=pair.right_ref,
                axis=axis, reason=reason, decided_by=BY_ONTOLOGY, promoted=True,
                recall_score=pair.score,
            ))
            continue
        if pair.exact:
            edges.append(ConceptEdge(
                relation=SAME_AS, left=pair.left_ref, right=pair.right_ref,
                left_text=pair.left.evidence_text, right_text=pair.right.evidence_text,
                reason=f"정규화 항목명이 동일합니다: {norm_name(pair.left.entity_name)}",
                decided_by=BY_CODE, recall_score=pair.score,
            ))
            continue
        remaining.append(pair)
    logger.info("[Concept] 코드/온톨로지 확정 %d 건, LLM 위임 %d 건", len(edges), len(remaining))
    return edges, remaining
