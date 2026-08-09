"""fact 엔진 전용 Markdown 리포트 렌더러(F6).

현행 RAG 렌더러(:mod:`markdown_report`)와 **별개**다. fact 결과는 RAG 보다 정보가
많아서(속성별 불일치, 양측 근거 원문 + 정확한 좌표, 코드/LLM 판정 구분) 같은 틀에
욱여넣으면 그 정보가 사라진다.

리포트의 목적은 "다르다"고 말하는 것이 아니라 **사람이 원문으로 확인할 수 있게**
하는 것이다(§6.2 양측 evidence 인용 필수). 그래서 모든 항목에 기준/대상의 원문과
좌표를 나란히 싣는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

from ..fact.fact_comparator import MATCH, MISMATCH, MISSING, UNKNOWN, FactComparison
from ..fact.fact_models import Fact

LABEL = {
    MATCH: "✅ 일치",
    MISMATCH: "❌ 불일치",
    MISSING: "⚪ 대상에 없음",
    UNKNOWN: "❓ 판단보류",
}
"""판정 라벨의 **단일 출처**. 화면(``ui/micro_world``·``ui/runner``)도 이것을 쓴다 —
리포트와 화면의 문구가 갈라지면 사람이 둘을 대조할 수 없다."""

ORDER = (MISMATCH, UNKNOWN, MISSING, MATCH)
"""확인이 필요한 것부터 보여준다. 이 순서가 리포트의 설계 철학이다."""

# 하위호환(내부 호출부).
_LABEL = LABEL
_ORDER = ORDER


def render_fact_markdown(
    comparisons: list[FactComparison],
    *,
    reference_doc: str,
    target_docs: list[str],
    stats: Optional[dict] = None,
    graph: Any = None,
) -> str:
    lines: list[str] = [
        "# 문서 비교 리포트 (fact 엔진)",
        "",
        f"- 생성 시각: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- 기준 문서: `{reference_doc}`",
        f"- 대상 문서: {', '.join(f'`{d}`' for d in target_docs)}",
        "",
    ]
    lines += _budget_warning(graph)
    lines += _overview(comparisons)
    lines += _summary_table(comparisons)
    lines += _details(comparisons)
    if stats:
        lines += _run_stats(stats)
    lines += _review_section(graph)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
def _counts(items: Iterable[FactComparison]) -> dict[str, int]:
    out = {k: 0 for k in _LABEL}
    for c in items:
        out[c.result] = out.get(c.result, 0) + 1
    return out


def _overview(comparisons: list[FactComparison]) -> list[str]:
    counts = _counts(comparisons)
    total = len(comparisons)
    lines = [
        f"- 총 {total}건 판정 — "
        + ", ".join(f"{_LABEL[k]} {counts.get(k, 0)}건" for k in _ORDER),
    ]
    by_llm = sum(1 for c in comparisons if c.decided_by == "llm")
    if total:
        lines.append(
            f"- 판정 주체: 코드 {total - by_llm}건 / LLM {by_llm}건 "
            f"(LLM 위임률 {by_llm / total:.0%})"
        )

    # 대상 문서가 여럿이면 문서별로도 쪼개 보여준다.
    docs = sorted({c.target_doc for c in comparisons})
    if len(docs) > 1:
        lines += ["", "| 대상 문서 | " + " | ".join(_LABEL[k] for k in _ORDER) + " |",
                  "|---|" + "---|" * len(_ORDER)]
        for doc in docs:
            c = _counts(x for x in comparisons if x.target_doc == doc)
            lines.append(f"| {doc} | " + " | ".join(str(c.get(k, 0)) for k in _ORDER) + " |")
    lines.append("")
    return lines


def _summary_table(comparisons: list[FactComparison]) -> list[str]:
    lines = [
        "## 요약",
        "",
        "| # | 기준 항목 | 대상 문서 | 판정 | 어긋난 속성 | 사유 |",
        "|---|---|---|---|---|---|",
    ]
    for i, c in enumerate(_sorted(comparisons), start=1):
        lines.append(
            f"| {i} | {c.reference_fact.entity_name} | {c.target_doc} "
            f"| {_LABEL.get(c.result, c.result)} "
            f"| {', '.join(c.mismatch_attributes) or '-'} "
            f"| {_truncate(_oneline(c.reason), 70)} |"
        )
    lines.append("")
    return lines


def _details(comparisons: list[FactComparison]) -> list[str]:
    lines = ["## 상세", ""]
    for i, c in enumerate(_sorted(comparisons), start=1):
        title = f"### {i}. {c.reference_fact.entity_name} — {_LABEL.get(c.result, c.result)}"
        if c.mismatch_attributes:
            title += f" ({', '.join(c.mismatch_attributes)})"
        lines += [title, "", f"**대상 문서**: {c.target_doc}", ""]
        lines += [
            "| | 값 | 근거 원문 | 위치 |",
            "|---|---|---|---|",
            _side_row("기준", c.reference_fact),
            _side_row(c.target_doc, c.target_fact),
            "",
            f"**판단 근거**: {c.reason}",
            "",
            f"<sub>판정 주체: {c.decided_by} · 매칭: {c.match_method} "
            f"{c.match_score:.3f}</sub>",
            "",
        ]
    return lines


def _side_row(label: str, fact: Optional[Fact]) -> str:
    if fact is None:
        return f"| {label} | - | (대응 내용 없음) | - |"
    values = ", ".join(
        f"{k}={a.value}{(' ' + a.unit) if a.unit else ''}" for k, a in fact.attributes.items()
    ) or "-"
    quote = f"“{_truncate(_oneline(fact.evidence_text), 60)}”" if fact.evidence_text else "-"
    return f"| {label} | {values} | {quote} | {format_source(fact.source)} |"


def format_source(source: dict) -> str:
    """source dict → 사람이 원문을 찾아갈 수 있는 위치 문자열."""
    if not source:
        return "-"
    doc_type = source.get("doc_type")
    if doc_type == "excel":
        sheet = source.get("sheet") or ""
        where = source.get("cell_range") or (f"행 {source.get('row')}" if source.get("row") else "")
        return f"{sheet}!{where}" if sheet and where else (where or sheet or "-")
    if doc_type == "word":
        ids = source.get("block_ids") or []
        return f"블록 {', '.join(map(str, ids))}" if ids else "-"
    if doc_type == "ppt":
        parts = []
        if source.get("slide_no") is not None:
            parts.append(f"슬라이드 {source['slide_no']}")
        if source.get("shape_ids"):
            parts.append(f"도형 {', '.join(map(str, source['shape_ids']))}")
        if source.get("from_notes"):
            parts.append("스피커노트 포함")
        return " / ".join(parts) or "-"
    return "-"


def _run_stats(stats: dict) -> list[str]:
    lines = ["## 실행 정보", ""]
    for key, value in stats.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    return lines


def _sorted(comparisons: list[FactComparison]) -> list[FactComparison]:
    """확인이 필요한 판정(불일치 → 보류 → 없음 → 일치) 순으로 정렬한다."""
    rank = {k: i for i, k in enumerate(_ORDER)}
    return sorted(
        comparisons,
        key=lambda c: (rank.get(c.result, len(_ORDER)), c.target_doc, c.reference_fact.entity_name),
    )


def _oneline(text: str) -> str:
    return " ".join(str(text or "").split())


def _truncate(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _budget_warning(graph: Any) -> list[str]:
    """개념 판정 예산이 모자라면 **맨 위에** 크게 알린다.

    남은 배치가 전부 ``unknown`` → 연결 없음 → 전 항목 ``missing`` 으로 귀결되는데,
    그대로 두면 사용자가 보는 것은 "전부 대상에 없음"과 거대한 검토 필요 표뿐이고
    원인은 로그 warning 에만 남는다.
    """
    if graph is None:
        return []
    try:
        pending = int((getattr(graph, "stats", None) or {}).get("budget_exhausted_pairs") or 0)
    except (TypeError, ValueError):
        return []
    if pending <= 0:
        return []
    return [
        f"> 🚨 **개념 판정 예산이 부족해 {pending} 쌍을 판정하지 못했습니다.**",
        ">",
        "> 판정하지 못한 쌍은 개념이 이어지지 않으므로, 해당 기준 항목이 실제와 무관하게",
        "> `⚪ 대상에 없음`으로 보고됐을 수 있습니다. 아래 결과를 그대로 믿지 마세요.",
        ">",
        "> 조치: `fact.max_llm_calls_per_concept` 를 늘리거나 "
        "`fact.concept_batch_pairs` 를 키운 뒤 다시 실행하세요.",
        "",
    ]


def _review_section(graph: Any) -> list[str]:
    """관계를 판정하지 못한 쌍 — 사람이 확인해 온톨로지로 승격하면 영구히 해결된다.

    **거부 사유 열이 중요하다.** 검증에 실패해 강등된 엣지는 LLM 이 쓴 "이 둘은 같은
    항목이다"라는 사유를 달고 있다. 표만 보고도 "이건 거부된 주장"임을 알 수 없으면
    사람이 그것을 믿고 승격해 게이트를 우회시킨다.
    """
    from ..fact.concept_assembler import REJECT_NOTES
    from ..fact.concept_models import UNKNOWN as REL_UNKNOWN

    if graph is None:
        return []
    pending = [e for e in graph.edges if e.relation == REL_UNKNOWN]
    if not pending:
        return []

    labels = {(m.doc, m.fact_id): m.entity_name
              for n in graph.nodes for m in n.members}
    lines = [
        "",
        "## ⚠ 검토 필요 — 같은 항목인지 판정하지 못한 쌍",
        "",
        "확인 후 `knowledge/ontology.yaml` 에 `same_as` 또는 `differs_by` 로 적어두면",
        "다음 실행부터 이 쌍은 다시 묻지 않습니다.",
        "",
        "| 기준 항목 | 대상 항목 | 거부 사유 | 사유 |",
        "|---|---|---|---|",
    ]
    for edge in pending:
        left = labels.get((edge.left.doc, edge.left.fact_id), edge.left.fact_id)
        right = labels.get((edge.right.doc, edge.right.fact_id), edge.right.fact_id)
        rejected = REJECT_NOTES.get(edge.rejected_by, edge.rejected_by) or "-"
        reason = _truncate(_oneline(edge.reason), 80) or "판정 보류"
        lines.append(f"| {_oneline(left)} | {_oneline(right)} | {rejected} | {reason} |")
    return lines
