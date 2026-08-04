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
