"""F5 Matcher 의 개념 그래프 경로 테스트 — 유사도·임계값 없음."""

from contentcompare.fact.concept_models import (
    BY_CODE,
    BY_LLM,
    BY_ONTOLOGY,
    SAME_AS,
    ConceptEdge,
    ConceptGraph,
    ConceptMember,
    ConceptNode,
    FactRef,
)
from contentcompare.fact.fact_matcher import CONCEPT, ConceptMatcher
from contentcompare.fact.fact_models import Fact

REF = Fact(fact_id="fact-row-1", entity_name="공칭용량")
TGT = Fact(fact_id="fact-word-7", entity_name="공칭용량")
OTHER = Fact(fact_id="fact-word-11", entity_name="표준환경온도")


def _graph(decided_by=BY_CODE, promoted=False, linked=True) -> ConceptGraph:
    members = [ConceptMember("기준.xlsx", "fact-row-1", "공칭용량")]
    if linked:
        members.append(ConceptMember("규격서.docx", "fact-word-7", "공칭용량"))
        nodes = [ConceptNode("c-0001", "공칭용량", members)]
    else:
        nodes = [
            ConceptNode("c-0001", "공칭용량", members),
            ConceptNode("c-0002", "표준환경온도",
                        [ConceptMember("규격서.docx", "fact-word-11", "표준환경온도")]),
        ]
    edge = ConceptEdge(
        SAME_AS, FactRef("기준.xlsx", "fact-row-1"), FactRef("규격서.docx", "fact-word-7"),
        decided_by=decided_by, promoted=promoted, recall_score=0.83,
    )
    return ConceptGraph(nodes=nodes, edges=[edge])


def _matcher(graph, facts=None) -> ConceptMatcher:
    return ConceptMatcher(graph, "기준.xlsx", "규격서.docx", facts or [TGT, OTHER])


def test_linked_concept_yields_candidate():
    cands = _matcher(_graph()).search(REF)
    assert [c.fact.fact_id for c in cands] == ["fact-word-7"]
    assert cands[0].method == CONCEPT


def test_unlinked_concept_yields_nothing():
    """연결이 없으면 후보가 없다 → 상위 Comparator 가 missing 으로 판정한다."""
    assert _matcher(_graph(linked=False)).search(REF) == []


def test_code_decided_link_does_not_need_review():
    """정규화 이름 완전일치는 실측 10/10 이라 현행 F5 처럼 신뢰한다."""
    assert _matcher(_graph(decided_by=BY_CODE)).search(REF)[0].needs_review is False


def test_promoted_link_does_not_need_review():
    cands = _matcher(_graph(decided_by=BY_ONTOLOGY, promoted=True)).search(REF)
    assert cands[0].needs_review is False


def test_llm_decided_link_needs_review():
    """아직 아무도 확인하지 않은 연결 위에서 코드가 mismatch 를 단정하지 않게 한다."""
    assert _matcher(_graph(decided_by=BY_LLM)).search(REF)[0].needs_review is True


def test_recall_score_is_carried_for_diagnostics():
    assert _matcher(_graph()).search(REF)[0].score == 0.83


def test_member_missing_from_target_facts_is_skipped():
    """그래프에는 있는데 대상 fact 목록에 없으면(부분 재실행) 조용히 건너뛴다."""
    assert _matcher(_graph(), facts=[OTHER]).search(REF) == []
