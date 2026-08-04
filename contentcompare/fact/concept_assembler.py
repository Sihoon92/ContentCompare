"""F7-4 그래프 조립 — 근거 검증 → ``same_as`` 병합 → ``differs_by`` 위반 강등.

**코드가 위상만 보고 집행한다.** ``axis`` 문자열("측정조건"/"산정방식"/…)은 LLM 이 문서
도메인에 맞게 짓는 이름이며 여기서 해석하지 않는다. 그래서 도메인이 바뀌어도 이 모듈은
그대로다(설계 §2.1).

근거 검증은 **LLM 이 제안한 ``same_as``(``decided_by == BY_LLM``)에만** 적용한다.
사람이 온톨로지로 승격했거나(``BY_ONTOLOGY``) 코드가 정규화 이름 완전일치로 확정한
(``BY_CODE``) 연결은 LLM 의 주장이 아니라 지어낼 수 없으므로 검증 대상이 아니다 —
게다가 온톨로지 확정은 애초에 인용문을 채우지 않는다(사람은 항목명 쌍만 적는다).
차단(``differs_by``)은 근거가 없어도 손해가 없고, 연결만 검증을 요구하는 것이
비대칭 권한 원칙이다(설계 §2.3).
"""

from __future__ import annotations

import logging

from ..similarity.tokenize import tokenize
from .concept_models import (
    BY_LLM,
    DIFFERS_BY,
    SAME_AS,
    UNKNOWN,
    ConceptEdge,
    ConceptGraph,
    ConceptMember,
    ConceptNode,
)
from .fact_models import Fact
from .validator import evidence_coverage

logger = logging.getLogger(__name__)

EVIDENCE_MIN_COVERAGE = 0.8
"""F4a 와 같은 기준. 인용 토큰의 80% 가 원문에 있어야 실재하는 근거로 본다."""


def verify_evidence(edge: ConceptEdge, left: Fact, right: Fact) -> bool:
    """``same_as`` 의 양쪽 인용이 각 fact 원문에 실재하는가."""
    for claim, fact in ((edge.left_text, left), (edge.right_text, right)):
        if not (claim or "").strip():
            return False
        source = set(tokenize(f"{fact.evidence_text} {fact.search_text} {fact.entity_name}"))
        if evidence_coverage(claim, source) < EVIDENCE_MIN_COVERAGE:
            return False
    return True


def assemble(
    members: list[ConceptMember],
    edges: list[ConceptEdge],
    facts: dict[str, Fact],
) -> ConceptGraph:
    """멤버와 엣지로 개념 그래프를 만든다.

    ``facts`` 는 ``FactRef.key -> Fact``. 엣지가 모르는 fact 를 가리키면 조용히 버린다
    (LLM 이 없는 id 를 지목하는 경우 — 현행 Comparator 의 ``_pick_target`` 과 같은 방어).

    근거 검증은 LLM 이 제안한 ``same_as``(``decided_by == BY_LLM``)에만 적용한다.
    사람·코드가 확정한 연결은 지어낼 수 없으므로 인용문이 비어 있어도 병합한다.
    """
    known = {(m.doc, m.fact_id) for m in members}
    valid = [e for e in edges
             if (e.left.doc, e.left.fact_id) in known
             and (e.right.doc, e.right.fact_id) in known]

    stats = {"same_as": 0, "differs_by": 0, "unknown": 0,
             "rejected_evidence": 0, "rejected_differs_by": 0}

    # 1) 근거 검증 — LLM 이 제안한 same_as 만 대상이다. 통과 못 하면 unknown 으로
    #    강등한다(버리지 않는다). 온톨로지/코드 확정은 LLM 의 주장이 아니므로 건너뛴다.
    for edge in valid:
        if edge.relation != SAME_AS or edge.decided_by != BY_LLM:
            continue
        if not verify_evidence(edge, facts[edge.left.key], facts[edge.right.key]):
            edge.relation = UNKNOWN
            edge.rejected_by = "evidence"
            stats["rejected_evidence"] += 1
            logger.info("[Concept] 근거 미실재로 연결 거부: %s", edge.pair_key)

    blockers = [e for e in valid if e.relation == DIFFERS_BY]
    stats["differs_by"] = len(blockers)

    # 2) same_as 병합 — 사람이 승격한 것을 먼저 적용한다.
    parent: dict[str, str] = {f"{m.doc}#{m.fact_id}": f"{m.doc}#{m.fact_id}" for m in members}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def blocked(a: str, b: str) -> bool:
        """a·b 를 합치면 differs_by 로 연결된 두 fact 가 한 개념이 되는가."""
        for d in blockers:
            if {find(d.left.key), find(d.right.key)} == {a, b}:
                return True
        return False

    same_edges = [e for e in valid if e.relation == SAME_AS]
    for edge in sorted(same_edges, key=lambda e: not e.promoted):
        a, b = find(edge.left.key), find(edge.right.key)
        if a == b:
            stats["same_as"] += 1
            continue
        if blocked(a, b):
            edge.relation = UNKNOWN
            edge.rejected_by = "differs_by"
            stats["rejected_differs_by"] += 1
            logger.info("[Concept] differs_by 제약으로 병합 거부: %s", edge.pair_key)
            continue
        parent[a] = b
        stats["same_as"] += 1

    stats["unknown"] = sum(1 for e in valid if e.relation == UNKNOWN)

    # 3) 컴포넌트 → 노드. members 순서를 따라 결정적으로 번호를 매긴다.
    groups: dict[str, list[ConceptMember]] = {}
    for m in members:
        groups.setdefault(find(f"{m.doc}#{m.fact_id}"), []).append(m)
    nodes = [
        ConceptNode(concept_id=f"c-{i:04d}", label=group[0].entity_name, members=group)
        for i, group in enumerate(groups.values(), start=1)
    ]
    return ConceptGraph(nodes=nodes, edges=valid, stats=stats)
