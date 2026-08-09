"""F5 Fact Store — 문서별 fact 를 한곳에 모아 비교 입력으로 만든다.

기준(Excel) 1 : 대상 N 이 기본이지만(결정 #4), 어느 문서든 기준이 될 수 있도록
문서를 같은 :class:`DocFacts` 로 담고 기준/대상 구분만 밖에서 준다 — N:N 확장 시
:class:`FactStore` 만 바꾸면 되고 Matcher/Comparator 는 그대로다.

F4a validator 의 ``low_confidence_ids`` 를 문서와 함께 들고 다닌다. F5 가
``unknown`` 판정 근거로 쓰기 때문이다(§6.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .fact_models import Fact, FactSet


@dataclass
class DocFacts:
    """한 문서의 fact 묶음 + 그 문서에 대한 검증 결과."""

    doc_name: str
    """사람이 읽는 문서 이름(리포트 표기용). 보통 basename."""

    doc_type: str = ""
    facts: FactSet = field(default_factory=FactSet)
    low_confidence_ids: set[str] = field(default_factory=set)
    """F4a 가 ``error`` 로 표시한 fact id — 신뢰도가 낮아 단정하면 안 되는 것들."""

    def is_low_confidence(self, fact: Optional[Fact]) -> bool:
        return bool(fact) and fact.fact_id in self.low_confidence_ids


@dataclass
class FactStore:
    """비교 한 번에 참여하는 문서 전체."""

    reference: Optional[DocFacts] = None
    targets: list[DocFacts] = field(default_factory=list)

    def add(self, doc: DocFacts, *, is_reference: bool = False) -> None:
        if is_reference:
            self.reference = doc
        else:
            self.targets.append(doc)

    @property
    def ready(self) -> bool:
        """비교를 시작할 수 있는가(기준과 대상이 최소 1개씩 있고 기준 fact 가 있는가)."""
        return bool(self.reference and self.reference.facts.facts and self.targets)

    def summary(self) -> dict:
        return {
            "reference": self.reference.doc_name if self.reference else None,
            "reference_facts": len(self.reference.facts.facts) if self.reference else 0,
            "targets": {d.doc_name: len(d.facts.facts) for d in self.targets},
        }
