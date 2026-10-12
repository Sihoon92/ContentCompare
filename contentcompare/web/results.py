"""파이프라인 결과 → 화면용 JSON.

표를 만드는 로직은 :mod:`contentcompare.ui.runner` 를 그대로 쓴다(UI 3층 분리 — 화면이
바뀌어도 집계는 한 곳). 판정 라벨은 RAG=``runner.VERDICT_LABEL``, fact=``runner.FACT_LABEL``
이며 **섞지 않는다** — 같은 이모지, 다른 뜻이다.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ..models import RecordResult, Verdict
from ..report import render_markdown
from ..ui import runner


def rag_payload(results: list) -> dict:
    counts = runner.verdict_counts(results)
    details = []
    for i, r in enumerate(results, start=1):
        is_record = isinstance(r, RecordResult)
        details.append({
            "index": i,
            "label": runner.VERDICT_LABEL[r.verdict],
            "verdict": r.verdict.value,
            "source_label": r.reference.source_label,
            "reference_text": r.reference.text,
            "is_record": is_record,
            "sources": list(r.sources),
            "reasoning": r.reasoning,
            "fields": runner.field_rows(r) if is_record else [],
            "candidates": [
                {"score": round(float(c.score), 3),
                 "source_label": c.item.source_label,
                 "matched": c.item.item_id in r.matched_item_ids}
                for c in r.candidates
            ],
        })
    return _jsonable({
        "engine": "rag",
        "counts": [{"key": v.value, "label": runner.VERDICT_LABEL[v], "n": counts[v]}
                   for v in Verdict],
        "summary": runner.summary_rows(results),
        "details": details,
    })


def fact_payload(result: Any) -> dict:
    counts = runner.fact_verdict_counts(result.comparisons)
    return _jsonable({
        "engine": "fact",
        "counts": [{"key": k, "label": runner.FACT_LABEL.get(k, k), "n": n}
                   for k, n in counts.items()],
        "summary": runner.fact_summary_rows(result.comparisons),
        "failed_docs": [
            {"name": os.path.basename(str(s.get("path", ""))), "error": str(s.get("error", ""))}
            for s in result.failed_docs
        ],
        "compare_stats": result.compare_stats or {},
    })


def rag_markdown(results: list, reference: str, targets: list[str]) -> str:
    """RAG 리포트. 경로는 파일 이름만 — 서버의 작업 폴더 경로를 화면에 흘리지 않는다."""
    return render_markdown(
        results,
        reference_doc=os.path.basename(reference),
        target_docs=[os.path.basename(t) for t in targets],
    )


def _jsonable(obj: Any) -> Any:
    """JSON 으로 못 바꾸는 값(enum·Path 등)은 문자열로 — 응답이 직렬화에서 죽지 않게."""
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str))
