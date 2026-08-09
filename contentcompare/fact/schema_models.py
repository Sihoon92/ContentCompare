"""F1 산출물 데이터 모델 — Document Profile / Table Profile / Column Schema.

각 모델은 ``to_dict``(저장용 순수 dict)와 ``from_dict``(저장본 로드), ``from_llm``
(LLM 원본 dict — 키 누락·오타·미허용 값에 관대)을 제공한다. ``from_llm`` 은 알 수 없는
semantic_role 을 ``unknown`` 으로 강등한다(F4 검증 전 안전 강등).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .semantic_roles import normalize_role


def _as_str(v: Any, default: str = "") -> str:
    return v if isinstance(v, str) else (default if v is None else str(v))


def _as_int(v: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Document Profile
# --------------------------------------------------------------------------- #
@dataclass
class MainStructure:
    kind: str  # "table" | "text" | ...
    location: str
    purpose: str = ""
    row_grain_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "location": self.location,
            "purpose": self.purpose,
            "row_grain_hint": self.row_grain_hint,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MainStructure":
        return cls(
            kind=_as_str(d.get("kind")),
            location=_as_str(d.get("location")),
            purpose=_as_str(d.get("purpose")),
            row_grain_hint=_as_str(d.get("row_grain_hint")),
        )


@dataclass
class DocumentProfile:
    doc_type: str
    main_purpose: str = ""
    main_structures: list[MainStructure] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_type": self.doc_type,
            "main_purpose": self.main_purpose,
            "main_structures": [m.to_dict() for m in self.main_structures],
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DocumentProfile":
        structs = [MainStructure.from_dict(m) for m in d.get("main_structures") or [] if isinstance(m, dict)]
        try:
            conf = float(d.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        return cls(
            doc_type=_as_str(d.get("doc_type")),
            main_purpose=_as_str(d.get("main_purpose")),
            main_structures=structs,
            confidence=conf,
        )

    @classmethod
    def from_llm(cls, d: dict, *, fallback_doc_type: str = "") -> "DocumentProfile":
        prof = cls.from_dict(d if isinstance(d, dict) else {})
        if not prof.doc_type:
            prof.doc_type = fallback_doc_type
        return prof


# --------------------------------------------------------------------------- #
# Table Profile
# --------------------------------------------------------------------------- #
@dataclass
class HeaderStructure:
    header_start_row: Optional[int] = None
    header_rows: int = 1
    data_start_row: Optional[int] = None
    header_depth: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "header_start_row": self.header_start_row,
            "header_rows": self.header_rows,
            "data_start_row": self.data_start_row,
            "header_depth": self.header_depth,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "HeaderStructure":
        d = d or {}
        return cls(
            header_start_row=_as_int(d.get("header_start_row")),
            header_rows=_as_int(d.get("header_rows"), 1) or 1,
            data_start_row=_as_int(d.get("data_start_row")),
            header_depth=_as_int(d.get("header_depth"), 1) or 1,
        )


@dataclass
class RowGrain:
    description: str = ""
    primary_entity_columns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "primary_entity_columns": self.primary_entity_columns,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RowGrain":
        d = d or {}
        cols = [_as_str(c) for c in (d.get("primary_entity_columns") or []) if c]
        return cls(description=_as_str(d.get("description")), primary_entity_columns=cols)


@dataclass
class TableProfile:
    location: str = ""
    header_structure: HeaderStructure = field(default_factory=HeaderStructure)
    row_grain: RowGrain = field(default_factory=RowGrain)

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "header_structure": self.header_structure.to_dict(),
            "row_grain": self.row_grain.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TableProfile":
        d = d or {}
        return cls(
            location=_as_str(d.get("location")),
            header_structure=HeaderStructure.from_dict(d.get("header_structure") or {}),
            row_grain=RowGrain.from_dict(d.get("row_grain") or {}),
        )

    @classmethod
    def from_llm(cls, d: dict, *, location: str = "") -> "TableProfile":
        tp = cls.from_dict(d if isinstance(d, dict) else {})
        if not tp.location:
            tp.location = location
        return tp


# --------------------------------------------------------------------------- #
# Column Schema
# --------------------------------------------------------------------------- #
@dataclass
class ColumnSpec:
    column: str
    field_name: str = ""
    semantic_role: str = "unknown"
    data_type: str = "string"
    raw_header: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "field_name": self.field_name,
            "semantic_role": self.semantic_role,
            "data_type": self.data_type,
            "raw_header": self.raw_header,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ColumnSpec":
        d = d or {}
        raw = d.get("raw_header")
        if isinstance(raw, str):
            raw_list = [raw]
        else:
            raw_list = [_as_str(x) for x in (raw or []) if x is not None]
        return cls(
            column=_as_str(d.get("column")),
            field_name=_as_str(d.get("field_name")),
            semantic_role=normalize_role(d.get("semantic_role")),
            data_type=_as_str(d.get("data_type"), "string") or "string",
            raw_header=raw_list,
        )


@dataclass
class ColumnSchema:
    location: str = ""
    columns: list[ColumnSpec] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"location": self.location, "columns": [c.to_dict() for c in self.columns]}

    @classmethod
    def from_dict(cls, d: dict) -> "ColumnSchema":
        d = d or {}
        cols = [ColumnSpec.from_dict(c) for c in (d.get("columns") or []) if isinstance(c, dict)]
        return cls(location=_as_str(d.get("location")), columns=cols)

    @classmethod
    def from_llm(cls, d: dict, *, location: str = "") -> "ColumnSchema":
        cs = cls.from_dict(d if isinstance(d, dict) else {})
        if not cs.location:
            cs.location = location
        return cs

    def role_of(self, column: str) -> Optional[str]:
        for c in self.columns:
            if c.column == column:
                return c.semantic_role
        return None
