"""F7 그래프 무결성 검증 테스트 — 코드가 위상만 본다."""

from contentcompare.fact.concept_models import (
    DIFFERS_BY,
    SAME_AS,
    UNKNOWN,
    ConceptEdge,
    ConceptGraph,
    ConceptMember,
    ConceptNode,
    FactRef,
)
from contentcompare.fact.validator import ERROR, WARN, validate_graph

A = FactRef("기준.xlsx", "fact-row-1")
B = FactRef("규격서.docx", "fact-word-7")


def _graph(edges, members=None) -> ConceptGraph:
    members = members or [
        ConceptMember("기준.xlsx", "fact-row-1", "공칭용량"),
        ConceptMember("규격서.docx", "fact-word-7", "공칭용량"),
    ]
    return ConceptGraph(nodes=[ConceptNode("c-0001", "공칭용량", members)], edges=edges)


def _checks(report, name):
    return [c for c in report.checks if c.check == name]


def test_clean_graph_has_no_findings():
    report = validate_graph(_graph([ConceptEdge(SAME_AS, A, B, left_text="x", right_text="y")]))
    assert report.checks == []


def test_rejected_evidence_is_error():
    edge = ConceptEdge(UNKNOWN, A, B, rejected_by="evidence")
    report = validate_graph(_graph([edge]))
    found = _checks(report, "concept_evidence_missing")
    assert len(found) == 1 and found[0].severity == ERROR


def test_merge_violation_is_error():
    edge = ConceptEdge(UNKNOWN, A, B, rejected_by="differs_by")
    assert _checks(validate_graph(_graph([edge])), "concept_merge_violation")


def test_contradicting_pair_is_error():
    edges = [ConceptEdge(SAME_AS, A, B, left_text="x", right_text="y"),
             ConceptEdge(DIFFERS_BY, A, B, axis="기간")]
    found = _checks(validate_graph(_graph(edges)), "concept_contradiction")
    assert len(found) == 1 and found[0].severity == ERROR


def test_dangling_node_reference_is_error():
    edge = ConceptEdge(SAME_AS, A, FactRef("규격서.docx", "없는id"),
                       left_text="x", right_text="y")
    assert _checks(validate_graph(_graph([edge])), "concept_dangling_node")


def test_unknown_pair_is_warn_for_human_review():
    edge = ConceptEdge(UNKNOWN, A, B, reason="LLM 이 판단하지 못함")
    found = _checks(validate_graph(_graph([edge])), "concept_unknown_pair")
    assert len(found) == 1 and found[0].severity == WARN


def test_rejected_edge_is_not_double_counted_as_unknown():
    """강등된 엣지는 그 사유로만 보고한다."""
    edge = ConceptEdge(UNKNOWN, A, B, rejected_by="evidence")
    report = validate_graph(_graph([edge]))
    assert _checks(report, "concept_unknown_pair") == []


def test_report_aggregates_by_check():
    edges = [ConceptEdge(UNKNOWN, A, B, rejected_by="evidence"),
             ConceptEdge(UNKNOWN, B, A, reason="보류")]
    data = validate_graph(_graph(edges)).to_dict()
    assert data["by_check"]["concept_evidence_missing"] == 1
    assert data["overall"]["error"] == 1
