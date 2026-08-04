"""F5 Matcher 의 개념 그래프 경로 테스트 — 유사도·임계값 없음."""

from contentcompare.fact.concept_models import (
    BY_CODE,
    BY_LLM,
    BY_ONTOLOGY,
    DIFFERS_BY,
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
    """온톨로지 경로 검증 — 온톨로지 확정된 연결은 신뢰한다."""
    cands = _matcher(_graph(decided_by=BY_ONTOLOGY, promoted=True)).search(REF)
    assert cands[0].needs_review is False


def test_promoted_llm_link_does_not_need_review():
    """promoted 플래그 고립 검증 — LLM 이 만든 연결이라도 인간이 승격하면 신뢰한다."""
    cands = _matcher(_graph(decided_by=BY_LLM, promoted=True)).search(REF)
    assert cands[0].needs_review is False


def test_llm_decided_link_needs_review():
    """아직 아무도 확인하지 않은 연결 위에서 코드가 mismatch 를 단정하지 않게 한다."""
    assert _matcher(_graph(decided_by=BY_LLM)).search(REF)[0].needs_review is True


def test_recall_score_is_carried_for_diagnostics():
    assert _matcher(_graph()).search(REF)[0].score == 0.83


def test_explain_missing_says_concept_not_similarity():
    """F7 의 ``missing`` 은 '유사도 임계 미달'이 아니라 '개념 연결 없음'이다.

    사유 문구가 임계값을 가리키면 사용자는 F7 경로에서 **사용되지 않는**
    ``match_min_score`` 를 조정하러 간다.
    """
    reason = _matcher(_graph(linked=False)).explain_missing(REF)
    assert "개념" in reason
    assert "유사도 임계" not in reason


def test_explain_missing_names_the_blocking_differs_by_edge():
    """차단한 ``differs_by`` 엣지의 축·사유를 실어 사람이 왜 안 비교됐는지 알게 한다."""
    graph = _graph(linked=False)
    graph.edges.append(ConceptEdge(
        DIFFERS_BY,
        FactRef("기준.xlsx", "fact-row-1"),
        FactRef("규격서.docx", "fact-word-11"),
        axis="측정조건", reason="저장 조건과 상시 환경 조건은 다른 규격",
    ))
    reason = _matcher(graph).explain_missing(REF)
    assert "표준환경온도" in reason
    assert "측정조건" in reason
    assert "저장 조건과 상시 환경 조건은 다른 규격" in reason


def test_explain_missing_ignores_edges_of_other_reference_facts():
    """다른 기준 fact 의 차단 사유가 섞이면 안 된다."""
    graph = _graph(linked=False)
    graph.edges.append(ConceptEdge(
        DIFFERS_BY,
        FactRef("기준.xlsx", "fact-row-99"),
        FactRef("규격서.docx", "fact-word-11"),
        axis="측정조건", reason="남의 사유",
    ))
    assert "남의 사유" not in _matcher(graph).explain_missing(REF)


def test_candidates_are_sorted_by_score_descending():
    """``FactComparator.compare()`` 는 ``candidates[0]`` 을 '가장 좋은 후보'로 쓴다.

    한 개념 노드에 같은 대상 문서 fact 가 둘 이상 들어가면 멤버 등록 순서 그대로
    돌려줄 경우 임의의 fact 가 1위가 된다. 여기서는 낮은 점수 멤버를 **먼저**
    등록해 두고, 반환이 점수순으로 뒤집히는지 본다.
    """
    members = [
        ConceptMember("기준.xlsx", "fact-row-1", "공칭용량"),
        ConceptMember("규격서.docx", "fact-word-11", "표준환경온도"),  # 낮은 점수, 먼저 등록
        ConceptMember("규격서.docx", "fact-word-7", "공칭용량"),       # 높은 점수, 나중 등록
    ]
    ref = FactRef("기준.xlsx", "fact-row-1")
    graph = ConceptGraph(
        nodes=[ConceptNode("c-0001", "공칭용량", members)],
        edges=[
            ConceptEdge(SAME_AS, ref, FactRef("규격서.docx", "fact-word-11"),
                        decided_by=BY_CODE, recall_score=0.31),
            ConceptEdge(SAME_AS, ref, FactRef("규격서.docx", "fact-word-7"),
                        decided_by=BY_CODE, recall_score=0.95),
        ],
    )
    cands = _matcher(graph).search(REF)
    assert [c.fact.fact_id for c in cands] == ["fact-word-7", "fact-word-11"]


def test_member_missing_from_target_facts_is_skipped():
    """그래프에는 있는데 대상 fact 목록에 없으면(부분 재실행) 조용히 건너뛴다."""
    assert _matcher(_graph(), facts=[OTHER]).search(REF) == []
