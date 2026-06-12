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
    _column_edges,
    _grid_from_cells,
    _grid_from_geometry,
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


def test_grid_from_cells_basic():
    placed = [
        (1, 1, "항목"), (1, 2, "규격"), (1, 3, "단위"),
        (2, 1, "충전환경온도"), (2, 2, "-5~55"), (2, 3, "℃"),
    ]
    assert _grid_from_cells(placed) == [
        ["항목", "규격", "단위"],
        ["충전환경온도", "-5~55", "℃"],
    ]


def test_grid_from_cells_vertical_merge():
    # 세로 병합: '기본사양' 이 1열 2~3행을 병합 → table.Range.Cells 는 (2,1) 에만
    # 셀을 주고 (3,1) 은 구멍. fill_merged 로 (3,1) 에 '기본사양' 이 채워져야 한다.
    placed = [
        (1, 1, "구분"), (1, 2, "항목"), (1, 3, "값"),
        (2, 1, "기본사양"), (2, 2, "충전환경온도"), (2, 3, "-5"),
        (3, 2, "충전상한온도"), (3, 3, "55"),  # (3,1) 은 병합에 가려진 구멍
    ]
    assert _grid_from_cells(placed) == [
        ["구분", "항목", "값"],
        ["기본사양", "충전환경온도", "-5"],
        ["기본사양", "충전상한온도", "55"],  # 세로 병합 값이 아래 행으로 전파
    ]


def test_grid_from_cells_vertical_merge_user_example():
    # 사용자 예시: 'version' 이 1열 2개행 병합, 2열은 reliability test / Teardown.
    # 기대: ('version','reliability test'), ('version','Teardown').
    placed = [
        (1, 1, "version"), (1, 2, "reliability test"),
        (2, 2, "Teardown"),  # (2,1) 은 'version' 세로 병합에 가려진 구멍
    ]
    assert _grid_from_cells(placed) == [
        ["version", "reliability test"],
        ["version", "Teardown"],
    ]


def test_grid_from_cells_real_empty_cell_not_filled():
    # (2,1) 이 '구멍' 이 아니라 텍스트가 빈 '실제 셀' 이면 전파하지 않는다.
    placed = [
        (1, 1, "a"), (1, 2, "b"),
        (2, 1, ""), (2, 2, "c"),  # (2,1) 은 실제 빈 셀(병합 아님)
    ]
    assert _grid_from_cells(placed) == [["a", "b"], ["", "c"]]


def test_grid_from_cells_fill_disabled():
    placed = [(1, 1, "version"), (1, 2, "reliability test"), (2, 2, "Teardown")]
    assert _grid_from_cells(placed, fill_merged=False) == [
        ["version", "reliability test"],
        ["", "Teardown"],
    ]


def test_grid_from_cells_empty():
    assert _grid_from_cells([]) == []


# --------------------------------------------------------------------------- #
# 기하 기반 격자(가로 + 세로 병합 모두 채움) — 정상 경로
# --------------------------------------------------------------------------- #
def test_column_edges_clusters_within_tol():
    # 0/100/200 근처 위치들(±2pt 오차) → 3개 컬럼 경계.
    assert _column_edges([0.0, 100.5, 200.0, 99.0, 0.8], tol=3.0) == [0.0, 99.0, 200.0]


def test_geometry_plain_table():
    # 3컬럼(0/100/200, 너비 100) 일반 2행 표.
    geom = [
        (1, 0.0, 100.0, "항목"), (1, 100.0, 100.0, "규격"), (1, 200.0, 100.0, "단위"),
        (2, 0.0, 100.0, "충전환경온도"), (2, 100.0, 100.0, "-5~55"), (2, 200.0, 100.0, "℃"),
    ]
    assert _grid_from_geometry(geom) == [
        ["항목", "규격", "단위"],
        ["충전환경온도", "-5~55", "℃"],
    ]


def test_geometry_horizontal_merge_filled():
    # 1행: 'A' 가 1~2열 가로 병합(너비 200), 'B' 는 3열. 2행은 일반 3칸.
    geom = [
        (1, 0.0, 200.0, "A"), (1, 200.0, 100.0, "B"),
        (2, 0.0, 100.0, "a"), (2, 100.0, 100.0, "b"), (2, 200.0, 100.0, "c"),
    ]
    assert _grid_from_geometry(geom) == [
        ["A", "A", "B"],  # 가로 병합 값이 두 컬럼에 동일 저장
        ["a", "b", "c"],
    ]


def test_geometry_vertical_merge_user_example():
    # 'version' 이 1열 2행 세로 병합(2행 1열엔 셀 없음 → 구멍).
    geom = [
        (1, 0.0, 100.0, "version"), (1, 100.0, 100.0, "reliability test"),
        (2, 100.0, 100.0, "Teardown"),
    ]
    assert _grid_from_geometry(geom) == [
        ["version", "reliability test"],
        ["version", "Teardown"],  # 세로 병합 값이 아래 행에 채워짐
    ]


def test_geometry_both_horizontal_and_vertical_merge():
    # 'T' 가 1~2열 가로 병합 + 1~2행 세로 병합. 3행(a/b/c)이 100pt 컬럼 경계를 만든다.
    geom = [
        (1, 0.0, 200.0, "T"), (1, 200.0, 100.0, "X"),
        (2, 200.0, 100.0, "Y"),  # (2,1)(2,2) 는 T 에 가려진 구멍
        (3, 0.0, 100.0, "a"), (3, 100.0, 100.0, "b"), (3, 200.0, 100.0, "c"),
    ]
    assert _grid_from_geometry(geom) == [
        ["T", "T", "X"],  # 가로 병합
        ["T", "T", "Y"],  # 세로 병합으로 위 'T' 가 두 컬럼 모두에 전파
        ["a", "b", "c"],
    ]


def test_geometry_empty():
    assert _grid_from_geometry([]) == []


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
