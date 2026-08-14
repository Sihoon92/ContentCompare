"""Word Raw Extractor 테스트 — WordOpenXML 파싱 + 빌더.

Word(win32com) 없이, Word 가 내보내는 OpenXML(WordprocessingML)과 같은 형태의 XML
문자열을 직접 만들어 :func:`parse_word_xml` 가 문단/표(특히 병합)를 어떻게
physical_raw 로 만드는지 검증한다. COM 계층은 이 XML 문자열을 받아오는 일만 하므로
테스트 대상이 아니다.

기획의 '충전환경온도' 흐름 + 사용자가 보고한 가로/세로 병합 케이스를 재현한다.
"""

from __future__ import annotations

import json

from contentcompare.raw import raw_to_json
from contentcompare.raw.word_raw import (
    ParaProbe,
    TableProbe,
    build_word_doc,
    parse_word_xml,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
PKG = "http://schemas.microsoft.com/office/2006/xmlPackage"


# --------------------------------------------------------------------------- #
# XML 픽스처 헬퍼
# --------------------------------------------------------------------------- #
def _pkg(body: str) -> str:
    """body(<w:p>/<w:tbl> ...) 를 WordOpenXML 패키지 문자열로 감싼다."""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<pkg:package xmlns:pkg="{PKG}">'
        '<pkg:part pkg:name="/word/document.xml">'
        "<pkg:xmlData>"
        f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
        "</pkg:xmlData></pkg:part></pkg:package>"
    )


def _para(text: str, *, style: str | None = None, bold: bool | None = None,
          sz: int | None = None) -> str:
    pPr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    rpr_bits = ""
    if bold is not None:
        rpr_bits += "<w:b/>" if bold else '<w:b w:val="false"/>'
    if sz is not None:
        rpr_bits += f'<w:sz w:val="{sz}"/>'  # half-points
    rPr = f"<w:rPr>{rpr_bits}</w:rPr>" if rpr_bits else ""
    return f"<w:p>{pPr}<w:r>{rPr}<w:t>{text}</w:t></w:r></w:p>"


def _tc(text: str, *, span: int | None = None, vmerge: str | None = None) -> str:
    props = ""
    if span:
        props += f'<w:gridSpan w:val="{span}"/>'
    if vmerge == "restart":
        props += '<w:vMerge w:val="restart"/>'
    elif vmerge == "continue":
        props += "<w:vMerge/>"
    tcPr = f"<w:tcPr>{props}</w:tcPr>" if props else ""
    body = f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" if text else "<w:p/>"
    return f"<w:tc>{tcPr}{body}</w:tc>"


def _tbl(rows: list[str], n_cols: int) -> str:
    grid = "<w:tblGrid>" + '<w:gridCol w:w="100"/>' * n_cols + "</w:tblGrid>"
    trs = "".join(f"<w:tr>{r}</w:tr>" for r in rows)
    return f"<w:tbl>{grid}{trs}</w:tbl>"


def _blocks(body: str):
    return build_word_doc("doc.docx", parse_word_xml(_pkg(body))).to_dict()["blocks"]


def _rows(body: str):
    """단일 표 body → rows 2D."""
    blocks = _blocks(body)
    assert len(blocks) == 1 and blocks[0]["type"] == "table"
    return blocks[0]["rows"]


# --------------------------------------------------------------------------- #
# 문단
# --------------------------------------------------------------------------- #
def test_paragraph_order_and_text():
    body = _para("기본사양", style="Heading1") + _para(
        "충전환경온도는 -5℃에서 55℃ 범위로 관리하며, 중심치는 25℃로 한다."
    )
    blocks = _blocks(body)
    assert [b["type"] for b in blocks] == ["paragraph", "paragraph"]
    assert blocks[0]["text"] == "기본사양"
    assert "충전환경온도" in blocks[1]["text"]


def test_paragraph_style_bold_size():
    blocks = _blocks(_para("제목", style="Heading1", bold=True, sz=32))
    style = blocks[0]["style"]
    assert style["style_name"] == "Heading1"
    assert style["bold"] is True
    assert style["font_size"] == 16.0  # 32 half-points → 16pt


def test_empty_paragraph_skipped():
    blocks = _blocks(_para("내용") + "<w:p/>")
    assert len(blocks) == 1
    assert blocks[0]["text"] == "내용"


# --------------------------------------------------------------------------- #
# 표 — 일반/가로 병합/세로 병합/혼합
# --------------------------------------------------------------------------- #
def test_plain_table():
    body = _tbl(
        [
            _tc("항목") + _tc("규격") + _tc("단위"),
            _tc("충전환경온도") + _tc("-5~55") + _tc("℃"),
        ],
        n_cols=3,
    )
    assert _rows(body) == [["항목", "규격", "단위"], ["충전환경온도", "-5~55", "℃"]]


def test_horizontal_merge_gridspan_filled():
    # 1행: 'A' 가 1~2열 가로 병합(gridSpan=2), 'B' 는 3열.
    body = _tbl(
        [
            _tc("A", span=2) + _tc("B"),
            _tc("a") + _tc("b") + _tc("c"),
        ],
        n_cols=3,
    )
    assert _rows(body) == [["A", "A", "B"], ["a", "b", "c"]]


def test_vertical_merge_vmerge_filled():
    # 사용자 예시: 'version' 이 1열 2행 세로 병합(restart/continue).
    body = _tbl(
        [
            _tc("version", vmerge="restart") + _tc("reliability test"),
            _tc("", vmerge="continue") + _tc("Teardown"),
        ],
        n_cols=2,
    )
    assert _rows(body) == [
        ["version", "reliability test"],
        ["version", "Teardown"],
    ]


def test_vertical_merge_three_rows():
    # 3행 세로 병합 연쇄 전파.
    body = _tbl(
        [
            _tc("ver", vmerge="restart") + _tc("r1"),
            _tc("", vmerge="continue") + _tc("r2"),
            _tc("", vmerge="continue") + _tc("r3"),
        ],
        n_cols=2,
    )
    assert _rows(body) == [["ver", "r1"], ["ver", "r2"], ["ver", "r3"]]


def test_combined_horizontal_and_vertical_merge():
    # 'T' 가 1~2열 가로 병합 + 1~2행 세로 병합. 3열은 X / Y.
    body = _tbl(
        [
            _tc("T", span=2, vmerge="restart") + _tc("X"),
            _tc("", span=2, vmerge="continue") + _tc("Y"),
        ],
        n_cols=3,
    )
    assert _rows(body) == [["T", "T", "X"], ["T", "T", "Y"]]


def test_real_empty_cell_not_filled():
    # 병합이 아닌 진짜 빈 셀은 ''로 남는다(세로 전파 없음).
    body = _tbl(
        [
            _tc("a") + _tc("b"),
            _tc("") + _tc("c"),
        ],
        n_cols=2,
    )
    assert _rows(body) == [["a", "b"], ["", "c"]]


# --------------------------------------------------------------------------- #
# 문서 순서 + 직렬화
# --------------------------------------------------------------------------- #
def test_document_order_paragraph_then_table():
    body = (
        _para("기본사양", style="Heading1")
        + _para("설명 문단")
        + _tbl([_tc("항목") + _tc("값")], n_cols=2)
    )
    blocks = _blocks(body)
    assert [b["type"] for b in blocks] == ["paragraph", "paragraph", "table"]
    assert [b["block_id"] for b in blocks] == ["w_b001", "w_b002", "w_b003"]
    assert [b["order"] for b in blocks] == [1, 2, 3]


def test_no_interpretation_only_physical():
    body = _para("기본사양", style="Heading1")
    doc = build_word_doc("doc.docx", parse_word_xml(_pkg(body)))
    text = raw_to_json(doc)
    assert "entity" not in text and "lower_limit" not in text
    assert "block_id" in text


def test_json_serializable_and_korean_preserved():
    body = _para("충전환경온도")
    doc = build_word_doc("desc.docx", parse_word_xml(_pkg(body)))
    text = raw_to_json(doc)
    parsed = json.loads(text)
    assert "충전환경온도" in text
    assert parsed["doc_type"] == "word"


def test_parse_empty_or_invalid_xml():
    assert parse_word_xml("") == []
    assert parse_word_xml("not xml") == []


# --------------------------------------------------------------------------- #
# 빌더 단독(파서 무관)
# --------------------------------------------------------------------------- #
def test_build_word_doc_filters_empty_table():
    probes = [TableProbe(rows=[["", ""], ["", ""]]), ParaProbe(text="x")]
    blocks = build_word_doc("d.docx", probes).to_dict()["blocks"]
    assert len(blocks) == 1 and blocks[0]["text"] == "x"


# --------------------------------------------------------------------------- #
# 문단 들여쓰기 (Task 2)
# --------------------------------------------------------------------------- #
def _doc_xml(body: str) -> str:
    return (
        '<?xml version="1.0"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )


def test_paragraph_indent_from_w_ind():
    """<w:ind w:left> 를 칸 수로 환산해 담는다(720 twips = 0.5인치 ≈ 6칸)."""
    from contentcompare.raw.word_raw import parse_word_xml

    xml = _doc_xml(
        '<w:p><w:pPr><w:ind w:left="720"/></w:pPr><w:r><w:t>들여쓴 문단</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>보통 문단</w:t></w:r></w:p>"
    )
    probes = parse_word_xml(xml)

    assert probes[0].indent == 6
    assert probes[1].indent == 0


def test_paragraph_indent_accepts_w_start_alias():
    """w:start 는 w:left 의 신형 이름이다 — 둘 다 읽어야 한다."""
    from contentcompare.raw.word_raw import parse_word_xml

    xml = _doc_xml(
        '<w:p><w:pPr><w:ind w:start="360"/></w:pPr><w:r><w:t>x</w:t></w:r></w:p>'
    )
    assert parse_word_xml(xml)[0].indent == 3


def test_block_indent_reaches_physical_raw():
    """문단 들여쓰기가 블록까지 흐르고, 0 이면 키가 없다."""
    from contentcompare.raw.word_raw import build_word_doc, parse_word_xml

    xml = _doc_xml(
        '<w:p><w:pPr><w:ind w:left="720"/></w:pPr><w:r><w:t>a</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>b</w:t></w:r></w:p>"
    )
    blocks = build_word_doc("t.docx", parse_word_xml(xml)).to_dict()["blocks"]

    assert blocks[0]["indent"] == 6
    assert "indent" not in blocks[1]
