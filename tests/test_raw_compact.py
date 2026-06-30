"""Raw Compactor 테스트.

physical_raw(RawExcelDocument/RawWordDocument) → compact_raw 압축이 표 모양(행 단위
{열:값})과 구조 신호(병합/헤더 힌트)를 잘 보존하면서 군더더기를 줄이는지 검증한다.
COM 불필요 — raw 객체를 코드로 만들어 주입한다.
"""

from __future__ import annotations

import json

from contentcompare.raw import compact_raw, compact_to_json
from contentcompare.raw.excel_raw import CellProbe, SheetProbe, build_raw_sheet
from contentcompare.raw.models import RawExcelDocument
from contentcompare.raw.ppt_raw import ShapeProbe, SlideProbe, build_ppt_doc
from contentcompare.raw.word_raw import ParaProbe, TableProbe, build_word_doc


# --------------------------------------------------------------------------- #
# Excel 압축
# --------------------------------------------------------------------------- #
def _excel_doc() -> RawExcelDocument:
    cells = [
        CellProbe(2, 6, "정량규격", merged_range="F2:H2", is_merged_anchor=True,
                  font_bold=True, fill_color="FFFFFF00"),
        CellProbe(3, 6, "하한치", font_bold=True),
        CellProbe(3, 7, "중심치", font_bold=True),
        CellProbe(3, 8, "상한치", font_bold=True),
        CellProbe(4, 5, "충전환경온도", comment="확인 필요"),
        CellProbe(4, 6, -5),
        CellProbe(4, 7, 25, number_format="0.0"),
        CellProbe(4, 8, 55),
    ]
    probe = SheetProbe(name="StandardList", cells=cells,
                       min_row=2, max_row=4, min_col=5, max_col=8)
    doc = RawExcelDocument(file_name="standard.xlsx")
    doc.sheets.append(build_raw_sheet(probe))
    return doc


def test_excel_root_and_dims():
    out = compact_raw(_excel_doc())
    assert out["doc_type"] == "excel"
    assert out["file_name"] == "standard.xlsx"
    sheet = out["sheets"][0]
    assert sheet["sheet_name"] == "StandardList"
    assert sheet["used_range"] == "E2:H4"
    assert sheet["n_rows"] == 3 and sheet["n_cols"] == 4


def test_excel_rows_grouped_by_row():
    sheet = compact_raw(_excel_doc())["sheets"][0]
    rows = {r["r"]: r["cells"] for r in sheet["rows"]}
    # 3행은 하한/중심/상한이 열 문자 키로 묶인다.
    assert rows[3] == {"F": "하한치", "G": "중심치", "H": "상한치"}
    # 4행은 값들이 그대로(숫자 보존).
    assert rows[4] == {"E": "충전환경온도", "F": -5, "G": 25, "H": 55}


def test_excel_merged_cells_preserved():
    sheet = compact_raw(_excel_doc())["sheets"][0]
    assert {"range": "F2:H2", "value": "정량규격"} in sheet["merged_cells"]


def test_excel_sparse_side_maps():
    sheet = compact_raw(_excel_doc())["sheets"][0]
    # bold 는 헤더 후보 힌트로 주소 리스트만.
    assert set(sheet["bold_cells"]) == {"F2", "F3", "G3", "H3"}
    assert sheet["cell_formats"] == {"G4": "0.0"}
    assert sheet["fill_cells"] == {"F2": "FFFFFF00"}
    assert sheet["comments"] == {"E4": "확인 필요"}


def test_excel_no_default_noise_in_cells():
    """행의 cells 에는 값만 — font_size/value_type/bold 같은 기본 노이즈가 없어야 한다."""
    text = compact_to_json(_excel_doc())
    assert "value_type" not in text
    assert "font_size" not in text
    assert "is_merged_anchor" not in text
    assert "col_index" not in text


def test_excel_max_rows_truncation():
    out = compact_raw(_excel_doc(), max_rows=2)
    sheet = out["sheets"][0]
    assert len(sheet["rows"]) == 2
    assert sheet["truncated"] is True
    assert sheet["total_rows"] == 3


# --------------------------------------------------------------------------- #
# Word 압축
# --------------------------------------------------------------------------- #
def _word_doc():
    probes = [
        ParaProbe(text="기본사양", style_name="Heading1", bold=True, font_size=16.0),
        ParaProbe(text="설명 문단"),  # 스타일 없음
        TableProbe(rows=[["항목", "규격"], ["충전환경온도", "-5~55"]]),
    ]
    return build_word_doc("desc.docx", probes)


def test_word_blocks_compacted():
    out = compact_raw(_word_doc())
    assert out["doc_type"] == "word"
    blocks = out["blocks"]
    assert [b["type"] for b in blocks] == ["paragraph", "paragraph", "table"]
    assert blocks[0]["id"] == "w_b001"
    assert blocks[0]["text"] == "기본사양"
    assert blocks[0]["style"]["style_name"] == "Heading1"
    # 스타일 없는 문단엔 style 키가 없다(압축).
    assert "style" not in blocks[1]
    # 표는 rows 보존.
    assert blocks[2]["rows"] == [["항목", "규격"], ["충전환경온도", "-5~55"]]


def test_word_drops_order_field():
    text = compact_to_json(_word_doc())
    parsed = json.loads(text)
    assert "order" not in parsed["blocks"][0]  # 순서는 리스트/ id 로 충분
    assert "충전환경온도" in text  # 한글 보존


# --------------------------------------------------------------------------- #
# PPT 압축
# --------------------------------------------------------------------------- #
def _ppt_doc():
    return build_ppt_doc("deck.pptx", [
        SlideProbe(slide_no=1, layout_name="Title and Content",
                   notes="0.1C, 4.55V 조건 기준", shapes=[
                       ShapeProbe(kind="text", name="Title 1", text="충전환경온도",
                                  placeholder="title", left=38.0, top=30.0),
                       ShapeProbe(kind="table", rows=[["항목", "규격"], ["온도", "-5~55"]]),
                   ]),
    ])


def test_ppt_slides_compacted():
    out = compact_raw(_ppt_doc())
    assert out["doc_type"] == "ppt"
    slide = out["slides"][0]
    assert slide["slide_no"] == 1
    assert slide["layout"] == "Title and Content"
    assert slide["notes"] == "0.1C, 4.55V 조건 기준"
    shapes = slide["shapes"]
    assert [s["type"] for s in shapes] == ["text", "table"]
    assert shapes[0]["text"] == "충전환경온도"
    assert shapes[0]["name"] == "Title 1"
    assert shapes[0]["style"] == {"placeholder": "title"}
    assert shapes[1]["rows"] == [["항목", "규격"], ["온도", "-5~55"]]


def test_ppt_position_dropped_in_compact():
    text = compact_to_json(_ppt_doc())
    assert "position" not in text
    assert "left" not in text


def test_ppt_korean_preserved():
    text = compact_to_json(_ppt_doc())
    assert "충전환경온도" in text
