"""LLM 기반 헤더 추정(detect_header) + ExcelReader auto_header 경로 테스트."""

from __future__ import annotations

from contentcompare.config import ExcelConfig
from contentcompare.readers.excel_reader import ExcelReader, SheetGrid
from contentcompare.readers.header_detect import HeaderSpec, detect_header


class FixedLLM:
    """지정한 문자열을 그대로 반환하는 가짜 LLM."""

    def __init__(self, reply):
        self.reply = reply
        self.seen_user = None

    def complete(self, system, user, *, temperature=0.0):
        self.seen_user = user
        return self.reply


def _rows():
    return [
        ["대외비", "대외비", "대외비"],
        ["제품명", "매출액", "직원수"],
        ["A", "1200", "50"],
    ]


# --------------------------------------------------------------------------- #
# detect_header
# --------------------------------------------------------------------------- #
def test_detect_header_parses_json():
    llm = FixedLLM('{"header_start": 1, "header_rows": 1}')
    spec = detect_header(_rows(), llm)
    assert spec == HeaderSpec(header_start=1, header_rows=1)
    assert "행 0:" in llm.seen_user  # 미리보기가 프롬프트에 포함


def test_detect_header_extracts_json_from_text():
    llm = FixedLLM('분석 결과: {"header_start": 0, "header_rows": 2} 입니다')
    spec = detect_header(_rows(), llm)
    assert spec == HeaderSpec(header_start=0, header_rows=2)


def test_detect_header_bad_json_returns_none():
    assert detect_header(_rows(), FixedLLM("모르겠습니다")) is None


def test_detect_header_out_of_range_returns_none():
    assert detect_header(_rows(), FixedLLM('{"header_start": 99, "header_rows": 1}')) is None


def test_detect_header_clamps_rows():
    # header_rows 가 과도하면 데이터 범위로 보정.
    spec = detect_header(_rows(), FixedLLM('{"header_start": 1, "header_rows": 9}'))
    assert spec.header_start == 1
    assert spec.header_rows == len(_rows()) - 1


# --------------------------------------------------------------------------- #
# ExcelReader auto_header 통합
# --------------------------------------------------------------------------- #
def test_reader_auto_header_uses_llm():
    grid = SheetGrid(name="S", values=[
        ["대외비", "대외비", "대외비"],
        ["제품명", "매출액", "직원수"],
        ["A", "1200", "50"],
    ])
    llm = FixedLLM('{"header_start": 1, "header_rows": 1}')
    reader = ExcelReader(ExcelConfig(auto_header=True, key_columns=["제품명"]), llm=llm)
    items = reader._parse_sheet(grid, "기준.xlsx")
    assert items[0].key_context == "[제품명=A]"
    assert {f.header for f in items[0].fields} == {"매출액", "직원수"}


def test_reader_auto_header_falls_back_when_llm_fails():
    grid = SheetGrid(name="S", values=[
        ["제품명", "매출액"],
        ["A", "1200"],
    ])
    # LLM 이 엉뚱한 응답 → 규칙 기반(header_row=1)으로 폴백.
    reader = ExcelReader(
        ExcelConfig(auto_header=True, key_columns=["제품명"]), llm=FixedLLM("???")
    )
    items = reader._parse_sheet(grid, "기준.xlsx")
    assert items[0].fields[0].header == "매출액"
