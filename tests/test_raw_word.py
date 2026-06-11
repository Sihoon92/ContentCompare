"""Word Raw Extractor(win32com) 의 순수 빌더 테스트.

Word(win32com) 없이 :class:`ParaProbe`/:class:`TableProbe` 를 직접 주입해
:func:`build_word_doc` 가 문단/표를 문서 순서대로 physical_raw 로 만드는지
검증한다(COM 계층은 Word 설치가 필요하므로 단위테스트 대상이 아니다).

기획의 '충전환경온도' 설명 문단 + 규격표 흐름을 probe 로 재현한다.
"""

from __future__ import annotations

import json

from contentcompare.raw import raw_to_json
from contentcompare.raw.word_raw import (
    ParaProbe,
    TableProbe,
    _coerce_size,
    build_word_doc,
)


def _probes():
    return [
        ParaProbe(text="기본사양", style_name="제목 1", bold=True, font_size=16.0),
        ParaProbe(
            text="충전환경온도는 -5℃에서 55℃ 범위로 관리하며, 중심치는 25℃로 한다.",
            style_name="표준",
        ),
        TableProbe(rows=[["항목", "규격", "단위"], ["충전환경온도", "-5~55", "℃"]]),
        ParaProbe(text="   "),  # 빈 문단 — 제외되어야 한다
    ]


def _doc():
    return build_word_doc("standard_description.docx", _probes())


# --------------------------------------------------------------------------- #
# 순수 헬퍼
# --------------------------------------------------------------------------- #
def test_coerce_size():
    assert _coerce_size(16.0) == 16.0
    assert _coerce_size(None) is None
    assert _coerce_size(9999999) is None  # 혼합(wdUndefined)
    assert _coerce_size(0) is None


# --------------------------------------------------------------------------- #
# raw 문서 구조
# --------------------------------------------------------------------------- #
def test_doc_root_shape():
    doc = _doc().to_dict()
    assert doc["doc_type"] == "word"
    assert doc["file_name"] == "standard_description.docx"
    assert isinstance(doc["blocks"], list)


def test_blocks_in_document_order():
    blocks = _doc().to_dict()["blocks"]
    # heading(문단) → 설명(문단) → 표. 빈 문단은 제외 → 정확히 3개.
    assert len(blocks) == 3
    assert [b["type"] for b in blocks] == ["paragraph", "paragraph", "table"]
    assert [b["order"] for b in blocks] == [1, 2, 3]
    assert [b["block_id"] for b in blocks] == ["w_b001", "w_b002", "w_b003"]


def test_heading_paragraph_style():
    head = _doc().to_dict()["blocks"][0]
    assert head["type"] == "paragraph"
    assert head["text"] == "기본사양"
    assert head["style"]["style_name"] == "제목 1"
    assert head["style"]["bold"] is True
    assert head["style"]["font_size"] == 16.0


def test_description_paragraph_text():
    desc = _doc().to_dict()["blocks"][1]
    assert desc["type"] == "paragraph"
    assert "충전환경온도" in desc["text"]
    assert "-5℃에서 55℃" in desc["text"]


def test_table_rows_extracted():
    table = _doc().to_dict()["blocks"][2]
    assert table["type"] == "table"
    assert table["rows"][0] == ["항목", "규격", "단위"]
    assert table["rows"][1] == ["충전환경온도", "-5~55", "℃"]


def test_empty_paragraph_skipped():
    blocks = _doc().to_dict()["blocks"]
    assert all((b.get("text") or "").strip() for b in blocks if b["type"] == "paragraph")


def test_no_interpretation_only_physical():
    text = raw_to_json(_doc())
    assert "entity" not in text
    assert "lower_limit" not in text
    assert "block_id" in text


def test_json_serializable_and_korean_preserved():
    text = raw_to_json(_doc())
    parsed = json.loads(text)
    assert "충전환경온도" in text
    assert parsed["blocks"][0]["text"] == "기본사양"
