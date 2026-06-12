"""Raw Compactor — physical_raw → compact_raw (LLM 입력용 압축).

physical_raw 는 셀마다 ``address/row/column/col_index/value_type/font_size`` 가
반복돼 토큰을 많이 먹고 노이즈가 크다. Compactor 는 이를 **LLM 이 구조를 추론하기
좋은 형태**로 줄인다. 단, 구조 추론에 필요한 신호(병합 영역, 헤더 후보 힌트)는
보존한다 — 어디가 헤더이고 row grain 이 무엇인지 **판단은 하지 않는다**(LLM 의 몫).

압축 원칙
---------
- Excel: 셀들을 행 단위 ``{열: 값}`` 맵으로 묶는다(표 모양 그대로 보임). 기본값
  (font_size, bold=false, value_type 등)은 버리고, 의미 있는 메타데이터(bold/서식/
  채움/코멘트)만 **희소 side-map** 으로 남긴다 → 압축 + 거의 무손실.
- Word: 이미 압축적이라 군더더기 필드(value_type 등)만 정리한다.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .models import RawExcelDocument, RawWordDocument


def compact_raw(doc: Any, *, max_rows: Optional[int] = None) -> dict[str, Any]:
    """raw 문서 객체 → compact dict. doc_type 으로 분기."""
    if isinstance(doc, RawExcelDocument):
        return compact_excel(doc, max_rows=max_rows)
    if isinstance(doc, RawWordDocument):
        return compact_word(doc)
    raise TypeError(f"compact_raw: 지원하지 않는 문서 타입 {type(doc)!r}")


def compact_to_json(
    doc: Any, *, indent: int = 2, max_rows: Optional[int] = None
) -> str:
    """compact dict → json 문자열(한글 보존)."""
    return json.dumps(
        compact_raw(doc, max_rows=max_rows), ensure_ascii=False, indent=indent
    )


# --------------------------------------------------------------------------- #
# Excel
# --------------------------------------------------------------------------- #
def compact_excel(
    doc: RawExcelDocument, *, max_rows: Optional[int] = None
) -> dict[str, Any]:
    return {
        "doc_type": "excel",
        "file_name": doc.file_name,
        "sheets": [_compact_sheet(s, max_rows) for s in doc.sheets],
    }


def _compact_sheet(sheet, max_rows: Optional[int]) -> dict[str, Any]:
    """RawSheet → 행 단위 {열:값} + 희소 메타 side-map 으로 압축."""
    by_row: dict[int, dict[str, Any]] = {}
    bold: list[str] = []
    formats: dict[str, str] = {}
    fills: dict[str, str] = {}
    comments: dict[str, str] = {}

    for c in sorted(sheet.cells, key=lambda c: (c.row, c.col_index)):
        by_row.setdefault(c.row, {})[c.column] = c.value
        if c.font_bold:
            bold.append(c.address)
        if c.number_format:
            formats[c.address] = c.number_format
        if c.fill_color:
            fills[c.address] = c.fill_color
        if c.comment:
            comments[c.address] = c.comment

    rows = [{"r": r, "cells": by_row[r]} for r in sorted(by_row)]
    truncated = False
    if max_rows is not None and len(rows) > max_rows:
        rows = rows[:max_rows]
        truncated = True

    out: dict[str, Any] = {
        "sheet_name": sheet.sheet_name,
        "used_range": sheet.used_range,
        "n_rows": (sheet.max_row - sheet.min_row + 1) if sheet.cells else 0,
        "n_cols": (sheet.max_col - sheet.min_col + 1) if sheet.cells else 0,
        "merged_cells": [{"range": m.range, "value": m.value} for m in sheet.merged_cells],
        "rows": rows,
    }
    # 있을 때만 넣어 압축 유지(헤더 추론 힌트 / 부가 메타).
    if truncated:
        out["truncated"] = True
        out["total_rows"] = len(by_row)
    if sheet.hidden:
        out["hidden"] = True
    if bold:
        out["bold_cells"] = bold
    if formats:
        out["cell_formats"] = formats
    if fills:
        out["fill_cells"] = fills
    if comments:
        out["comments"] = comments
    return out


# --------------------------------------------------------------------------- #
# Word
# --------------------------------------------------------------------------- #
def compact_word(doc: RawWordDocument) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    for b in doc.blocks:
        if b.type == "paragraph":
            item: dict[str, Any] = {"id": b.block_id, "type": "paragraph", "text": b.text}
            if b.style:
                item["style"] = b.style
        else:  # table
            item = {"id": b.block_id, "type": "table", "rows": b.rows}
        blocks.append(item)
    return {"doc_type": "word", "file_name": doc.file_name, "blocks": blocks}
