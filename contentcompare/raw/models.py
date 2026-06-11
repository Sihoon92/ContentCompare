"""Raw json 데이터 모델 (physical_raw).

문서에서 *관찰 가능한* 정보만 담는다 — 해석된 결과가 아니다.
모든 모델은 :meth:`to_dict` 로 순수 dict(→ json 직렬화 가능) 가 된다.

설계 메모
---------
- ``None`` 인 부가 필드(스타일·코멘트 등)는 :meth:`to_dict` 에서 **생략** 한다.
  raw json 을 LLM 입력으로 쓸 때 노이즈를 줄이기 위함(있는 정보만 보여준다).
- 위치 정보는 절대 좌표를 유지한다. 나중에 불일치를 찾았을 때 "어느 파일, 어느
  시트, 어느 셀/단락" 인지 되짚어야 하기 때문.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    """값이 ``None`` 인 키를 제거한 새 dict 를 반환(빈 리스트/문자열은 보존)."""
    return {k: v for k, v in d.items() if v is not None}


# --------------------------------------------------------------------------- #
# Excel
# --------------------------------------------------------------------------- #
@dataclass
class RawCell:
    """엑셀 셀 1개의 물리 정보."""

    address: str
    """A1 표기 주소 (예: ``E17``)."""

    row: int
    """1-based 절대 행 번호."""

    column: str
    """열 문자 (예: ``E``)."""

    col_index: int
    """1-based 절대 열 번호."""

    value: Any
    """셀 원문 값(숫자/문자/날짜 등). 빈 셀은 이 객체 자체를 만들지 않는다."""

    value_type: str
    """값의 파이썬 타입 이름 (``str``/``int``/``float``/``datetime`` 등)."""

    number_format: Optional[str] = None
    """표시 서식 문자열 (예: ``0.0%``, ``#,##0``). 기본(General)이면 생략."""

    merged_range: Optional[str] = None
    """이 셀이 속한 병합 영역 (예: ``F2:H2``). 병합 안이 아니면 ``None``."""

    is_merged_anchor: bool = False
    """병합 영역의 좌상단(값을 실제로 보유한) 셀이면 ``True``."""

    font_bold: Optional[bool] = None
    """굵게 여부. 알 수 없으면 ``None``."""

    font_size: Optional[float] = None
    fill_color: Optional[str] = None
    """배경색 ARGB 문자열 (예: ``FFFFFF00``). 채움 없으면 생략."""

    comment: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(
            {
                "address": self.address,
                "row": self.row,
                "column": self.column,
                "col_index": self.col_index,
                "value": self.value,
                "value_type": self.value_type,
                "number_format": self.number_format,
                "merged_range": self.merged_range,
                # anchor 가 아니면(False) 굳이 내보내지 않아 노이즈 감소.
                "is_merged_anchor": self.is_merged_anchor or None,
                "font_bold": self.font_bold,
                "font_size": self.font_size,
                "fill_color": self.fill_color,
                "comment": self.comment,
            }
        )


@dataclass
class RawMergedRegion:
    """병합 셀 영역 1개."""

    range: str
    """병합 범위 (예: ``B2:B3``)."""

    value: Any
    """좌상단 앵커 셀의 값."""

    def to_dict(self) -> dict[str, Any]:
        return {"range": self.range, "value": self.value}


@dataclass
class RawSheet:
    """엑셀 시트 1장의 물리 구조."""

    sheet_name: str
    used_range: Optional[str]
    """used range A1 범위 (예: ``B2:P17``). 빈 시트면 ``None``."""

    min_row: int
    max_row: int
    min_col: int
    max_col: int
    merged_cells: list[RawMergedRegion] = field(default_factory=list)
    cells: list[RawCell] = field(default_factory=list)
    """비어있지 않은 셀들(행→열 순). 빈 셀은 포함하지 않는다."""

    hidden: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(
            {
                "sheet_name": self.sheet_name,
                "used_range": self.used_range,
                "dimensions": {
                    "min_row": self.min_row,
                    "max_row": self.max_row,
                    "min_col": self.min_col,
                    "max_col": self.max_col,
                },
                "hidden": self.hidden or None,
                "merged_cells": [m.to_dict() for m in self.merged_cells],
                "cells": [c.to_dict() for c in self.cells],
            }
        )


@dataclass
class RawExcelDocument:
    """엑셀 파일 1개의 raw json 루트."""

    file_name: str
    sheets: list[RawSheet] = field(default_factory=list)
    doc_type: str = "excel"

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_type": self.doc_type,
            "file_name": self.file_name,
            "sheets": [s.to_dict() for s in self.sheets],
        }


# --------------------------------------------------------------------------- #
# Word
# --------------------------------------------------------------------------- #
@dataclass
class RawWordBlock:
    """Word 본문 블록 1개 (문단 또는 표). 문서 순서(order)를 보존한다."""

    block_id: str
    """문서 내 안정 식별자 (예: ``w_b002``)."""

    order: int
    """본문 등장 순서 (1-based)."""

    type: str
    """``paragraph`` 또는 ``table``."""

    text: Optional[str] = None
    """문단 텍스트(type=paragraph)."""

    style: Optional[dict[str, Any]] = None
    """스타일 정보 (style_name/bold/font_size 등). 비면 생략."""

    rows: Optional[list[list[str]]] = None
    """표 셀 텍스트 2D(type=table)."""

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(
            {
                "block_id": self.block_id,
                "order": self.order,
                "type": self.type,
                "text": self.text,
                "style": self.style,
                "rows": self.rows,
            }
        )


@dataclass
class RawWordDocument:
    """Word 파일 1개의 raw json 루트."""

    file_name: str
    blocks: list[RawWordBlock] = field(default_factory=list)
    doc_type: str = "word"

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_type": self.doc_type,
            "file_name": self.file_name,
            "blocks": [b.to_dict() for b in self.blocks],
        }


# 디스패처/타입힌트용 합집합.
RawDocument = "RawExcelDocument | RawWordDocument"
