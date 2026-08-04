"""F7 개념 그래프 데이터 모델 테스트 — 순수 자료구조(LLM/네트워크 불필요)."""

from contentcompare.fact.concept_models import (
    BY_CODE,
    DIFFERS_BY,
    SAME_AS,
    ConceptEdge,
    ConceptGraph,
    ConceptMember,
    ConceptNode,
    FactRef,
)


def _graph() -> ConceptGraph:
    node = ConceptNode(
        concept_id="c-0001",
        label="공칭용량",
        members=[
            ConceptMember(doc="기준.xlsx", fact_id="fact-row-1", entity_name="공칭용량"),
            ConceptMember(doc="규격서.docx", fact_id="fact-word-7", entity_name="공칭용량"),
        ],
    )
    edge = ConceptEdge(
        relation=SAME_AS,
        left=FactRef("기준.xlsx", "fact-row-1"),
        right=FactRef("규격서.docx", "fact-word-7"),
        left_text="1150",
        right_text="공칭용량 1150 mAh",
        reason="같은 항목",
        decided_by=BY_CODE,
    )
    return ConceptGraph(nodes=[node], edges=[edge], stats={"same_as": 1})


def test_fact_ref_key_joins_doc_and_id():
    assert FactRef("a.xlsx", "fact-1").key == "a.xlsx#fact-1"


def test_pair_key_is_direction_independent():
    a, b = FactRef("x.xlsx", "f1"), FactRef("y.docx", "f2")
    assert ConceptEdge(SAME_AS, a, b).pair_key == ConceptEdge(SAME_AS, b, a).pair_key


def test_node_id_of_finds_member():
    g = _graph()
    assert g.node_id_of("규격서.docx", "fact-word-7") == "c-0001"
    assert g.node_id_of("규격서.docx", "없는id") is None


def test_partners_returns_only_requested_document():
    """기준 fact 와 같은 개념에 속한 **그 대상 문서**의 fact 만 후보가 된다."""
    g = _graph()
    partners = g.partners("기준.xlsx", "fact-row-1", "규격서.docx")
    assert [m.fact_id for m in partners] == ["fact-word-7"]
    assert g.partners("기준.xlsx", "fact-row-1", "발표.pptx") == []


def test_partners_excludes_self_document():
    g = _graph()
    assert g.partners("기준.xlsx", "fact-row-1", "기준.xlsx") == []


def test_edge_of_is_direction_independent():
    g = _graph()
    a, b = FactRef("기준.xlsx", "fact-row-1"), FactRef("규격서.docx", "fact-word-7")
    assert g.edge_of(a, b) is g.edge_of(b, a)
    assert g.edge_of(a, FactRef("규격서.docx", "다른id")) is None


def test_round_trip_serialization():
    g = _graph()
    restored = ConceptGraph.from_dict(g.to_dict())
    assert restored.to_dict() == g.to_dict()
    assert restored.node_id_of("기준.xlsx", "fact-row-1") == "c-0001"


def test_from_dict_tolerates_garbage():
    """저장된 산출물이 손상돼도 죽지 않는다(다른 fact 모델과 같은 방어 수준)."""
    g = ConceptGraph.from_dict({"nodes": [{}, None], "edges": ["x", {}]})
    assert g.nodes and g.nodes[0].concept_id == ""
    assert len(g.edges) == 1


def test_differs_by_edge_carries_axis():
    e = ConceptEdge(
        DIFFERS_BY, FactRef("a", "1"), FactRef("b", "2"), axis="측정조건",
    )
    assert e.to_dict()["axis"] == "측정조건"
