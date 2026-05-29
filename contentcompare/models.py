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
class FieldClaim:
    """엑셀 셀 하나 = 하나의 검증 대상 주장(예: 매출액=1,200).

    레코드(행)는 검색 단위, 필드(셀)는 판정 단위다(엑셀 hybrid 분해, 기획).
    """

    field_id: str
    """문서 내 안정 식별자 (예: ``기준.xlsx#Sheet1!D5``)."""

    header: str
    """컬럼 헤더 라벨(다단 헤더는 ``2024>매출`` 처럼 결합)."""

    value_raw: Any
    """원본 셀 값(숫자/문자/날짜 등)."""

    value_norm: str
    """비교용 정규화 문자열(콤마/통화기호 제거, 단위 보존)."""

    cell_ref: str
    """A1 표기 셀 주소 (예: ``D5``)."""


@dataclass
class RecordItem(DocItem):
    """엑셀 한 행을 나타내는 검색 단위. 내부에 셀 단위 :class:`FieldClaim` 들을 가진다.

    :class:`DocItem` 을 그대로 상속하므로 임베딩/검색 파이프라인에서는 일반 항목처럼
    다뤄지고, 필드별 판정(Phase 3)에서는 ``fields`` 를 사용한다.
    """

    key_context: str = ""
    """행을 식별하는 키 컬럼 문맥 (예: ``[제품명=A, 연도=2023]``)."""

    fields: list[FieldClaim] = field(default_factory=list)
    """비교 대상 셀(주장)들."""


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


@dataclass
class FieldResult:
    """필드(셀) 1건에 대한 판정 결과 (엑셀 hybrid 판정 단위)."""

    field: "FieldClaim"
    verdict: Verdict
    reasoning: str
    matched_item_ids: list[str] = field(default_factory=list)


@dataclass
class RecordResult:
    """기준 레코드(행) 1건에 대한 비교 결과 — 필드별 판정들의 묶음.

    리포트/요약에서는 :attr:`verdict`(필드 판정 집계)로 레코드 단위 상태를 보여준다.
    """

    record: DocItem
    fields: list[FieldResult] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def verdict(self) -> Verdict:
        """필드 판정들을 레코드 단위로 집계."""
        if not self.fields:
            return Verdict.NOT_FOUND
        counts = {v: 0 for v in Verdict}
        for fr in self.fields:
            counts[fr.verdict] += 1
        total = len(self.fields)
        if counts[Verdict.SAME] == total:
            return Verdict.SAME
        if counts[Verdict.NOT_FOUND] == total:
            return Verdict.NOT_FOUND
        if counts[Verdict.SAME] == 0 and counts[Verdict.PARTIAL] == 0 and counts[Verdict.DIFFERENT] > 0:
            return Verdict.DIFFERENT
        return Verdict.PARTIAL

    @property
    def matched_item_ids(self) -> list[str]:
        """모든 필드에서 매칭된 후보 id 의 합집합(순서 보존)."""
        seen: list[str] = []
        for fr in self.fields:
            for i in fr.matched_item_ids:
                if i not in seen:
                    seen.append(i)
        return seen

    @property
    def reference(self) -> DocItem:
        """리포트 호환용 별칭(기준 항목)."""
        return self.record

    @property
    def sources(self) -> list[str]:
        by_id = {c.item.item_id: c.item.source_label for c in self.candidates}
        return [by_id[i] for i in self.matched_item_ids if i in by_id]
