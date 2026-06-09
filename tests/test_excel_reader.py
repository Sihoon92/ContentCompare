"""ExcelReader 의 순수 파싱 로직 테스트.

xlwings(Office) 없이 :class:`SheetGrid` 에 2D 값을 직접 주입해
hybrid 분해 / 다단 헤더 / 키 추정 / 값 정규화를 검증한다.
"""

from __future__ import annotations

import pytest

from contentcompare.config import ExcelConfig
from contentcompare.models import DocType, RecordItem
from contentcompare.readers.excel_reader import (
    ExcelReader,
    SheetGrid,
    normalize_value,
)


def _reader(**cfg) -> ExcelReader:
    return ExcelReader(ExcelConfig(**cfg))


def _grid(values, **kw) -> SheetGrid:
    return SheetGrid(name="Sheet1", values=values, **kw)


# --------------------------------------------------------------------------- #
# 값 정규화
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("₩1,200,000", "1200000"),
        ("1,000", "1000"),
        ("12.50%", "12.5%"),
        ("100 억원", "100억원"),
        ("3.0", "3"),
        ("제품 A", "제품 A"),
        ("  공백   정돈 ", "공백 정돈"),
        ("", ""),
        (1500.0, "1500"),
    ],
)
def test_normalize_value(raw, expected):
    assert normalize_value(raw) == expected


# --------------------------------------------------------------------------- #
# hybrid 분해
# --------------------------------------------------------------------------- #
def test_hybrid_builds_record_with_fields_and_cellrefs():
    grid = _grid([
        ["제품명", "매출액", "직원수"],
        ["A", "1,200", "50"],
        ["B", "3,400", "70"],
    ])
    items = _reader(granularity="hybrid", key_columns=["제품명"]).\
        _parse_sheet(grid, "기준.xlsx")

    assert len(items) == 2
    rec = items[0]
    assert isinstance(rec, RecordItem)
    assert rec.doc_type == DocType.EXCEL
    assert rec.key_context == "[제품명=A]"
    # 키 컬럼은 비교 대상에서 제외 → 매출액/직원수 2개 필드.
    headers = {f.header for f in rec.fields}
    assert headers == {"매출액", "직원수"}
    sales = next(f for f in rec.fields if f.header == "매출액")
    assert sales.value_norm == "1200"
    assert sales.cell_ref == "B2"           # 2번째 열, 2번째 행
    assert sales.field_id == "기준.xlsx#Sheet1!B2"
    assert items[1].fields[0].cell_ref == "B3"


def test_first_row_offset_shifts_cell_refs():
    # used_range 가 B3 에서 시작하는 경우 셀 주소가 그에 맞게 이동.
    grid = _grid(
        [["제품명", "매출액"], ["A", "100"]],
        first_row=3,
        first_col=2,
    )
    items = _reader(header_row=3, key_columns=["제품명"]).\
        _parse_sheet(grid, "기준.xlsx")
    assert items[0].locator["row"] == 4
    assert items[0].fields[0].cell_ref == "C4"  # first_col=2(B) + 1열 → C, 행4


# --------------------------------------------------------------------------- #
# 다단 헤더
# --------------------------------------------------------------------------- #
def test_skips_full_width_banner_row():
    # 1행이 전열 통합 '대외비' 배너 → 헤더가 아니라 2행을 헤더로 인식해야 한다.
    grid = _grid([
        ["대외비", "대외비", "대외비"],
        ["제품명", "매출액", "직원수"],
        ["A", "1200", "50"],
    ])
    items = _reader(granularity="hybrid", key_columns=["제품명"]).\
        _parse_sheet(grid, "기준.xlsx")
    assert items[0].key_context == "[제품명=A]"
    assert {f.header for f in items[0].fields} == {"매출액", "직원수"}


def test_multi_header_group_label_combined():
    # [정량규격] 이 3개 열(하한/중심/상한)을 묶는 멀티헤더.
    grid = _grid([
        ["", "정량규격", "정량규격", "정량규격"],
        ["제품", "하한치", "중심치", "상한치"],
        ["A", "1", "2", "3"],
    ])
    items = _reader(granularity="hybrid", header_rows=2, key_columns=["제품"]).\
        _parse_sheet(grid, "기준.xlsx")
    headers = {f.header for f in items[0].fields}
    assert headers == {"정량규격>하한치", "정량규격>중심치", "정량규격>상한치"}


def test_banner_then_multi_header():
    # 배너(대외비) + 멀티헤더 동시: 배너 건너뛰고 2줄 헤더 결합.
    grid = _grid([
        ["대외비", "대외비", "대외비", "대외비"],
        ["", "정량규격", "정량규격", "정량규격"],
        ["제품", "하한치", "중심치", "상한치"],
        ["A", "1", "2", "3"],
    ])
    items = _reader(granularity="hybrid", header_rows=2, key_columns=["제품"]).\
        _parse_sheet(grid, "기준.xlsx")
    headers = {f.header for f in items[0].fields}
    assert headers == {"정량규격>하한치", "정량규격>중심치", "정량규격>상한치"}
    assert items[0].key_context == "[제품=A]"


def test_multi_row_header_combines_labels():
    grid = _grid([
        ["구분", "2024", None, "2023"],   # 상위(가로 병합: None 은 좌측 전파)
        ["제품", "매출", "이익", "매출"],   # 하위
        ["A", "10", "2", "8"],
    ])
    items = _reader(
        granularity="hybrid",
        header_rows=2,
        key_columns=[1],            # 1번째 열(구분>제품)
    )._parse_sheet(grid, "기준.xlsx")

    rec = items[0]
    headers = [f.header for f in rec.fields]
    assert "2024>매출" in headers
    assert "2024>이익" in headers
    assert "2023>매출" in headers


# --------------------------------------------------------------------------- #
# 검색 텍스트(search_text): 헤더 제외, 값만, 제외 컬럼 빠짐
# --------------------------------------------------------------------------- #
def test_search_text_is_values_only_no_headers():
    grid = _grid([
        ["제품명", "매출액", "직원수"],
        ["A", "1200", "50"],
    ])
    items = _reader(key_columns=["제품명"])._parse_sheet(grid, "기준.xlsx")
    rec = items[0]
    # 검색용: 헤더 없이 값만(임베딩/BM25 입력).
    assert rec.search_text == "A | 1200 | 50"
    assert "제품명" not in rec.search_text
    # 판정/표시용 text 는 헤더=값 유지.
    assert rec.text == "제품명=A | 매출액=1200 | 직원수=50"
    assert rec.index_text == "A | 1200 | 50"  # 검색은 search_text 사용


def test_search_text_excludes_empty_cells():
    grid = _grid([
        ["제품명", "매출액", "비고"],
        ["A", "1200", ""],   # 비고 빈 셀
    ])
    items = _reader(key_columns=["제품명"])._parse_sheet(grid, "기준.xlsx")
    assert items[0].search_text == "A | 1200"   # 빈 셀 제외


def test_skip_columns_excluded_from_search_text():
    grid = _grid([
        ["순번", "제품명", "매출액"],
        ["1", "A", "1200"],
    ])
    items = _reader(key_columns=["제품명"], skip_columns=["순번"])._parse_sheet(grid, "기준.xlsx")
    rec = items[0]
    # 제외 컬럼 '순번'(값 '1')은 검색·판정 양쪽에서 빠진다.
    assert "1" not in rec.search_text.split(" | ")
    assert rec.search_text == "A | 1200"
    assert "순번" not in rec.text


# --------------------------------------------------------------------------- #
# granularity 분기
# --------------------------------------------------------------------------- #
def test_field_granularity_splits_each_cell():
    grid = _grid([
        ["제품명", "매출액", "직원수"],
        ["A", "1200", "50"],
    ])
    items = _reader(granularity="field", key_columns=["제품명"]).\
        _parse_sheet(grid, "기준.xlsx")
    # 비교 컬럼 2개 → 항목 2개, 각각 단일 필드 + 키문맥 포함.
    assert len(items) == 2
    assert all(len(it.fields) == 1 for it in items)
    assert all("[제품명=A]" in it.text for it in items)
    assert {it.fields[0].header for it in items} == {"매출액", "직원수"}


def test_row_granularity_returns_plain_docitem_without_fields():
    grid = _grid([
        ["제품명", "매출액"],
        ["A", "1200"],
    ])
    items = _reader(granularity="row", key_columns=["제품명"]).\
        _parse_sheet(grid, "기준.xlsx")
    assert len(items) == 1
    assert not isinstance(items[0], RecordItem)
    assert "제품명=A" in items[0].text


# --------------------------------------------------------------------------- #
# 컬럼 해석
# --------------------------------------------------------------------------- #
def test_key_column_auto_inference_picks_text_column():
    # 키 미지정: 텍스트 비중이 높은 첫 컬럼(제품)이 키가 되어야 한다.
    grid = _grid([
        ["코드", "제품", "값"],
        [100, "사과", 10],
        [200, "배", 20],
    ])
    items = _reader(granularity="hybrid")._parse_sheet(grid, "d.xlsx")
    assert items[0].key_context == "[제품=사과]"
    # 키(제품) 제외, 코드/값 이 비교 대상.
    assert {f.header for f in items[0].fields} == {"코드", "값"}


def test_skip_and_explicit_compare_columns():
    grid = _grid([
        ["제품명", "매출액", "메모", "직원수"],
        ["A", "1200", "비고없음", "50"],
    ])
    items = _reader(
        key_columns=["제품명"],
        compare_columns=["매출액", "직원수"],
        skip_columns=["메모"],
    )._parse_sheet(grid, "d.xlsx")
    assert {f.header for f in items[0].fields} == {"매출액", "직원수"}


def test_empty_rows_are_skipped():
    grid = _grid([
        ["제품명", "매출액"],
        ["A", "1200"],
        [None, None],
        ["", ""],
    ])
    items = _reader(key_columns=["제품명"])._parse_sheet(grid, "d.xlsx")
    assert len(items) == 1


# --------------------------------------------------------------------------- #
# 표시문자(value_as_displayed)
# --------------------------------------------------------------------------- #
def test_displays_grid_used_for_value_norm():
    # 원시값은 0.125 지만 표시문자는 12.5% → 표시문자가 정규화에 쓰여야 한다.
    grid = _grid(
        values=[["제품명", "비율"], ["A", 0.125]],
        displays=[["제품명", "비율"], ["A", "12.5%"]],
    )
    items = _reader(key_columns=["제품명"], value_as_displayed=True).\
        _parse_sheet(grid, "d.xlsx")
    field = items[0].fields[0]
    assert field.value_norm == "12.5%"
    assert field.value_raw == 0.125  # 원본값은 보존
