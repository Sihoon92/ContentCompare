"""PPT Raw Extractor 테스트 — 순수 빌더(build_ppt_doc).

PowerPoint(win32com) 없이, COM 이 만들어 줄 probe(:class:`SlideProbe`/:class:`ShapeProbe`)를
직접 주입해 physical_raw 가 슬라이드/도형/표/노트를 어떻게 담는지 검증한다. COM 계층
(_probe_* / extract_ppt_raw)은 데이터를 받아오는 일만 하므로 단위테스트 대상이 아니다.
"""

from __future__ import annotations

import json

from contentcompare.raw import raw_to_json
from contentcompare.raw.ppt_raw import ShapeProbe, SlideProbe, build_ppt_doc


def _doc(slides):
    return build_ppt_doc("deck.pptx", slides).to_dict()


def _slides(doc):
    return doc["slides"]


# --------------------------------------------------------------------------- #
# 텍스트/표/노트 추출
# --------------------------------------------------------------------------- #
def test_text_shape_extracted():
    doc = _doc([
        SlideProbe(slide_no=1, shapes=[
            ShapeProbe(kind="text", name="Title 1", text="  충전환경온도  "),
        ]),
    ])
    shape = _slides(doc)[0]["shapes"][0]
    assert shape["type"] == "text"
    assert shape["text"] == "충전환경온도"  # 공백 정돈
    assert shape["name"] == "Title 1"


def test_table_shape_extracted():
    doc = _doc([
        SlideProbe(slide_no=1, shapes=[
            ShapeProbe(kind="table", rows=[["항목", "규격"], ["충전환경온도", "-5~55"]]),
        ]),
    ])
    shape = _slides(doc)[0]["shapes"][0]
    assert shape["type"] == "table"
    assert shape["rows"] == [["항목", "규격"], ["충전환경온도", "-5~55"]]


def test_notes_extracted():
    doc = _doc([
        SlideProbe(slide_no=1, notes="0.1C, 4.55V 조건 기준",
                   shapes=[ShapeProbe(kind="text", text="본문")]),
    ])
    assert _slides(doc)[0]["notes"] == "0.1C, 4.55V 조건 기준"


# --------------------------------------------------------------------------- #
# 제외/생략 규칙
# --------------------------------------------------------------------------- #
def test_empty_text_shape_skipped():
    doc = _doc([
        SlideProbe(slide_no=1, shapes=[
            ShapeProbe(kind="text", text="   "),
            ShapeProbe(kind="text", text="내용"),
        ]),
    ])
    shapes = _slides(doc)[0]["shapes"]
    assert len(shapes) == 1 and shapes[0]["text"] == "내용"


def test_empty_table_skipped():
    doc = _doc([
        SlideProbe(slide_no=1, shapes=[
            ShapeProbe(kind="table", rows=[["", ""], ["", ""]]),
            ShapeProbe(kind="text", text="x"),
        ]),
    ])
    shapes = _slides(doc)[0]["shapes"]
    assert len(shapes) == 1 and shapes[0]["type"] == "text"


def test_empty_slide_dropped():
    # 도형도 노트도 없는 슬라이드는 결과에서 생략된다.
    doc = _doc([
        SlideProbe(slide_no=1, shapes=[ShapeProbe(kind="text", text="   ")]),
        SlideProbe(slide_no=2, shapes=[ShapeProbe(kind="text", text="살아있음")]),
    ])
    slides = _slides(doc)
    assert len(slides) == 1 and slides[0]["slide_no"] == 2


def test_chart_and_picture_kinds_excluded():
    # 결정 #6: 차트/이미지는 추출하지 않는다.
    doc = _doc([
        SlideProbe(slide_no=1, shapes=[
            ShapeProbe(kind="chart", name="Chart 1"),
            ShapeProbe(kind="picture", name="Pic 1"),
            ShapeProbe(kind="text", text="유일한 텍스트"),
        ]),
    ])
    shapes = _slides(doc)[0]["shapes"]
    assert len(shapes) == 1 and shapes[0]["text"] == "유일한 텍스트"


# --------------------------------------------------------------------------- #
# 식별자/순서/위치/스타일
# --------------------------------------------------------------------------- #
def test_slide_and_shape_ids_and_order():
    doc = _doc([
        SlideProbe(slide_no=1, shapes=[
            ShapeProbe(kind="text", text="a"),
            ShapeProbe(kind="table", rows=[["b"]]),
        ]),
    ])
    slide = _slides(doc)[0]
    assert slide["slide_id"] == "p001"
    shapes = slide["shapes"]
    assert [s["shape_id"] for s in shapes] == ["p001_s001", "p001_s002"]
    assert [s["order"] for s in shapes] == [1, 2]


def test_position_in_physical_rounded():
    doc = _doc([
        SlideProbe(slide_no=1, shapes=[
            ShapeProbe(kind="text", text="t", left=38.04, top=30.0, width=640.0, height=80.0),
        ]),
    ])
    pos = _slides(doc)[0]["shapes"][0]["position"]
    assert pos == {"left": 38.0, "top": 30.0, "width": 640.0, "height": 80.0}


def test_style_placeholder_kept():
    doc = _doc([
        SlideProbe(slide_no=1, shapes=[
            ShapeProbe(kind="text", text="제목", placeholder="title", bold=True, font_size=24.0),
        ]),
    ])
    style = _slides(doc)[0]["shapes"][0]["style"]
    assert style == {"placeholder": "title", "bold": True, "font_size": 24.0}


def test_no_position_when_absent():
    doc = _doc([SlideProbe(slide_no=1, shapes=[ShapeProbe(kind="text", text="t")])])
    assert "position" not in _slides(doc)[0]["shapes"][0]


# --------------------------------------------------------------------------- #
# 해석 금지 / 직렬화
# --------------------------------------------------------------------------- #
def test_no_interpretation_only_physical():
    doc = build_ppt_doc("deck.pptx", [
        SlideProbe(slide_no=1, shapes=[ShapeProbe(kind="text", text="충전환경온도")]),
    ])
    text = raw_to_json(doc)
    assert "entity" not in text and "lower_limit" not in text
    assert "shape_id" in text


def test_json_serializable_and_korean_preserved():
    doc = build_ppt_doc("deck.pptx", [
        SlideProbe(slide_no=1, shapes=[ShapeProbe(kind="text", text="충전환경온도")]),
    ])
    text = raw_to_json(doc)
    parsed = json.loads(text)
    assert "충전환경온도" in text
    assert parsed["doc_type"] == "ppt"
    assert parsed["slides"][0]["slide_id"] == "p001"
