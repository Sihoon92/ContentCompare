"""F3 산출물 데이터 모델 — Attribute / Fact / FactSet.

F1/F2 모델 패턴(``to_dict``/``from_dict``/``from_llm``)을 따른다. ``from_llm`` 은 LLM
원본 dict(키 누락·타입오류·미허용 값)에 관대하다. 단, ``fact_id``/``source``/
``search_text`` 는 신뢰하지 않는다(추출기 코드가 최종 결정한다 — 설계 §3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .fact_types import FT_DESCRIPTIVE, normalize_fact_type
from .record_models import Attribute, parse_attributes


def _as_str(v: Any, default: str = "") -> str:
    return v if isinstance(v, str) else (default if v is None else str(v))


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@dataclass
class Fact:
    fact_id: str = ""
    fact_type: str = FT_DESCRIPTIVE
    entity_name: str = ""
    entity_path: list[str] = field(default_factory=list)
    attributes: dict[str, Attribute] = field(default_factory=dict)
    search_text: str = ""
    source: dict[str, Any] = field(default_factory=dict)  # doc_type 별 locator(§4.3)
    evidence_text: str = ""
    confidence: float = 0.0
    inherited_from: list[str] = field(default_factory=list)
    """다른 블록·줄의 레이블을 이어받아 조건을 채웠으면 그 ``[id]`` 들.

    ``decided_by``·``quote_verified`` 와 같은 원리다 — **추론한 것은 추론했다고
    남긴다.** 사람이 이 목록이 붙은 fact 만 골라 전수 검수할 수 있어야, LLM 에게
    상속을 허용한 대가를 관리할 수 있다.
    """

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "fact_type": self.fact_type,
            "entity_name": self.entity_name,
            "entity_path": self.entity_path,
            "attributes": {k: a.to_dict() for k, a in self.attributes.items()},
            "search_text": self.search_text,
            "source": self.source,
            "evidence_text": self.evidence_text,
            "confidence": self.confidence,
            "inherited_from": self.inherited_from,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Fact":
        d = d or {}
        path = [_as_str(p) for p in (d.get("entity_path") or []) if p]
        src = d.get("source")
        return cls(
            fact_id=_as_str(d.get("fact_id")),
            fact_type=_as_str(d.get("fact_type"), FT_DESCRIPTIVE),
            entity_name=_as_str(d.get("entity_name")),
            entity_path=path,
            attributes=parse_attributes(d.get("attributes")),
            search_text=_as_str(d.get("search_text")),
            source=src if isinstance(src, dict) else {},
            evidence_text=_as_str(d.get("evidence_text")),
            confidence=_as_float(d.get("confidence")),
            inherited_from=[
                _as_str(i) for i in (d.get("inherited_from") or []) if _as_str(i)
            ],
        )

    @classmethod
    def from_llm(cls, d: dict) -> "Fact":
        d = d if isinstance(d, dict) else {}
        fact = cls.from_dict(d)
        fact.fact_type = normalize_fact_type(fact.fact_type)
        # entity_path 미지정 시 entity_name 으로 최소 경로 생성.
        if not fact.entity_path and fact.entity_name:
            fact.entity_path = [fact.entity_name]
        return fact


@dataclass
class FactSet:
    location: str = ""
    facts: list[Fact] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"location": self.location, "facts": [f.to_dict() for f in self.facts]}

    @classmethod
    def from_dict(cls, d: dict) -> "FactSet":
        d = d or {}
        facts = [Fact.from_dict(f) for f in (d.get("facts") or []) if isinstance(f, dict)]
        return cls(location=_as_str(d.get("location")), facts=facts)
