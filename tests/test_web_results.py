"""결과 JSON — 두 엔진의 라벨을 섞지 않고 JSON 으로 직렬화된다."""

from __future__ import annotations

import json

from contentcompare.fact.pipeline import FactRunResult
from contentcompare.models import (
    Candidate, ComparisonResult, DocItem, DocType, FieldClaim, FieldFinding,
    RecordItem, RecordResult, Verdict,
)
from contentcompare.web import results as R


def _item(item_id, text="내용"):
    return DocItem(item_id=item_id, doc_id="d", doc_type=DocType.WORD, text=text,
                   source_label=f"라벨 {item_id}")


def test_rag_payload_lists_counts_details_and_candidates():
    simple = ComparisonResult(
        reference=_item("ref-1", "매출 100억"), verdict=Verdict.SAME, reasoning="같음",
        candidates=[Candidate(_item("t-1"), 0.91234), Candidate(_item("t-2"), 0.5)],
        matched_item_ids=["t-1"])
    claim = FieldClaim(field_id="f1", header="하한치", value_raw=-5, value_norm="-5",
                       cell_ref="F2")
    record = RecordResult(
        record=RecordItem(item_id="row-2", doc_id="d", doc_type=DocType.EXCEL,
                          text="충전온도", source_label="기준 2행", fields=[claim]),
        verdict=Verdict.DIFFERENT, reasoning="다름",
        findings=[FieldFinding(field=claim, found=False, note="없음")])
    payload = R.rag_payload([simple, record])
    json.dumps(payload, ensure_ascii=False)
    counts = {c["key"]: c["n"] for c in payload["counts"]}
    assert counts["same"] == 1 and counts["different"] == 1
    first, second = payload["details"]
    assert first["label"] == "✅ 같음" and not first["is_record"]
    assert first["candidates"] == [
        {"score": 0.912, "source_label": "라벨 t-1", "matched": True},
        {"score": 0.5, "source_label": "라벨 t-2", "matched": False}]
    assert first["sources"] == ["라벨 t-1"]
    assert second["is_record"] and second["fields"][0]["항목(열)"] == "하한치"
    assert len(payload["summary"]) == 2


def test_fact_payload_uses_fact_labels_and_failed_docs():
    result = FactRunResult(
        summaries=[{"path": "C:/x/기준.xlsx", "status": "ok"},
                   {"path": "C:/x/깨진.docx", "status": "error", "error": "OSError: 열 수 없음"}],
        comparisons=[{"result": "match", "entity_name": "충전온도", "target_doc": "a.docx",
                      "reason": "일치"},
                     {"result": "missing", "entity_name": "보관온도", "target_doc": "a.docx",
                      "reason": "없음"}],
        compare_stats={"comparisons": 2, "llm": {"calls": 0}},
        markdown="# 리포트")
    payload = R.fact_payload(result)
    json.dumps(payload, ensure_ascii=False)
    assert payload["engine"] == "fact"
    counts = {c["key"]: c["n"] for c in payload["counts"]}
    assert counts["match"] == 1 and counts["missing"] == 1
    assert payload["failed_docs"] == [{"name": "깨진.docx", "error": "OSError: 열 수 없음"}]
    assert payload["summary"][0]["판정"]  # 리포트와 같은 라벨(단일 출처)
    assert payload["compare_stats"]["comparisons"] == 2


def test_rag_markdown_uses_basenames():
    md = R.rag_markdown([], "C:/jobs/x/inputs/reference/기준.xlsx",
                        ["C:/jobs/x/inputs/targets/자료/a.docx"])
    assert "기준.xlsx" in md and "C:/jobs" not in md
