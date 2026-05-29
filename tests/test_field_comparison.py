"""Phase 3: 필드별 LLM 비교(compare_record) + RecordResult 집계 + 리포트 렌더 테스트."""

from __future__ import annotations

import json

from contentcompare.comparison import Comparator
from contentcompare.models import (
    Candidate,
    DocItem,
    DocType,
    FieldClaim,
    RecordItem,
    RecordResult,
    Verdict,
)
from contentcompare.report import render_markdown


class ScriptedLLM:
    """미리 지정한 응답을 순서대로 돌려주는 가짜 LLM."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, system, user, *, temperature=0.0):
        r = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return r


def _record():
    f1 = FieldClaim("d.xlsx#S!B2", "매출액", 1200, "1200", "B2")
    f2 = FieldClaim("d.xlsx#S!C2", "직원수", 50, "50", "C2")
    rec = RecordItem(
        item_id="d.xlsx#S!2",
        doc_id="d.xlsx",
        doc_type=DocType.EXCEL,
        text="제품명=A | 매출액=1200 | 직원수=50",
        source_label="d.xlsx > [S] 2행 [제품명=A]",
        key_context="[제품명=A]",
        fields=[f1, f2],
    )
    cand = Candidate(
        DocItem("t.docx#1", "t.docx", DocType.WORD, "A 제품 매출 1200, 직원 60명", "t.docx 1단락"),
        0.9,
    )
    return rec, [cand]


def _resp(fields):
    return json.dumps({"fields": fields})


# --------------------------------------------------------------------------- #
def test_compare_record_maps_field_verdicts():
    rec, cands = _record()
    llm = ScriptedLLM(_resp([
        {"field_id": "d.xlsx#S!B2", "verdict": "same",
         "matched_item_ids": ["t.docx#1"], "reasoning": "매출 1200 일치"},
        {"field_id": "d.xlsx#S!C2", "verdict": "different",
         "matched_item_ids": ["t.docx#1"], "reasoning": "직원수 50 vs 60"},
    ]))
    result = Comparator(llm).compare_record(rec, cands)

    assert isinstance(result, RecordResult)
    bid = {fr.field.field_id: fr for fr in result.fields}
    assert bid["d.xlsx#S!B2"].verdict == Verdict.SAME
    assert bid["d.xlsx#S!C2"].verdict == Verdict.DIFFERENT
    # 집계: 일부 different → 레코드는 부분일치(같음과 다름 혼재).
    assert result.verdict == Verdict.PARTIAL


def test_compare_record_filters_invalid_matched_ids():
    rec, cands = _record()
    llm = ScriptedLLM(_resp([
        {"field_id": "d.xlsx#S!B2", "verdict": "same",
         "matched_item_ids": ["t.docx#1", "없는id"], "reasoning": "x"},
        {"field_id": "d.xlsx#S!C2", "verdict": "not_found",
         "matched_item_ids": [], "reasoning": "y"},
    ]))
    result = Comparator(llm).compare_record(rec, cands)
    b2 = next(fr for fr in result.fields if fr.field.cell_ref == "B2")
    assert b2.matched_item_ids == ["t.docx#1"]  # 존재하지 않는 id 제거


def test_compare_record_missing_field_marked_different():
    rec, cands = _record()
    # C2 필드 판정이 응답에서 누락됨.
    llm = ScriptedLLM(_resp([
        {"field_id": "d.xlsx#S!B2", "verdict": "same",
         "matched_item_ids": ["t.docx#1"], "reasoning": "ok"},
    ]))
    result = Comparator(llm).compare_record(rec, cands)
    c2 = next(fr for fr in result.fields if fr.field.cell_ref == "C2")
    assert c2.verdict == Verdict.DIFFERENT
    assert "누락" in c2.reasoning


def test_compare_record_no_candidates_all_not_found():
    rec, _ = _record()
    llm = ScriptedLLM("should-not-be-called")
    result = Comparator(llm).compare_record(rec, [])
    assert llm.calls == 0  # LLM 호출 없음
    assert all(fr.verdict == Verdict.NOT_FOUND for fr in result.fields)
    assert result.verdict == Verdict.NOT_FOUND


def test_compare_record_retries_on_bad_json():
    rec, cands = _record()
    good = _resp([
        {"field_id": "d.xlsx#S!B2", "verdict": "same", "matched_item_ids": [], "reasoning": "a"},
        {"field_id": "d.xlsx#S!C2", "verdict": "same", "matched_item_ids": [], "reasoning": "b"},
    ])
    llm = ScriptedLLM("죄송합니다 JSON 아님", good)
    result = Comparator(llm).compare_record(rec, cands)
    assert llm.calls == 2  # 1차 실패 → 재요청
    assert result.verdict == Verdict.SAME


def test_record_verdict_all_same():
    rec, cands = _record()
    llm = ScriptedLLM(_resp([
        {"field_id": "d.xlsx#S!B2", "verdict": "same", "matched_item_ids": [], "reasoning": "a"},
        {"field_id": "d.xlsx#S!C2", "verdict": "same", "matched_item_ids": [], "reasoning": "b"},
    ]))
    assert Comparator(llm).compare_record(rec, cands).verdict == Verdict.SAME


# --------------------------------------------------------------------------- #
def test_report_renders_record_field_table():
    rec, cands = _record()
    llm = ScriptedLLM(_resp([
        {"field_id": "d.xlsx#S!B2", "verdict": "same",
         "matched_item_ids": ["t.docx#1"], "reasoning": "매출 일치"},
        {"field_id": "d.xlsx#S!C2", "verdict": "different",
         "matched_item_ids": ["t.docx#1"], "reasoning": "직원수 다름"},
    ]))
    result = Comparator(llm).compare_record(rec, cands)
    md = render_markdown([result], reference_doc="d.xlsx", target_docs=["t.docx"])
    assert "# 문서 비교 리포트" in md
    assert "| 필드 | 기준값 | 판정 | 출처 | 사유 |" in md  # 필드 표 헤더
    assert "매출액" in md and "직원수" in md
    assert "필드 1/2 일치" in md  # 요약 한줄
