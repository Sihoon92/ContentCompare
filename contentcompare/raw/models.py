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
class RawLine:
    """문단 안의 한 줄(soft line break 로 나뉜 단위).

    한 문단에 조건이 여러 개 적히는 문서가 흔하다(충전 온도 4구간). 그것을 한
    문자열로 뭉개면 어느 조건이 대상에 있고 없는지를 사후에 확인할 방법이 사라진다.

    ``raw_text`` 와 ``normalized_text`` 를 나눈 이유는 **검색 정규화가 원문 인용을
    훼손하면 안 되기** 때문이다. 사람이 검수할 때 보는 것은 ``raw_text`` 다.
    """

    line_id: str
    """``<block_id>:l<NN>`` 형식의 안정 식별자 (예: ``w_b012:l03``)."""

    order: int
    """문단 안에서의 순서 (1-based)."""

    raw_text: str
    """인용용 원문. 양끝 공백만 정리하고 내부는 건드리지 않는다."""

    normalized_text: str = ""
    """검색용 정규화 텍스트. ``raw_text`` 와 같으면 생략한다."""

    indent: int = 0
    """이 줄의 선행 공백 칸 수(탭은 4칸 환산). 0 이면 ``to_dict`` 에서 생략한다.

    원문에서 열을 맞춰 앞 줄의 레이블을 생략한 연속행은 **이 값으로만** 구분된다 —
    ``raw_text`` 는 인용 검증 규약을 지키려고 양끝을 strip 하므로 흔적이 남지 않는다.
    """

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "line_id": self.line_id,
            "order": self.order,
            "raw_text": self.raw_text,
        }
        # 같은 값을 두 번 싣지 않는다 — physical_raw 가 두 배로 커진다.
        if self.normalized_text and self.normalized_text != self.raw_text:
            out["normalized_text"] = self.normalized_text
        if self.indent:
            out["indent"] = self.indent
        return out


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
    """문단 텍스트(type=paragraph). **줄바꿈이 병합된 기존 표현**이다 —
    ``compact_raw`` 가 이것을 F3 LLM 입력으로 쓰므로 표현을 바꾸지 않는다."""

    style: Optional[dict[str, Any]] = None
    """서식 정보 (style_name/bold/font_size). 비면 생략.

    ⚠️ **이 dict 는 ``compact_raw`` 를 통해 F3 LLM 입력에 그대로 실린다.** 여기에
    키를 더하면 프롬프트가 바뀌어 fact 추출 결과와 캐시가 통째로 무효화된다.
    코드만 쓰는 구조 정보는 :attr:`structure` 에 넣을 것.
    """

    structure: Optional[dict[str, Any]] = None
    """구조 정보 (heading_level/list). 비면 생략.

    ``style`` 과 나눈 이유는 두 가지다. 첫째, heading 계층과 목록 여부는 서식이
    아니라 **문서 구조**이고 Phase 5 에서 ``under_heading``/``same_list`` 관계로
    쓰인다. 둘째, ``compact_raw`` 가 ``style`` 만 내보내므로 여기 담으면 F3 입력이
    변하지 않는다 — 원문 보존이 fact 추출을 건드리지 않는다는 것이 이 단계의 계약이다.
    """

    rows: Optional[list[list[str]]] = None
    """표 셀 텍스트 2D(type=table)."""

    cell_lines: Optional[list[list[list[str]]]] = None
    """표 셀의 줄 목록(type=table). ``rows``(행×열)에 한 겹 더한 **행 × 열 × 줄**.

    줄이 하나뿐인 셀은 ``[]`` 로 둔다 — 원소 1개짜리 리스트로 채우면 "여러 줄인
    셀"을 가려내는 판정이 길이 비교가 되어야 하는데, 빈 리스트면 ``if`` 한 줄로
    끝난다. 전 셀이 1줄이면 필드 자체가 ``None`` 이라 ``physical_raw`` 가 안 커진다.

    ⚠️ ``rows`` 의 셀 문자열은 **여기 있는 줄을 공백으로 이어 붙인 값 그대로** 둔다.
    그 값이 ``compact_raw`` 로 나가기 때문이다(설계 결정 0).
    """

    lines: list[RawLine] = field(default_factory=list)
    """문단을 줄 단위로 쪼갠 원문(type=paragraph). 표는 행/셀 2D 로 이미 구조가
    보존돼 있어 비워 둔다."""

    indent: int = 0
    """문단 자체의 들여쓰기 칸 수(type=paragraph). 0 이면 ``to_dict`` 에서 생략한다."""

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(
            {
                "block_id": self.block_id,
                "order": self.order,
                "type": self.type,
                "text": self.text,
                "style": self.style,
                "structure": self.structure,
                "rows": self.rows,
                "cell_lines": self.cell_lines,
                "lines": [l.to_dict() for l in self.lines] or None,
                "indent": self.indent or None,
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


# --------------------------------------------------------------------------- #
# PowerPoint
# --------------------------------------------------------------------------- #
@dataclass
class RawPptShape:
    """슬라이드 위 도형 1개의 물리 정보(텍스트박스 또는 표).

    차트/이미지는 추출 대상이 아니다(텍스트박스/표만). 의미 해석은 후속 LLM 단계의 몫.
    """

    shape_id: str
    """슬라이드-도형 안정 식별자 (예: ``p001_s002``)."""

    order: int
    """슬라이드 내 등장 순서 (1-based)."""

    type: str
    """``text`` 또는 ``table``."""

    name: Optional[str] = None
    """PPT 도형 이름 (예: ``Title 1``). 구조 추론 힌트."""

    text: Optional[str] = None
    """텍스트(type=text). 문단들을 공백으로 결합."""

    rows: Optional[list[list[str]]] = None
    """표 셀 텍스트 2D(type=table)."""

    position: Optional[dict[str, float]] = None
    """도형 위치/크기 (포인트): ``{left, top, width, height}``. 없으면 생략."""

    style: Optional[dict[str, Any]] = None
    """스타일 정보 (placeholder/bold/font_size 등). 비면 생략."""

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(
            {
                "shape_id": self.shape_id,
                "order": self.order,
                "type": self.type,
                "name": self.name,
                "text": self.text,
                "rows": self.rows,
                "position": self.position,
                "style": self.style,
            }
        )


@dataclass
class RawPptSlide:
    """슬라이드 1장의 물리 구조."""

    slide_id: str
    """슬라이드 안정 식별자 (예: ``p001``)."""

    slide_no: int
    """1-based 슬라이드 번호."""

    layout_name: Optional[str] = None
    """슬라이드 레이아웃 이름 (예: ``Title and Content``). 구조 추론 힌트."""

    shapes: list[RawPptShape] = field(default_factory=list)
    notes: Optional[str] = None
    """스피커 노트 텍스트. 없으면 생략."""

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(
            {
                "slide_id": self.slide_id,
                "slide_no": self.slide_no,
                "layout_name": self.layout_name,
                "notes": self.notes,
                "shapes": [s.to_dict() for s in self.shapes],
            }
        )


@dataclass
class RawPptDocument:
    """PPT 파일 1개의 raw json 루트."""

    file_name: str
    slides: list[RawPptSlide] = field(default_factory=list)
    doc_type: str = "ppt"

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_type": self.doc_type,
            "file_name": self.file_name,
            "slides": [s.to_dict() for s in self.slides],
        }


# 디스패처/타입힌트용 합집합.
RawDocument = "RawExcelDocument | RawWordDocument | RawPptDocument"
