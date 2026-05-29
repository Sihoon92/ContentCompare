"""Markdown 리포트 렌더러.

요약 표 + 항목별 상세 섹션을 생성한다. 기준 항목이 엑셀 레코드(:class:`RecordResult`)면
필드별 판정 표를, 단순 항목(:class:`ComparisonResult`)이면 후보/사유를 보여준다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Union

from ..models import ComparisonResult, RecordResult, Verdict

Result = Union[ComparisonResult, RecordResult]

_VERDICT_LABEL = {
    Verdict.SAME: "✅ 같음",
    Verdict.PARTIAL: "🟡 부분일치",
    Verdict.DIFFERENT: "❌ 다름",
    Verdict.NOT_FOUND: "⚪ 미발견",
}


def render_markdown(
    results: list[Result],
    *,
    reference_doc: str,
    target_docs: list[str],
) -> str:
    lines: list[str] = []
    lines.append("# 문서 비교 리포트")
    lines.append("")
    lines.append(f"- 생성 시각: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"- 기준 문서: `{reference_doc}`")
    lines.append(f"- 대상 문서: {', '.join(f'`{d}`' for d in target_docs)}")
    lines.append("")
    lines.append(_summary_counts(results))
    lines.append("")

    # 요약 표
    lines.append("## 요약")
    lines.append("")
    lines.append("| # | 기준 항목 | 판정 | 출처 | 한줄 요약 |")
    lines.append("|---|-----------|------|------|-----------|")
    for i, r in enumerate(results, start=1):
        sources = "<br>".join(r.sources) if r.sources else "-"
        lines.append(
            f"| {i} | {_truncate(r.reference.text, 40)} "
            f"| {_VERDICT_LABEL[r.verdict]} | {sources} "
            f"| {_truncate(_oneline_summary(r), 60)} |"
        )
    lines.append("")

    # 상세
    lines.append("## 상세")
    lines.append("")
    for i, r in enumerate(results, start=1):
        if isinstance(r, RecordResult):
            _render_record(lines, i, r)
        else:
            _render_comparison(lines, i, r)

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
def _render_record(lines: list[str], i: int, r: RecordResult) -> None:
    lines.append(f"### {i}. {r.record.source_label} — {_VERDICT_LABEL[r.verdict]}")
    lines.append("")
    lines.append(f"**기준 내용**: {r.record.text}")
    lines.append("")

    # 필드별 판정 표
    lines.append("| 필드 | 기준값 | 판정 | 출처 | 사유 |")
    lines.append("|------|--------|------|------|------|")
    by_id = {c.item.item_id: c.item.source_label for c in r.candidates}
    for fr in r.fields:
        srcs = [by_id[m] for m in fr.matched_item_ids if m in by_id]
        src_txt = "<br>".join(srcs) if srcs else "-"
        lines.append(
            f"| {fr.field.header} | {_truncate(fr.field.value_norm, 24)} "
            f"| {_VERDICT_LABEL[fr.verdict]} | {src_txt} "
            f"| {_truncate(_oneline(fr.reasoning), 70)} |"
        )
    lines.append("")

    if r.candidates:
        lines.append("**검색된 후보**:")
        for c in r.candidates:
            mark = " ⟵ 매칭" if c.item.item_id in r.matched_item_ids else ""
            lines.append(
                f"- ({c.score:.3f}) {c.item.source_label}{mark}: "
                f"{_truncate(c.item.text, 120)}"
            )
        lines.append("")


def _render_comparison(lines: list[str], i: int, r: ComparisonResult) -> None:
    lines.append(f"### {i}. {r.reference.source_label} — {_VERDICT_LABEL[r.verdict]}")
    lines.append("")
    lines.append(f"**기준 내용**: {r.reference.text}")
    lines.append("")
    if r.candidates:
        lines.append("**검색된 후보**:")
        for c in r.candidates:
            mark = " ⟵ 매칭" if c.item.item_id in r.matched_item_ids else ""
            lines.append(
                f"- ({c.score:.3f}) {c.item.source_label}{mark}: "
                f"{_truncate(c.item.text, 120)}"
            )
        lines.append("")
    lines.append(f"**판단 근거**: {r.reasoning}")
    lines.append("")


def _oneline_summary(r: Result) -> str:
    """요약 표의 한줄 요약. 레코드는 필드 일치 개수, 단일 항목은 사유."""
    if isinstance(r, RecordResult):
        total = len(r.fields)
        same = sum(1 for f in r.fields if f.verdict == Verdict.SAME)
        return f"필드 {same}/{total} 일치"
    return r.reasoning


def _summary_counts(results: list[Result]) -> str:
    counts = {v: 0 for v in Verdict}
    for r in results:
        counts[r.verdict] += 1
    parts = [f"{_VERDICT_LABEL[v]} {counts[v]}건" for v in Verdict]
    return f"- 총 {len(results)}개 항목 — " + ", ".join(parts)


def _oneline(text: str) -> str:
    return " ".join(text.split())


def _truncate(text: str, n: int) -> str:
    text = _oneline(text)
    return text if len(text) <= n else text[: n - 1] + "…"
