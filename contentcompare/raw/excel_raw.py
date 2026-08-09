"""Excel Raw Extractor — xlwings(COM) 로 .xlsx 를 physical_raw 로 변환.

회사 환경 제약으로 openpyxl 대신 **xlwings(설치된 Excel)** 를 사용한다. 기존
``readers/excel_reader.py`` 와 동일하게 **COM I/O 와 순수 파싱을 분리** 한다:

- COM 계층(:func:`_probe_sheet`): xlwings ``.api`` 로 셀 값/서식/병합/코멘트를 읽어
  순수 데이터(:class:`SheetProbe`)로 만든다. Excel 설치가 필요 → 단위테스트 불가.
- 순수 계층(:func:`build_raw_sheet`): :class:`SheetProbe` → :class:`RawSheet`.
  Excel 없이 probe 를 직접 주입해 테스트할 수 있다.

해석은 하지 않는다. 보이는 것만 담는다(값/위치/병합/서식/코멘트). 어느 행이
헤더인지 등은 후속 LLM 단계의 몫.

xlwings 는 Windows + Excel 설치가 필요하므로 import 를 :func:`extract_excel_raw`
시점으로 지연한다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Optional

from ..readers import com_util
from .models import RawCell, RawExcelDocument, RawMergedRegion, RawSheet

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# COM I/O 와 순수 파싱의 경계 (테스트 시 직접 주입)
# --------------------------------------------------------------------------- #
@dataclass
class CellProbe:
    """COM 으로 읽은 셀 1개의 원시 정보(서식 포함). 빈 셀은 만들지 않는다."""

    row: int
    col: int
    value: Any
    number_format: Optional[str] = None
    font_bold: Optional[bool] = None
    font_size: Optional[float] = None
    fill_color: Optional[str] = None
    comment: Optional[str] = None
    merged_range: Optional[str] = None
    """이 셀이 속한 병합영역 (예: ``F2:H2``). 병합 아니면 None."""

    is_merged_anchor: bool = False


@dataclass
class SheetProbe:
    """시트 1장의 COM 추출 결과. :func:`build_raw_sheet` 의 입력."""

    name: str
    cells: list[CellProbe] = field(default_factory=list)
    min_row: int = 1
    max_row: int = 1
    min_col: int = 1
    max_col: int = 1
    hidden: bool = False


# --------------------------------------------------------------------------- #
# 순수 헬퍼
# --------------------------------------------------------------------------- #
def _col_letter(col: int) -> str:
    """1-based 컬럼 번호 → 엑셀 열 문자(A, B, ..., AA)."""
    letters = ""
    while col > 0:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _value_type(value: Any) -> str:
    """raw json 에 남길 값 타입 이름. datetime 계열은 별도 라벨."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, time):
        return "time"
    return type(value).__name__


def _json_safe(value: Any) -> Any:
    """json 직렬화 가능한 형태로 변환(날짜는 ISO 문자열). 숫자/문자는 그대로."""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


def _a1(range_str: str) -> str:
    """COM MergeArea 주소(``$F$2:$H$2``) → A1 표기(``F2:H2``)."""
    return range_str.replace("$", "")


# --------------------------------------------------------------------------- #
# 순수 빌더 (Excel 불필요 — 테스트 진입점)
# --------------------------------------------------------------------------- #
def build_raw_sheet(probe: SheetProbe) -> RawSheet:
    """:class:`SheetProbe` → :class:`RawSheet`.

    병합영역 목록은 앵커 셀(좌상단)들에서 모은다(dedup). 빈 셀은 probe 에 없으므로
    자동으로 제외된다.
    """
    cells: list[RawCell] = []
    merged: list[RawMergedRegion] = []
    seen_ranges: set[str] = set()

    for cp in probe.cells:
        fmt = cp.number_format
        cells.append(
            RawCell(
                address=f"{_col_letter(cp.col)}{cp.row}",
                row=cp.row,
                column=_col_letter(cp.col),
                col_index=cp.col,
                value=_json_safe(cp.value),
                value_type=_value_type(cp.value),
                number_format=fmt if fmt and fmt != "General" else None,
                merged_range=cp.merged_range,
                is_merged_anchor=cp.is_merged_anchor,
                font_bold=cp.font_bold,
                font_size=cp.font_size,
                fill_color=cp.fill_color,
                comment=cp.comment,
            )
        )
        if cp.is_merged_anchor and cp.merged_range and cp.merged_range not in seen_ranges:
            seen_ranges.add(cp.merged_range)
            merged.append(RawMergedRegion(range=cp.merged_range, value=_json_safe(cp.value)))

    # probe.cells 는 행→열 순이므로 앵커도 등장 순서대로 모인다(별도 정렬 불필요).
    used_range = None
    if cells:
        used_range = (
            f"{_col_letter(probe.min_col)}{probe.min_row}:"
            f"{_col_letter(probe.max_col)}{probe.max_row}"
        )

    return RawSheet(
        sheet_name=probe.name,
        used_range=used_range,
        min_row=probe.min_row,
        max_row=probe.max_row,
        min_col=probe.min_col,
        max_col=probe.max_col,
        merged_cells=merged,
        cells=cells,
        hidden=probe.hidden,
    )


# --------------------------------------------------------------------------- #
# COM 진입점
# --------------------------------------------------------------------------- #
def extract_excel_raw(path: str) -> RawExcelDocument:
    """xlsx 파일 경로 → :class:`RawExcelDocument` (xlwings/COM)."""
    try:
        import xlwings as xw
    except ImportError as exc:  # pragma: no cover - 환경 의존
        raise RuntimeError(
            "xlwings 가 필요합니다(Windows + Excel). pip install xlwings"
        ) from exc

    file_name = os.path.basename(path)
    doc = RawExcelDocument(file_name=file_name)

    # 워커 스레드(예: Streamlit)에서 COM 을 쓰려면 스레드별 초기화가 필요하다.
    try:
        import pythoncom

        pythoncom.CoInitialize()
    except Exception:  # noqa: BLE001 - 비윈도우/이미 초기화 등
        pythoncom = None

    logger.info("[RawExcel] 열기: %s", os.path.abspath(path))
    app = xw.App(visible=False, add_book=False)
    com_util.track("excel", app)
    book = None
    try:
        book = app.books.open(path)
        for sheet in book.sheets:
            probe = _probe_sheet(sheet)
            if probe is not None:
                doc.sheets.append(build_raw_sheet(probe))
        logger.info("[RawExcel] 완료: 시트 %d개", len(doc.sheets))
    except Exception:
        logger.exception("[RawExcel] 처리 실패: %s", path)
        raise
    finally:
        try:
            if book is not None:
                book.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RawExcel] book.close 실패(무시): %s", exc)
        com_util.close_app("excel", app)
        if pythoncom is not None:
            try:
                pythoncom.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass
    return doc


def _probe_sheet(sheet) -> Optional[SheetProbe]:  # pragma: no cover - COM 의존
    """xlwings 시트 → :class:`SheetProbe`. 비어있지 않은 셀만 서식까지 읽는다."""
    used = sheet.used_range
    values = used.value
    if values is None:
        return None

    first_row = int(used.row)
    first_col = int(used.column)
    # used.value 를 항상 2D 로 정규화.
    if not isinstance(values, list):
        grid = [[values]]
    elif values and not isinstance(values[0], list):
        grid = [values]
    else:
        grid = values

    cells: list[CellProbe] = []
    max_cols = 0
    for r, row_vals in enumerate(grid):
        max_cols = max(max_cols, len(row_vals))
        for c, val in enumerate(row_vals):
            if val is None or (isinstance(val, str) and val == ""):
                continue
            abs_row = first_row + r
            abs_col = first_col + c
            cell = sheet.range((abs_row, abs_col))
            cells.append(_probe_cell(cell, abs_row, abs_col, val))

    if not cells:
        return None

    hidden = False
    try:
        hidden = bool(sheet.api.Visible != -1)  # xlSheetVisible == -1
    except Exception:  # noqa: BLE001
        pass

    nrows = len(grid)
    return SheetProbe(
        name=sheet.name,
        cells=cells,
        min_row=first_row,
        max_row=first_row + nrows - 1,
        min_col=first_col,
        max_col=first_col + max_cols - 1,
        hidden=hidden,
    )


def _probe_cell(cell, abs_row: int, abs_col: int, value: Any) -> CellProbe:  # pragma: no cover - COM 의존
    """단일 셀의 서식/병합/코멘트를 COM 으로 읽어 :class:`CellProbe` 생성."""
    api = cell.api

    number_format = _safe(lambda: api.NumberFormat)
    font_bold = _coerce_bool(_safe(lambda: api.Font.Bold))
    font_size = _safe(lambda: float(api.Font.Size))
    fill_color = _probe_fill(api)
    comment = _safe(lambda: api.Comment.Text() if api.Comment is not None else None)

    merged_range = None
    is_anchor = False
    if _safe(lambda: bool(api.MergeCells)):
        area = _safe(lambda: api.MergeArea)
        if area is not None:
            merged_range = _a1(_safe(lambda: area.Address) or "")
            anchor_row = _safe(lambda: int(area.Row))
            anchor_col = _safe(lambda: int(area.Column))
            is_anchor = (anchor_row == abs_row and anchor_col == abs_col)

    return CellProbe(
        row=abs_row,
        col=abs_col,
        value=value,
        number_format=number_format,
        font_bold=font_bold,
        font_size=font_size,
        fill_color=fill_color,
        comment=comment,
        merged_range=merged_range or None,
        is_merged_anchor=is_anchor,
    )


def _probe_fill(api) -> Optional[str]:  # pragma: no cover - COM 의존
    """Interior(채움)색 → ARGB hex. solid 패턴이 아니면 None."""
    # xlNone == -4142. 패턴이 없으면 색을 무시한다.
    pattern = _safe(lambda: int(api.Interior.Pattern))
    if pattern is None or pattern == -4142:
        return None
    bgr = _safe(lambda: int(api.Interior.Color))
    if bgr is None or bgr < 0:
        return None
    # Excel Interior.Color 는 BGR 정수. RGB 로 재배열 후 ARGB(불투명) 문자열로.
    blue, green, red = (bgr >> 16) & 0xFF, (bgr >> 8) & 0xFF, bgr & 0xFF
    return f"FF{red:02X}{green:02X}{blue:02X}"


def _coerce_bool(v: Any) -> Optional[bool]:
    """COM 의 Bold 등(-1/0/혼합) → True/False/None."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if v in (-1, 1, True):
        return True
    if v in (0, False):
        return False
    return None  # 혼합(wdUndefined 등)


def _safe(fn):  # pragma: no cover - COM 의존
    """COM 속성 접근 중 예외/None 을 흡수해 None 으로 폴백."""
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return None
