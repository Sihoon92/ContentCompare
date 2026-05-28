"""Markdown 리포트 렌더러.

요약 표 + 항목별 상세 섹션을 생성한다.
"""

from __future__ import annotations

from datetime import datetime

from ..models import ComparisonResult, Verdict

_VERDICT_LABEL = {
    Verdict.SAME: "✅ 같음",
    Verdict.PARTIAL: "🟡 부분일치",
    Verdict.DIFFERENT: "❌ 다름",
    Verdict.NOT_FOUND: "⚪ 미발견",
}


def render_markdown(
    results: list[ComparisonResult],
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
    lines.append("| # | 기준 항목 | 판정 | 출처 | 한줄 사유 |")
    lines.append("|---|-----------|------|------|-----------|")
    for i, r in enumerate(results, start=1):
        sources = "<br>".join(r.sources) if r.sources else "-"
        lines.append(
            f"| {i} | {_truncate(r.reference.text, 40)} "
            f"| {_VERDICT_LABEL[r.verdict]} | {sources} "
            f"| {_truncate(_oneline(r.reasoning), 60)} |"
        )
    lines.append("")

    # 상세
    lines.append("## 상세")
    lines.append("")
    for i, r in enumerate(results, start=1):
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

    return "\n".join(lines)


def _summary_counts(results: list[ComparisonResult]) -> str:
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
