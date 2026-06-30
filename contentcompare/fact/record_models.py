"""F2 산출물 데이터 모델 — Record / Entity / QuantSpec / RecordSource / RecordSet.

F1 ``schema_models`` 패턴을 따른다: ``to_dict``(저장용), ``from_dict``(저장본 로드),
``from_llm``(LLM 원본 dict — 키 누락·타입오류에 관대). LLM 산출 record 를 코드가
안전하게 받아 다운스트림(F3 Fact Extractor)이 그대로 쓰게 한다.
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
class QuantSpec:
    lower: Any = None
    target: Any = None
    upper: Any = None
    unit: str = ""

    def is_empty(self) -> bool:
        return self.lower is None and self.target is None and self.upper is None and not self.unit

    def to_dict(self) -> dict[str, Any]:
        return {"lower": self.lower, "target": self.target, "upper": self.upper, "unit": self.unit}

    @classmethod
    def from_dict(cls, d: dict) -> "QuantSpec":
        d = d or {}
        return cls(
            lower=d.get("lower"),
            target=d.get("target"),
            upper=d.get("upper"),
            unit=_as_str(d.get("unit")),
        )


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
    quantitative_spec: Optional[QuantSpec] = None
    qualitative_spec: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    source: RecordSource = field(default_factory=RecordSource)
    evidence_text: str = ""
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "entity": self.entity.to_dict(),
            "quantitative_spec": self.quantitative_spec.to_dict() if self.quantitative_spec else None,
            "qualitative_spec": self.qualitative_spec,
            "metadata": self.metadata,
            "source": self.source.to_dict(),
            "evidence_text": self.evidence_text,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Record":
        d = d or {}
        qs = d.get("quantitative_spec")
        meta = d.get("metadata")
        return cls(
            record_id=_as_str(d.get("record_id")),
            entity=Entity.from_dict(d.get("entity") or {}),
            quantitative_spec=QuantSpec.from_dict(qs) if isinstance(qs, dict) else None,
            qualitative_spec=_as_str(d.get("qualitative_spec")),
            metadata=meta if isinstance(meta, dict) else {},
            source=RecordSource.from_dict(d.get("source") or {}),
            evidence_text=_as_str(d.get("evidence_text")),
            confidence=_as_float(d.get("confidence")),
        )

    @classmethod
    def from_llm(cls, d: dict, *, sheet_name: str = "", index=None) -> "Record":
        d = d if isinstance(d, dict) else {}
        qs = d.get("quantitative_spec")
        quant = QuantSpec.from_dict(qs) if isinstance(qs, dict) else None
        if quant is not None and quant.is_empty():
            quant = None
        source = RecordSource.from_dict(d.get("source") or {})
        if not source.sheet:
            source.sheet = sheet_name
        meta = d.get("metadata")
        rec = cls(
            record_id=_as_str(d.get("record_id")),
            entity=Entity.from_llm(d.get("entity") or {}),
            quantitative_spec=quant,
            qualitative_spec=_as_str(d.get("qualitative_spec")),
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
