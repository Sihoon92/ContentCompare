"""F2 산출물 데이터 모델 — Record / Entity / Attribute / RecordSource / RecordSet.

F1 ``schema_models`` 패턴을 따른다: ``to_dict``(저장용), ``from_dict``(저장본 로드),
``from_llm``(LLM 원본 dict — 키 누락·타입오류에 관대). LLM 산출 record 를 코드가
안전하게 받아 다운스트림(F3 Fact Extractor)이 그대로 쓰게 한다.

정량/정성 값은 모두 ``attributes: {name -> Attribute(value, unit)}`` 단일 맵으로 담는다
(규격 경계는 canonical 키 lower_limit/target_value/upper_limit, 일반 컬럼은 field_name).
``Attribute`` 는 F3 ``Fact`` 와 공유한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


def _as_str(v: Any, default: str = "") -> str:
    return v if isinstance(v, str) else (default if v is None else str(v))


def _as_int(v: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@dataclass
class Attribute:
    """비교 대상 속성값 — 값 + 단위(가공하지 않음). F2 Record / F3 Fact 공유."""

    value: Any = None
    unit: str = ""

    def is_empty(self) -> bool:
        return self.value is None and not self.unit

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "unit": self.unit}

    @classmethod
    def from_dict(cls, d: Any) -> "Attribute":
        if isinstance(d, dict):
            return cls(value=d.get("value"), unit=_as_str(d.get("unit")))
        # {value, unit} dict 가 아니면 원값을 value 로 보정.
        return cls(value=d)


def parse_attributes(raw: Any) -> dict[str, Attribute]:
    """LLM/저장 dict → {name: Attribute}. 빈 속성은 제외."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Attribute] = {}
    for name, val in raw.items():
        attr = Attribute.from_dict(val)
        if not attr.is_empty():
            out[_as_str(name)] = attr
    return out


@dataclass
class Entity:
    category: str = ""
    subcategory: str = ""
    display_name: str = ""
    path: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "subcategory": self.subcategory,
            "display_name": self.display_name,
            "path": self.path,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Entity":
        d = d or {}
        path = [_as_str(p) for p in (d.get("path") or []) if p]
        return cls(
            category=_as_str(d.get("category")),
            subcategory=_as_str(d.get("subcategory")),
            display_name=_as_str(d.get("display_name")),
            path=path,
        )

    @classmethod
    def from_llm(cls, d: dict) -> "Entity":
        ent = cls.from_dict(d if isinstance(d, dict) else {})
        if not ent.path:
            ent.path = [p for p in (ent.category, ent.subcategory, ent.display_name) if p]
        return ent


@dataclass
class RecordSource:
    sheet: str = ""
    row: Optional[int] = None
    cell_range: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"sheet": self.sheet, "row": self.row, "cell_range": self.cell_range}

    @classmethod
    def from_dict(cls, d: dict) -> "RecordSource":
        d = d or {}
        return cls(
            sheet=_as_str(d.get("sheet")),
            row=_as_int(d.get("row")),
            cell_range=_as_str(d.get("cell_range")),
        )


@dataclass
class Record:
    record_id: str = ""
    entity: Entity = field(default_factory=Entity)
    attributes: dict[str, Attribute] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    source: RecordSource = field(default_factory=RecordSource)
    evidence_text: str = ""
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "entity": self.entity.to_dict(),
            "attributes": {k: a.to_dict() for k, a in self.attributes.items()},
            "metadata": self.metadata,
            "source": self.source.to_dict(),
            "evidence_text": self.evidence_text,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Record":
        d = d or {}
        meta = d.get("metadata")
        return cls(
            record_id=_as_str(d.get("record_id")),
            entity=Entity.from_dict(d.get("entity") or {}),
            attributes=parse_attributes(d.get("attributes")),
            metadata=meta if isinstance(meta, dict) else {},
            source=RecordSource.from_dict(d.get("source") or {}),
            evidence_text=_as_str(d.get("evidence_text")),
            confidence=_as_float(d.get("confidence")),
        )

    @classmethod
    def from_llm(cls, d: dict, *, sheet_name: str = "", index=None) -> "Record":
        d = d if isinstance(d, dict) else {}
        source = RecordSource.from_dict(d.get("source") or {})
        if not source.sheet:
            source.sheet = sheet_name
        meta = d.get("metadata")
        rec = cls(
            record_id=_as_str(d.get("record_id")),
            entity=Entity.from_llm(d.get("entity") or {}),
            attributes=parse_attributes(d.get("attributes")),
            metadata=meta if isinstance(meta, dict) else {},
            source=source,
            evidence_text=_as_str(d.get("evidence_text")),
            confidence=_as_float(d.get("confidence")),
        )
        # record_id 미지정 시 row 기반 생성; row 도 없으면 인덱스 기반 폴백.
        if not rec.record_id:
            if source.row is not None:
                rec.record_id = f"row-{source.row}"
            elif index is not None:
                rec.record_id = f"row-idx-{index}"
            else:
                rec.record_id = "row-?"
        return rec


@dataclass
class RecordSet:
    location: str = ""
    records: list[Record] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"location": self.location, "records": [r.to_dict() for r in self.records]}

    @classmethod
    def from_dict(cls, d: dict) -> "RecordSet":
        d = d or {}
        recs = [Record.from_dict(r) for r in (d.get("records") or []) if isinstance(r, dict)]
        return cls(location=_as_str(d.get("location")), records=recs)
