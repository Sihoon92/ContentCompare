"""행 단위 종합 판정(compare_record) + RecordResult + 리포트 렌더 테스트(요청 1번)."""

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
        self.last_user = ""

    def complete(self, system, user, *, temperature=0.0):
        self.last_user = user
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


def _resp(verdict, findings, *, matched=("t.docx#1",), reasoning="행 종합"):
    return json.dumps(
        {
            "verdict": verdict,
            "matched_item_ids": list(matched),
            "reasoning": reasoning,
            "findings": findings,
        }
    )


def test_compare_record_unknown_verdict_with_reason():
    rec, cands = _record()
    llm = ScriptedLLM(_resp("unknown", [
        {"field_id": "d.xlsx#S!B2", "found": True,
         "note": "단위가 달라 동일 여부 불명", "evidence": "A 제품 매출 1200"},
        {"field_id": "d.xlsx#S!C2", "found": True, "note": "모호", "evidence": "직원 60명"},
    ], reasoning="단위(억원 등)가 불명확해 같은 값인지 판단 어려움"))
    result = Comparator(llm).compare_record(rec, cands)
    assert result.verdict == Verdict.UNKNOWN
    assert "판단" in result.reasoning or "불명" in result.reasoning
    # 근거 인용이 finding 에 보존된다.
    b2 = next(fd for fd in result.findings if fd.field.cell_ref == "B2")
    assert b2.evidence == "A 제품 매출 1200"


# --------------------------------------------------------------------------- #
def test_compare_record_holistic_verdict_and_findings():
    rec, cands = _record()
    llm = ScriptedLLM(_resp("partial", [
        {"field_id": "d.xlsx#S!B2", "found": True, "note": "매출 1200 확인"},
        {"field_id": "d.xlsx#S!C2", "found": True, "note": "직원수 50 vs 60 차이"},
    ]))
    result = Comparator(llm).compare_record(rec, cands)

    assert isinstance(result, RecordResult)
    assert result.verdict == Verdict.PARTIAL          # 행 종합 판정(저장값)
    assert result.reasoning == "행 종합"
    assert result.matched_item_ids == ["t.docx#1"]
    bid = {fd.field.field_id: fd for fd in result.findings}
    assert bid["d.xlsx#S!B2"].found is True
    assert "직원수" in bid["d.xlsx#S!C2"].note


def test_compare_record_filters_invalid_matched_ids():
    rec, cands = _record()
    llm = ScriptedLLM(_resp("same", [
        {"field_id": "d.xlsx#S!B2", "found": True, "note": "x"},
        {"field_id": "d.xlsx#S!C2", "found": True, "note": "y"},
    ], matched=["t.docx#1", "없는id"]))
    result = Comparator(llm).compare_record(rec, cands)
    assert result.matched_item_ids == ["t.docx#1"]    # 존재하지 않는 id 제거


def test_compare_record_missing_finding_marked_not_found():
    rec, cands = _record()
    # C2 항목 내역이 응답에서 누락됨.
    llm = ScriptedLLM(_resp("partial", [
        {"field_id": "d.xlsx#S!B2", "found": True, "note": "ok"},
    ]))
    result = Comparator(llm).compare_record(rec, cands)
    c2 = next(fd for fd in result.findings if fd.field.cell_ref == "C2")
    assert c2.found is False
    assert "누락" in c2.note


def test_compare_record_no_candidates_not_found():
    rec, _ = _record()
    llm = ScriptedLLM("should-not-be-called")
    result = Comparator(llm).compare_record(rec, [])
    assert llm.calls == 0  # LLM 호출 없음
    assert result.verdict == Verdict.NOT_FOUND
    assert all(fd.found is False for fd in result.findings)


def test_compare_record_retries_on_bad_json():
    rec, cands = _record()
    good = _resp("same", [
        {"field_id": "d.xlsx#S!B2", "found": True, "note": "a"},
        {"field_id": "d.xlsx#S!C2", "found": True, "note": "b"},
    ])
    llm = ScriptedLLM("죄송합니다 JSON 아님", good)
    result = Comparator(llm).compare_record(rec, cands)
    assert llm.calls == 2  # 1차 실패 → 재요청
    assert result.verdict == Verdict.SAME


def test_record_verdict_same():
    rec, cands = _record()
    llm = ScriptedLLM(_resp("same", [
        {"field_id": "d.xlsx#S!B2", "found": True, "note": "a"},
        {"field_id": "d.xlsx#S!C2", "found": True, "note": "b"},
    ]))
    assert Comparator(llm).compare_record(rec, cands).verdict == Verdict.SAME


def test_knowledge_injected_into_prompt():
    rec, cands = _record()
    llm = ScriptedLLM(_resp("same", [
        {"field_id": "d.xlsx#S!B2", "found": True, "note": "a"},
        {"field_id": "d.xlsx#S!C2", "found": True, "note": "b"},
    ]))
    Comparator(llm, knowledge="formation = 화성 공정").compare_record(rec, cands)
    assert "formation = 화성 공정" in llm.last_user


# --------------------------------------------------------------------------- #
def test_report_renders_record_findings_table():
    rec, cands = _record()
    llm = ScriptedLLM(_resp("partial", [
        {"field_id": "d.xlsx#S!B2", "found": True, "note": "매출 일치"},
        {"field_id": "d.xlsx#S!C2", "found": False, "note": "직원수 다름"},
    ], reasoning="A 제품은 t.docx 1단락에 있으며 매출은 같으나 직원수가 다름"))
    result = Comparator(llm).compare_record(rec, cands)
    md = render_markdown([result], reference_doc="d.xlsx", target_docs=["t.docx"])
    assert "# 문서 비교 리포트" in md
    assert "| 항목(열) | 기준값 | 확인 | 근거 | 인용(후보 원문) |" in md  # 열별 확인 표 헤더
    assert "매출액" in md and "직원수" in md
    assert "종합 근거(왜)" in md
