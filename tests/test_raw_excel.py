"""Excel Raw Extractor(xlwings) 의 순수 빌더 테스트.

xlwings(Excel) 없이 :class:`SheetProbe`/:class:`CellProbe` 를 직접 주입해
:func:`build_raw_sheet` 가 physical_raw 를 어떻게 만드는지 검증한다(COM 계층은
Excel 설치가 필요하므로 단위테스트 대상이 아니다 — readers 와 동일한 분리 패턴).

기획의 '충전환경온도' 표준규격 표(병합 멀티헤더 + 데이터 1행)를 probe 로 재현한다.
"""

from __future__ import annotations

import json

from contentcompare.raw import raw_to_json
from contentcompare.raw.excel_raw import (
    CellProbe,
    SheetProbe,
    _coerce_bool,
    _col_letter,
    build_raw_sheet,
)
from contentcompare.raw.models import RawExcelDocument


def _standard_probe() -> SheetProbe:
    """'충전환경온도' 예시: F2:H2('정량규격') 가로병합 + I2:I3 세로병합 + 데이터 4행."""
    cells = [
        # 멀티헤더 상단(2행) — F2 가 F2:H2 병합 앵커, I2 가 I2:I3 병합 앵커.
        CellProbe(2, 4, "항목", merged_range="D2:E2", is_merged_anchor=True, font_bold=True),
        CellProbe(2, 6, "정량규격", merged_range="F2:H2", is_merged_anchor=True,
                  font_bold=True, fill_color="FFFFFF00"),
        CellProbe(2, 9, "SPEC(중심치+-상하한치)", merged_range="I2:I3", is_merged_anchor=True),
        CellProbe(2, 11, "단위", merged_range="K2:K3", is_merged_anchor=True),
        # 멀티헤더 하단(3행).
        CellProbe(3, 4, "중분류", font_bold=True),
        CellProbe(3, 5, "소분류", font_bold=True),
        CellProbe(3, 6, "하한치", font_bold=True),
        CellProbe(3, 7, "중심치", font_bold=True),
        CellProbe(3, 8, "상한치", font_bold=True),
        # 데이터(4행).
        CellProbe(4, 2, 10),
        CellProbe(4, 3, "기본사양"),
        CellProbe(4, 4, "기본사양"),
        CellProbe(4, 5, "충전환경온도", comment="측정 조건 확인 필요"),
        CellProbe(4, 6, -5),
        CellProbe(4, 7, 25, number_format="0.0"),
        CellProbe(4, 8, 55),
        CellProbe(4, 9, "25+-30"),
        CellProbe(4, 10, "-5 to 5도씨 0.1C (4.55V)"),
        CellProbe(4, 11, "도씨"),
    ]
    return SheetProbe(
        name="StandardList", cells=cells, min_row=2, max_row=4, min_col=2, max_col=11
    )


def _doc():
    doc = RawExcelDocument(file_name="standard.xlsx")
    doc.sheets.append(build_raw_sheet(_standard_probe()))
    return doc


def _cell(sheet_dict, address):
    for c in sheet_dict["cells"]:
        if c["address"] == address:
            return c
    return None


# --------------------------------------------------------------------------- #
# 순수 헬퍼
# --------------------------------------------------------------------------- #
def test_col_letter():
    assert _col_letter(1) == "A"
    assert _col_letter(6) == "F"
    assert _col_letter(11) == "K"
    assert _col_letter(27) == "AA"


def test_coerce_bool():
    assert _coerce_bool(-1) is True
    assert _coerce_bool(0) is False
    assert _coerce_bool(None) is None
    assert _coerce_bool(9999999) is None  # 혼합(wdUndefined)


# --------------------------------------------------------------------------- #
# raw 문서 구조
# --------------------------------------------------------------------------- #
def test_doc_root_shape():
    doc = _doc().to_dict()
    assert doc["doc_type"] == "excel"
    assert doc["file_name"] == "standard.xlsx"
    assert len(doc["sheets"]) == 1
    assert doc["sheets"][0]["sheet_name"] == "StandardList"


def test_used_range_from_dimensions():
    sheet = _doc().to_dict()["sheets"][0]
    assert sheet["used_range"] == "B2:K4"
    assert sheet["dimensions"] == {"min_row": 2, "max_row": 4, "min_col": 2, "max_col": 11}


def test_merged_regions_captured():
    sheet = _doc().to_dict()["sheets"][0]
    ranges = {m["range"]: m["value"] for m in sheet["merged_cells"]}
    assert ranges["D2:E2"] == "항목"
    assert ranges["F2:H2"] == "정량규격"
    assert ranges["I2:I3"] == "SPEC(중심치+-상하한치)"


def test_merged_anchor_and_range_on_cells():
    sheet = _doc().to_dict()["sheets"][0]
    f2 = _cell(sheet, "F2")
    assert f2["value"] == "정량규격"
    assert f2["merged_range"] == "F2:H2"
    assert f2["is_merged_anchor"] is True
    # 병합 영역 안의 빈 셀(G2/H2)은 probe 에 없으므로 cells 에도 없다.
    assert _cell(sheet, "G2") is None


def test_data_cells_keep_value_and_type():
    sheet = _doc().to_dict()["sheets"][0]
    e4 = _cell(sheet, "E4")
    assert e4["value"] == "충전환경온도"
    assert e4["row"] == 4 and e4["column"] == "E" and e4["col_index"] == 5

    f4 = _cell(sheet, "F4")
    assert f4["value"] == -5
    assert f4["value_type"] == "int"

    assert _cell(sheet, "A1") is None  # 빈 셀은 없음


def test_style_and_format_metadata():
    sheet = _doc().to_dict()["sheets"][0]
    f2 = _cell(sheet, "F2")
    assert f2["font_bold"] is True
    assert f2["fill_color"] == "FFFFFF00"

    g4 = _cell(sheet, "G4")
    assert g4["number_format"] == "0.0"

    e4 = _cell(sheet, "E4")
    assert e4["comment"] == "측정 조건 확인 필요"


def test_no_interpretation_only_physical():
    """raw 단계는 의미 해석을 하지 않는다 — entity/lower_limit 같은 키가 없어야 한다."""
    text = raw_to_json(_doc())
    assert "entity" not in text
    assert "lower_limit" not in text
    assert "merged_range" in text and "address" in text


def test_json_serializable_and_korean_preserved():
    text = raw_to_json(_doc())
    parsed = json.loads(text)
    assert "충전환경온도" in text  # ensure_ascii=False 로 한글 보존
    assert parsed["sheets"][0]["sheet_name"] == "StandardList"
