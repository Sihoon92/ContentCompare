"""블록 ↔ fact 매핑(``facts_by_block.json``) 테스트 — LLM/네트워크 불필요.

이 계측의 존재 이유는 **F3 추출 누락 추적**이다. 인용되지 않은 블록(``cited=False``)이
곧 "대상에 없음" 오판의 후보이므로, 그 검출이 깨지지 않는지가 핵심이다.
"""

from __future__ import annotations

from contentcompare.fact.fact_extractor import build_facts_by_block
from contentcompare.fact.fact_models import Fact, FactSet


def _fact(fact_id: str, source: dict, name: str = "항목") -> Fact:
    return Fact(fact_id=fact_id, entity_name=name, source=source)


# --------------------------------------------------------------------------- #
# Word
# --------------------------------------------------------------------------- #
WORD_COMPACT = {
    "doc_type": "word",
    "file_name": "규격서.docx",
    "blocks": [
        {"id": "w_b001", "type": "paragraph", "text": "배터리 셀 규격서"},
        {"id": "w_b002", "type": "paragraph", "text": "공칭전압은 3.85V 이다."},
        {"id": "w_b003", "type": "table", "rows": [["항목", "규격"], ["공칭용량", "1150"]]},
    ],
}


def test_word_maps_block_ids_and_flags_uncited():
    facts = FactSet(location="규격서.docx", facts=[
        _fact("fact-word-1", {"doc_type": "word", "block_ids": ["w_b002"]}),
        _fact("fact-word-2", {"doc_type": "word", "block_ids": ["w_b003"]}),
    ])
    out = build_facts_by_block(WORD_COMPACT, facts)

    by_id = {b["id"]: b for b in out["blocks"]}
    assert by_id["w_b002"]["fact_ids"] == ["fact-word-1"]
    assert by_id["w_b002"]["cited"] is True
    assert by_id["w_b003"]["kind"] == "table"
    # 아무 fact 도 근거로 쓰지 않은 블록 → F3 추출 누락 후보
    assert by_id["w_b001"]["cited"] is False and by_id["w_b001"]["fact_ids"] == []
    assert out["summary"]["blocks_in"] == 3
    assert out["summary"]["blocks_cited"] == 2


def test_word_block_order_is_preserved():
    out = build_facts_by_block(WORD_COMPACT, FactSet())
    assert [b["id"] for b in out["blocks"]] == ["w_b001", "w_b002", "w_b003"]


def test_one_fact_can_cite_several_blocks():
    facts = FactSet(facts=[
        _fact("fact-word-1", {"doc_type": "word", "block_ids": ["w_b001", "w_b002"]}),
    ])
    out = build_facts_by_block(WORD_COMPACT, facts)
    by_id = {b["id"]: b for b in out["blocks"]}
    assert by_id["w_b001"]["fact_ids"] == ["fact-word-1"]
    assert by_id["w_b002"]["fact_ids"] == ["fact-word-1"]


def test_table_preview_flattens_rows():
    out = build_facts_by_block(WORD_COMPACT, FactSet())
    table = next(b for b in out["blocks"] if b["id"] == "w_b003")
    assert "공칭용량" in table["preview"] and "1150" in table["preview"]


# --------------------------------------------------------------------------- #
# PPT — 스피커노트 id 역산이 핵심
# --------------------------------------------------------------------------- #
PPT_COMPACT = {
    "doc_type": "ppt",
    "file_name": "발표.pptx",
    "slides": [
        {"slide_no": 1,
         "shapes": [{"id": "p001_s001", "type": "text", "text": "규격 리뷰", "name": "T1"}],
         "notes": "환경 조건은 챔버 기준."},
        {"slide_no": 2,
         "shapes": [{"id": "p002_s002", "type": "table",
                     "rows": [["항목", "값"], ["충전환경온도", "35"]], "name": "T2"}]},
    ],
}


def test_ppt_shape_and_notes_ids_are_reconstructed():
    facts = FactSet(facts=[
        _fact("fact-ppt-1", {"doc_type": "ppt", "slide_no": 1,
                             "shape_ids": ["p001_s001"], "from_notes": False}),
        # 스피커노트만 근거로 삼은 fact — shape_ids 가 빈 배열이다(실측 형태).
        _fact("fact-ppt-2", {"doc_type": "ppt", "slide_no": 1,
                             "shape_ids": [], "from_notes": True}),
    ])
    out = build_facts_by_block(PPT_COMPACT, facts)
    by_id = {b["id"]: b for b in out["blocks"]}

    assert by_id["s1-p001_s001"]["fact_ids"] == ["fact-ppt-1"]
    assert by_id["s1-notes"]["fact_ids"] == ["fact-ppt-2"]
    assert by_id["s1-notes"]["kind"] == "notes"
    assert by_id["s2-p002_s002"]["cited"] is False


def test_ppt_fact_citing_both_shape_and_notes():
    facts = FactSet(facts=[
        _fact("fact-ppt-1", {"doc_type": "ppt", "slide_no": 1,
                             "shape_ids": ["p001_s001"], "from_notes": True}),
    ])
    out = build_facts_by_block(PPT_COMPACT, facts)
    by_id = {b["id"]: b for b in out["blocks"]}
    assert by_id["s1-p001_s001"]["cited"] and by_id["s1-notes"]["cited"]


# --------------------------------------------------------------------------- #
# Excel — 행 하나 = 블록 하나(뷰어가 doc_type 분기를 덜 하도록 같은 스키마)
# --------------------------------------------------------------------------- #
EXCEL_COMPACT = {
    "doc_type": "excel",
    "file_name": "기준.xlsx",
    "sheets": [{
        "sheet_name": "데이터",
        "rows": [
            {"r": 1, "cells": {"B": "순번", "C": "항목"}},
            {"r": 3, "cells": {"B": "1", "C": "공칭전압", "D": "3.85"}},
        ],
    }],
}


def test_excel_rows_become_blocks():
    facts = FactSet(facts=[
        _fact("fact-row-3", {"doc_type": "excel", "sheet": "데이터",
                             "row": 3, "cell_range": "B3:P3"}),
    ])
    out = build_facts_by_block(EXCEL_COMPACT, facts)
    by_id = {b["id"]: b for b in out["blocks"]}

    assert by_id["row-3"]["fact_ids"] == ["fact-row-3"]
    assert by_id["row-1"]["cited"] is False  # 헤더 행은 fact 가 안 된다(정상)
    assert "공칭전압" in by_id["row-3"]["preview"]


# --------------------------------------------------------------------------- #
# 경계 · 방어
# --------------------------------------------------------------------------- #
def test_no_facts_yields_all_uncited():
    out = build_facts_by_block(WORD_COMPACT, FactSet())
    assert out["summary"]["facts_out"] == 0
    assert out["summary"]["blocks_cited"] == 0
    assert all(not b["cited"] for b in out["blocks"])


def test_fact_pointing_at_unknown_block_is_reported_not_dropped():
    """블록 목록에 없는 id 를 가리키는 fact — 조용히 사라지면 원인 추적이 끊긴다."""
    facts = FactSet(facts=[
        _fact("fact-word-9", {"doc_type": "word", "block_ids": ["w_b999"]}),
    ])
    out = build_facts_by_block(WORD_COMPACT, facts)
    assert out["summary"]["facts_without_block"] == ["fact-word-9"]


def test_fact_without_source_is_reported():
    facts = FactSet(facts=[_fact("fact-word-9", {})])
    out = build_facts_by_block(WORD_COMPACT, facts)
    assert out["summary"]["facts_without_block"] == ["fact-word-9"]


def test_extract_stats_are_merged_into_summary():
    """캐시 여부·드롭 사유가 같은 자리에 실려야 뷰어가 한 번에 읽는다."""
    stats = {"cached": True, "facts_out": 2, "dropped_no_valid_source_id": 3}
    out = build_facts_by_block(WORD_COMPACT, FactSet(), stats)
    assert out["summary"]["cached"] is True
    assert out["summary"]["dropped_no_valid_source_id"] == 3


def test_unknown_doc_type_does_not_raise():
    out = build_facts_by_block({"doc_type": "pdf"}, FactSet())
    assert out["blocks"] == [] and out["summary"]["blocks_in"] == 0
