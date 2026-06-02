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
class FieldFinding:
    """행 종합 판정에서 각 열(필드)이 대상 문서에서 어떻게 확인되었는지에 대한 내역.

    개별 같음/다름 판정(verdict)이 아니라 '확인됨(found) + 근거(note)' 수준의 서술이다.
    레코드(행)는 한 번에 종합 판정하므로(요청 1번), 필드는 그 판단의 세부 근거로만 쓰인다.
    """

    field: "FieldClaim"
    found: bool
    """이 항목(열)에 해당하는 내용을 대상 문서(후보)에서 찾았는가."""

    note: str = ""
    """어디서/어떻게 확인했는지(또는 못 찾았는지)에 대한 한 줄 근거."""


@dataclass
class RecordResult:
    """기준 레코드(행) 1건에 대한 **행 단위 종합 판정** 결과(요청 1번).

    한 행의 모든 열을 종합해, 그 내용이 대상 문서에 있는지(verdict)·어디에 있는지
    (matched_item_ids → sources)·왜 그렇게 판단했는지(reasoning)를 한 번에 담는다.
    :attr:`findings` 는 열별 확인 내역(세부 근거)이다.
    """

    record: DocItem
    verdict: Verdict = Verdict.NOT_FOUND
    """행 전체를 종합한 판정."""

    reasoning: str = ""
    """이 행의 내용이 대상의 어디에 왜 있다고(또는 없다고) 판단했는지 종합 서술."""

    candidates: list[Candidate] = field(default_factory=list)
    matched_item_ids: list[str] = field(default_factory=list)
    """행 내용의 근거가 된 후보 item_id 들."""

    findings: list[FieldFinding] = field(default_factory=list)
    """열(필드)별 확인 내역(세부 근거)."""

    @property
    def reference(self) -> DocItem:
        """리포트 호환용 별칭(기준 항목)."""
        return self.record

    @property
    def sources(self) -> list[str]:
        by_id = {c.item.item_id: c.item.source_label for c in self.candidates}
        return [by_id[i] for i in self.matched_item_ids if i in by_id]
