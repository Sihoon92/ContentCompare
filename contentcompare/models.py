"""핵심 데이터 모델.

문서에서 추출한 비교 단위(:class:`DocItem`), 임베딩 검색 후보(:class:`Candidate`),
그리고 최종 비교 결과(:class:`ComparisonResult`)를 정의한다.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class DocType(str, enum.Enum):
    EXCEL = "excel"
    WORD = "word"
    PPT = "ppt"
    UNKNOWN = "unknown"


@dataclass
class DocItem:
    """문서에서 추출된 하나의 비교 단위(엑셀 row, 단락, 표 셀, 도형 텍스트 등)."""

    item_id: str
    """문서 내에서 안정적으로 식별되는 고유 ID (예: ``문서A.docx#p12``)."""

    doc_id: str
    """문서 식별자(보통 파일명)."""

    doc_type: DocType
    text: str
    """임베딩/비교에 사용하는 정규화된 텍스트."""

    source_label: str
    """사람이 읽는 출처 라벨 (예: ``문서A.docx > 2페이지 > 3번째 단락``)."""

    locator: dict[str, Any] = field(default_factory=dict)
    """기계용 위치 정보 (sheet/row/col, slide, paragraph index 등)."""

    raw: dict[str, Any] = field(default_factory=dict)
    """원본 값/메타데이터 (셀 원본값, 숫자 포맷 등)."""

    def is_empty(self) -> bool:
        return not self.text or not self.text.strip()


@dataclass
class Candidate:
    """임베딩 유사도 검색으로 찾은 후보."""

    item: DocItem
    score: float
    """코사인 유사도 등 [0,1] 범위 점수."""


class Verdict(str, enum.Enum):
    SAME = "same"            # 정량/정성 내용이 동일
    DIFFERENT = "different"  # 관련 내용은 있으나 다름
    PARTIAL = "partial"      # 일부만 일치
    NOT_FOUND = "not_found"  # 관련 내용을 찾지 못함


@dataclass
class ComparisonResult:
    """기준 항목 1건에 대한 비교 결과."""

    reference: DocItem
    verdict: Verdict
    reasoning: str
    """LLM 이 서술한 판단 근거(다른 이유 / 같다는 근거)."""

    candidates: list[Candidate] = field(default_factory=list)
    """LLM 에 투입된 후보들(점수 포함)."""

    matched_item_ids: list[str] = field(default_factory=list)
    """LLM 이 '관련 있다/같다'고 지목한 후보 item_id 들."""

    @property
    def sources(self) -> list[str]:
        """매칭된 후보들의 출처 라벨 목록."""
        by_id = {c.item.item_id: c.item.source_label for c in self.candidates}
        return [by_id[i] for i in self.matched_item_ids if i in by_id]
