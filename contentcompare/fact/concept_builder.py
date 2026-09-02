"""F7-1~F7-3 — 후보 쌍 생성 → 코드/온톨로지 확정 → LLM 판정.

**유사도는 여기서만 쓰인다.** 판정이 아니라 "LLM 에게 검토시킬 쌍을 좁히는" 용도이므로
임계값이 틀려도 손해가 작다 — 낮게 잡으면 호출이 늘고, 높게 잡으면 후보가 안 만들어져
``missing`` 이 된다(설계 §2.4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from .concept_assembler import assemble
from .concept_models import (
    BY_CODE,
    BY_LLM,
    BY_NONE,
    BY_ONTOLOGY,
    RELATIONS,
    SAME_AS,
    UNKNOWN,
    ConceptEdge,
    ConceptMember,
    ConceptGraph,
    FactRef,
)
from .fact_matcher import EXACT, RANKED_OUT_EXTRA, FactMatcher, norm_name
from .fact_models import Fact
from .fact_store import FactStore
from .llm_stage import LlmBudgetExceeded
from .ontology import Ontology
from .prompts import CONCEPT_SYSTEM, build_concept_user
from .schemas import schema_for

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
    ontology: Optional[Ontology] = None,
    ranked_out: Optional[list[dict]] = None,
) -> list[CandidatePair]:
    """기준 fact × 각 대상 문서에서 검토할 후보 쌍을 만든다(recall 전용).

    ``ontology`` 를 주면 **recall 과 독립적으로** 온톨로지가 아는 쌍을 마저 채운다
    (설계 §F7-2). 유사도가 낮아 후보가 안 만들어진 쌍은 사람이 ``same_as`` 로 적어도
    영원히 이어지지 않는데, 승격이 가장 필요한 쌍이 바로 "유사도로는 못 잇는 진짜
    동의어"다(설계 §3.2). ``FactMatcher.search()`` 가 이름 완전일치에서 조기 종료하는
    것도 같은 구멍을 만든다. 이름 쌍 조회는 임베딩이 필요 없어 비용이 낮다(dict 조회).

    ``ranked_out`` 은 진단용 out-param 이다(기준 fact 별 후보 랭킹 + 탈락 사유).
    호출자가 저장을 맡고 이 모듈은 ``ArtifactStore`` 를 알지 않는다 —
    ``record_stats``/``fact_stats`` 와 같은 기존 패턴이다.
    """
    if not store.ready:
        return []
    ref_doc = store.reference
    pairs: list[CandidatePair] = []
    seen: set[tuple[str, str, str, str]] = set()
    diag: dict[str, dict] = {}  # ref_fact_id → 진단 행(삽입 순서 = 기준 fact 순서)

    def _add(target_doc: str, ref_fact: Fact, tgt_fact: Fact,
             score: float, exact: bool) -> bool:
        key = (ref_doc.doc_name, ref_fact.fact_id, target_doc, tgt_fact.fact_id)
        if key in seen:
            return False
        seen.add(key)
        pairs.append(CandidatePair(
            left_doc=ref_doc.doc_name, left=ref_fact,
            right_doc=target_doc, right=tgt_fact, score=score, exact=exact,
        ))
        return True

    def _slot(ref_fact: Fact, target_doc: str) -> Optional[dict]:
        """진단 구조에서 (기준 fact × 대상 문서) 칸을 꺼낸다(없으면 만든다)."""
        if ranked_out is None:
            return None
        entry = diag.setdefault(ref_fact.fact_id, {
            "ref_fact_id": ref_fact.fact_id,
            "entity_name": ref_fact.entity_name,
            "targets": {},
        })
        return entry["targets"].setdefault(
            target_doc, {"doc": target_doc, "ranked": [], "from_ontology": []})

    for target in store.targets:
        matcher = FactMatcher(
            target.facts.facts,
            embedder=embedder,
            top_k=top_k,
            min_score=min_score,
            review_score=min_score,  # F7 에서 needs_review 는 연결 주체로 대체된다
        )
        for ref_fact in ref_doc.facts.facts:
            slot = _slot(ref_fact, target.doc_name)
            rows = slot["ranked"] if slot is not None else None
            for cand in matcher.search(ref_fact, ranked_out=rows):
                _add(target.doc_name, ref_fact, cand.fact,
                     cand.score, cand.method == EXACT)

    added = _augment_with_ontology(store, ontology, _add, _slot)
    if ranked_out is not None:
        ranked_out.extend(
            {"ref_fact_id": e["ref_fact_id"], "entity_name": e["entity_name"],
             "targets": list(e["targets"].values())}
            for e in diag.values()
        )
    logger.info("[Concept] 후보 쌍 %d 건(온톨로지 보강 %d 건)", len(pairs), added)
    return pairs


def _augment_with_ontology(store: FactStore, ontology: Optional[Ontology],
                           add: Any, slot: Any = None) -> int:
    """온톨로지가 아는 (기준 × 대상) 쌍 중 recall 이 빠뜨린 것을 무조건 추가한다.

    추가된 쌍은 ``score=0.0``/``exact=False`` 로 두고, 관계는 평소대로
    :func:`resolve_known` 이 온톨로지에서 읽어 붙인다.

    ``slot`` 은 진단 구조의 칸을 돌려주는 콜백이다 — 이렇게 보강된 쌍은 유사도
    랭킹에 없으므로, 구분해 두지 않으면 뷰어가 "recall 이 찾았다"로 오해한다.
    """
    ref_doc = store.reference
    if ref_doc is None or ontology is None or not len(ontology):
        return 0
    added = 0
    for target in store.targets:
        for ref_fact in ref_doc.facts.facts:
            for tgt_fact in target.facts.facts:
                if ontology.relation_for(ref_fact.entity_name, tgt_fact.entity_name) is None:
                    continue
                if add(target.doc_name, ref_fact, tgt_fact, 0.0, False):
                    added += 1
                if slot is not None:
                    cell = slot(ref_fact, target.doc_name)
                    if cell is not None and tgt_fact.fact_id not in cell["from_ontology"]:
                        cell["from_ontology"].append(tgt_fact.fact_id)
    return added


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


def judge_pairs(
    runner: Any,
    pairs: list[CandidatePair],
    *,
    knowledge: str = "",
    purpose: str = "",
    ontology_summary: str = "",
    batch_size: int = 20,
) -> tuple[list[ConceptEdge], int]:
    """남은 후보 쌍을 배치로 LLM 에 넘겨 관계를 받는다.

    실패·예산 초과·응답 누락은 전부 ``unknown`` 엣지로 남긴다. **쌍을 잃지 않는 것**이
    중요하다 — 판단 못 한 쌍은 리포트의 '검토 필요'로 사람에게 간다.

    반환은 ``(엣지, 예산 소진으로 판정 못 한 쌍 수)``. 예산 초과는 조용히 전 항목
    ``missing`` 으로 귀결되므로 **호출자가 드러낼 수 있게** 별도로 센다.
    """
    edges: list[ConceptEdge] = []
    exhausted = 0
    for start in range(0, len(pairs), max(1, batch_size)):
        batch = pairs[start : start + max(1, batch_size)]
        batch_edges, batch_exhausted = _judge_batch(
            runner, batch, knowledge, purpose, ontology_summary)
        edges.extend(batch_edges)
        exhausted += batch_exhausted
    return edges, exhausted


def _judge_batch(
    runner: Any,
    batch: list[CandidatePair],
    knowledge: str,
    purpose: str,
    ontology_summary: str,
) -> tuple[list[ConceptEdge], int]:
    by_ids: dict[tuple[str, str], CandidatePair] = {
        (p.left.fact_id, p.right.fact_id): p for p in batch
    }
    # 프롬프트 조립은 try 밖에서 한다 — 조립 버그가 'LLM 판정 실패'로 위장되면
    # 전 항목 missing 이 되면서 원인 추적이 불가능해진다.
    user = build_concept_user(batch, knowledge=knowledge, purpose=purpose,
                              ontology_summary=ontology_summary)
    try:
        obj = runner.complete_json(CONCEPT_SYSTEM, user,
                                   schema=schema_for("concept"))
    except Exception as e:  # noqa: BLE001 — 배치 격리(LlmBudgetExceeded·파싱실패·네트워크)
        logger.warning("[Concept] 배치 판정 실패(%s) → 보류: %s", type(e).__name__, e)
        exhausted = len(batch) if isinstance(e, LlmBudgetExceeded) else 0
        return ([_unknown_edge(p, f"LLM 판정 실패({type(e).__name__})") for p in batch],
                exhausted)

    decided: dict[tuple[str, str], ConceptEdge] = {}
    for item in (obj.get("pairs") or []):
        if not isinstance(item, dict):
            continue
        key = (str(item.get("left_fact_id") or ""), str(item.get("right_fact_id") or ""))
        pair = by_ids.get(key)
        if pair is None:
            logger.info("[Concept] 후보에 없는 id 지목 → 무시: %s", key)
            continue
        relation = str(item.get("relation") or UNKNOWN)
        if relation not in RELATIONS:
            relation = UNKNOWN
        decided[key] = ConceptEdge(
            relation=relation, left=pair.left_ref, right=pair.right_ref,
            axis=str(item.get("axis") or ""),
            left_text=str(item.get("left_text") or ""),
            right_text=str(item.get("right_text") or ""),
            reason=str(item.get("reason") or ""),
            decided_by=BY_LLM, recall_score=pair.score,
        )
    return ([
        decided.get((p.left.fact_id, p.right.fact_id))
        or _unknown_edge(p, "LLM 응답에 이 쌍이 없었습니다")
        for p in batch
    ], 0)


def _unknown_edge(pair: CandidatePair, reason: str,
                  decided_by: str = BY_LLM) -> ConceptEdge:
    """판정하지 못한 쌍을 ``unknown`` 으로 남긴다(버리지 않는다).

    ``decided_by`` 는 **누가 판정했는지**의 계측이다. LLM 을 아예 쓰지 않아 판정하지
    않은 쌍까지 ``llm`` 으로 기록하면 위임률 통계가 거짓이 된다.
    """
    return ConceptEdge(
        relation=UNKNOWN, left=pair.left_ref, right=pair.right_ref,
        reason=reason, decided_by=decided_by, recall_score=pair.score,
    )


def build_concept_graph(
    store: FactStore,
    *,
    embedder: Any = None,
    runner: Any = None,
    ontology: Optional[Ontology] = None,
    knowledge: str = "",
    purpose: str = "",
    top_k: int = 5,
    min_score: float = 0.3,
    batch_size: int = 20,
    pairs_out: Optional[dict] = None,
) -> ConceptGraph:
    """F7 전체 — 후보 쌍 → 코드/온톨로지 확정 → LLM → 조립.

    ``pairs_out`` 은 진단용 out-param — 채워 주면 호출자가 ``candidate_pairs.json``
    으로 저장한다(:func:`candidate_pairs` 의 ``ranked_out`` 참고).
    """
    ontology = ontology or Ontology()
    by_ref: Optional[list[dict]] = [] if pairs_out is not None else None
    pairs = candidate_pairs(store, embedder=embedder, top_k=top_k, min_score=min_score,
                            ontology=ontology, ranked_out=by_ref)
    if pairs_out is not None:
        pairs_out.update({
            "reference": store.reference.doc_name if store.reference else "",
            "params": {"top_k": top_k, "min_score": min_score,
                       "batch_pairs": batch_size,
                       "ranked_out_extra": RANKED_OUT_EXTRA},
            "by_ref": by_ref or [],
        })
    known, remaining = resolve_known(pairs, ontology)

    llm_edges: list[ConceptEdge] = []
    budget_exhausted = 0
    if remaining and runner is not None:
        llm_edges, budget_exhausted = judge_pairs(
            runner, remaining, knowledge=knowledge, purpose=purpose,
            ontology_summary=ontology.summary(), batch_size=batch_size,
        )
    elif remaining:
        llm_edges = [_unknown_edge(p, "LLM 을 쓰지 않아 판정하지 않음", BY_NONE)
                     for p in remaining]

    members: list[ConceptMember] = []
    facts: dict[str, Fact] = {}
    for doc in ([store.reference] if store.reference else []) + list(store.targets):
        for fact in doc.facts.facts:
            members.append(ConceptMember(doc.doc_name, fact.fact_id, fact.entity_name))
            facts[FactRef(doc.doc_name, fact.fact_id).key] = fact

    graph = assemble(members, known + llm_edges, facts)
    graph.stats.update({
        "pairs_considered": len(pairs),
        "pairs_from_ontology": sum(1 for e in known if e.decided_by == BY_ONTOLOGY),
        "pairs_by_code": sum(1 for e in known if e.decided_by == BY_CODE),
        "pairs_by_llm": len(llm_edges),
        "llm_calls": getattr(runner, "calls", 0),
        # 예산 소진으로 판정하지 못한 쌍. 0 보다 크면 리포트가 경고를 띄운다 —
        # 그대로 두면 "전부 대상에 없음"으로만 보이고 원인이 로그에만 남는다.
        "budget_exhausted_pairs": budget_exhausted,
    })
    if budget_exhausted:
        logger.warning("[Concept] 예산 소진으로 %d 쌍을 판정하지 못했습니다 "
                       "— max_llm_calls_per_concept 를 늘리세요", budget_exhausted)
    logger.info("[Concept] 그래프 완성 %s", graph.stats)
    return graph
