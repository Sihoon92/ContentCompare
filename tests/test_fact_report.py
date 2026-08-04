"""fact 전용 리포트 렌더러 테스트.

리포트의 목적은 판정을 통보하는 것이 아니라 **사람이 원문으로 확인**하게 하는 것이다.
그래서 양측 근거 원문과 좌표가 반드시 실려야 한다(계획 §6.2).
"""

from __future__ import annotations

from contentcompare.fact.fact_comparator import FactComparison
from contentcompare.fact.fact_models import Fact
from contentcompare.fact.record_models import Attribute
from contentcompare.report.fact_report import format_source, render_fact_markdown


def _fact(name, evidence, source, **attrs):
    return Fact(
        fact_id=name,
        entity_name=name,
        attributes={k: Attribute(*v) for k, v in attrs.items()},
        evidence_text=evidence,
        source=source,
    )


_REF = _fact(
    "충전환경온도", "충전환경온도, -5, 35, 85",
    {"doc_type": "excel", "sheet": "데이터", "row": 17, "cell_range": "E17:H17"},
    upper_limit=(85, ""),
)
_TGT = _fact(
    "충전환경온도", "충전환경온도, -5, 35, 80, ℃",
    {"doc_type": "ppt", "slide_no": 2, "shape_ids": ["p002_s002"], "from_notes": True},
    upper_limit=(80, "℃"),
)


def _render(comparisons, **kw):
    return render_fact_markdown(
        comparisons, reference_doc="자표준문서.xlsx", target_docs=["발표.pptx"], **kw
    )


def test_mismatch_shows_both_sides_evidence_and_location():
    md = _render([FactComparison(
        reference_fact=_REF, target_doc="발표.pptx", result="mismatch",
        mismatch_attributes=["upper_limit"], target_fact=_TGT,
        match_score=0.81, match_method="embed", decided_by="code",
        reason="값이 다릅니다: upper_limit(기준 85 vs 대상 80℃)",
    )])
    assert "❌ 불일치" in md and "upper_limit" in md
    # 양측 원문 인용
    assert "충전환경온도, -5, 35, 85" in md and "충전환경온도, -5, 35, 80, ℃" in md
    # 양측 좌표 — 사람이 원문을 찾아갈 수 있어야 한다
    assert "데이터!E17:H17" in md
    assert "슬라이드 2" in md and "p002_s002" in md and "스피커노트 포함" in md
    # 판정 주체와 매칭 근거도 보인다
    assert "판정 주체: code" in md and "embed" in md


def test_missing_renders_placeholder_row():
    md = _render([FactComparison(
        reference_fact=_REF, target_doc="발표.pptx", result="missing",
        reason="대응하는 내용을 찾지 못했습니다.",
    )])
    assert "⚪ 대상에 없음" in md and "(대응 내용 없음)" in md


def test_overview_counts_and_llm_delegation_rate():
    items = [
        FactComparison(reference_fact=_REF, target_doc="발표.pptx", result="match"),
        FactComparison(reference_fact=_REF, target_doc="발표.pptx", result="unknown",
                       decided_by="llm"),
    ]
    md = _render(items)
    assert "총 2건 판정" in md
    assert "✅ 일치 1건" in md and "❓ 판단보류 1건" in md
    assert "LLM 위임률 50%" in md


def test_details_sorted_by_urgency():
    """확인이 필요한 것부터 — 불일치 → 보류 → 없음 → 일치."""
    order = ["match", "missing", "unknown", "mismatch"]
    md = _render([
        FactComparison(reference_fact=_fact(r, "e", {}), target_doc="발표.pptx", result=r)
        for r in order
    ])
    assert "### 1. mismatch — ❌ 불일치" in md
    assert "### 2. unknown — ❓ 판단보류" in md
    assert "### 3. missing — ⚪ 대상에 없음" in md
    assert "### 4. match — ✅ 일치" in md


def test_per_target_breakdown_when_multiple_targets():
    md = render_fact_markdown(
        [
            FactComparison(reference_fact=_REF, target_doc="a.docx", result="match"),
            FactComparison(reference_fact=_REF, target_doc="b.pptx", result="mismatch"),
        ],
        reference_doc="기준.xlsx", target_docs=["a.docx", "b.pptx"],
    )
    assert "| 대상 문서 |" in md and "| a.docx |" in md and "| b.pptx |" in md


def test_run_stats_section_is_optional():
    md = _render([FactComparison(reference_fact=_REF, target_doc="발표.pptx", result="match")],
                 stats={"comparisons": 1, "llm_calls": 0})
    assert "## 실행 정보" in md and "llm_calls: 0" in md


def test_format_source_covers_all_doc_types():
    assert format_source({"doc_type": "excel", "sheet": "S", "cell_range": "A1:C1"}) == "S!A1:C1"
    assert format_source({"doc_type": "excel", "sheet": "S", "row": 5}) == "S!행 5"
    assert format_source({"doc_type": "word", "block_ids": ["w_b005"]}) == "블록 w_b005"
    assert format_source({}) == "-"


# --------------------------------------------------------------------- #
# F7 개념 그래프 표시
# --------------------------------------------------------------------- #
from contentcompare.fact.concept_models import (  # noqa: E402
    BY_LLM,
    DIFFERS_BY,
    SAME_AS,
    UNKNOWN as REL_UNKNOWN,
    ConceptEdge,
    ConceptGraph,
    ConceptMember,
    ConceptNode,
    FactRef,
)


def _unknown_graph() -> ConceptGraph:
    members = [ConceptMember("기준.xlsx", "fact-row-20", "1개월저장온도"),
               ConceptMember("규격서.docx", "fact-word-11", "표준환경온도")]
    edge = ConceptEdge(
        REL_UNKNOWN,
        FactRef("기준.xlsx", "fact-row-20"),
        FactRef("규격서.docx", "fact-word-11"),
        reason="LLM 이 판단하지 못했습니다", decided_by=BY_LLM,
    )
    return ConceptGraph(
        nodes=[ConceptNode("c-0001", "1개월저장온도", members[:1]),
               ConceptNode("c-0002", "표준환경온도", members[1:])],
        edges=[edge],
    )


def _clean_graph() -> ConceptGraph:
    """판정된 엣지만 있고 UNKNOWN 엣지는 없는 그래프 — 검토 필요 섹션이 나타나면 안 된다."""
    same_members = [ConceptMember("기준.xlsx", "fact-row-1", "저장온도"),
                    ConceptMember("규격서.docx", "fact-word-2", "저장온도")]
    diff_members = [ConceptMember("기준.xlsx", "fact-row-3", "충전전압"),
                    ConceptMember("규격서.docx", "fact-word-4", "전압")]
    same_edge = ConceptEdge(
        SAME_AS,
        FactRef("기준.xlsx", "fact-row-1"),
        FactRef("규격서.docx", "fact-word-2"),
        reason="표기가 다르지만 같은 항목", decided_by="code",
    )
    diff_edge = ConceptEdge(
        DIFFERS_BY,
        FactRef("기준.xlsx", "fact-row-3"),
        FactRef("규격서.docx", "fact-word-4"),
        reason="단위가 다릅니다", decided_by="code",
    )
    return ConceptGraph(
        nodes=[ConceptNode("c-0001", "저장온도", same_members[:1]),
               ConceptNode("c-0002", "저장온도", same_members[1:]),
               ConceptNode("c-0003", "충전전압", diff_members[:1]),
               ConceptNode("c-0004", "전압", diff_members[1:])],
        edges=[same_edge, diff_edge],
    )


def test_review_section_lists_unresolved_pairs():
    """판정 못 한 쌍은 사람에게 보여야 한다 — 그래야 승격으로 이어진다."""
    md = render_fact_markdown([], reference_doc="기준.xlsx", target_docs=["규격서.docx"],
                              graph=_unknown_graph())
    assert "검토 필요" in md
    assert "1개월저장온도" in md and "표준환경온도" in md
    assert "knowledge/ontology.yaml" in md


def test_review_section_flags_rejected_edges():
    """강등된 엣지는 '검증에 실패해 거부된 주장'임이 표에서 보여야 한다.

    사유만 보이면 사람이 LLM 의 "같은 항목이다"를 믿고 승격해, 게이트가 막았던
    잘못된 연결을 영구화한다.
    """
    graph = _unknown_graph()
    graph.edges[0].rejected_by = "evidence"
    graph.edges[0].reason = "[거부됨: 근거 인용이 원문에 없음] 거부된 주장: 둘 다 같은 규격"
    md = render_fact_markdown([], reference_doc="기준.xlsx", target_docs=["규격서.docx"],
                              graph=graph)
    assert "거부 사유" in md
    assert "근거 인용이 원문에 없음" in md


def test_review_section_flattens_multiline_reasons():
    """LLM 사유에 개행이 들어가도 표가 깨지지 않아야 한다."""
    graph = _unknown_graph()
    graph.edges[0].reason = "첫 줄\n둘째 줄"
    md = render_fact_markdown([], reference_doc="기준.xlsx", target_docs=["규격서.docx"],
                              graph=graph)
    row = [ln for ln in md.splitlines() if "첫 줄" in ln]
    assert len(row) == 1 and "둘째 줄" in row[0]


def test_no_review_section_when_graph_is_clean():
    """해결된 엣지만 있으면(SAME_AS/DIFFERS_BY) 검토 필요 섹션이 나타나지 않는다."""
    md = render_fact_markdown([], reference_doc="기준.xlsx", target_docs=["규격서.docx"],
                              graph=_clean_graph())
    assert "검토 필요" not in md


def test_report_renders_without_graph():
    """기존 호출부(그래프 없음)가 그대로 동작해야 한다."""
    md = render_fact_markdown([], reference_doc="기준.xlsx", target_docs=["규격서.docx"])
    assert "기준.xlsx" in md
