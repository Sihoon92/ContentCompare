"""F1 산출물 모델(DocumentProfile/TableProfile/ColumnSchema) 직렬화/관대 파싱."""

from __future__ import annotations

from contentcompare.fact.schema_models import (
    ColumnSchema,
    ColumnSpec,
    DocumentProfile,
    TableProfile,
)


def test_document_profile_roundtrip():
    d = {
        "doc_type": "excel",
        "main_purpose": "규격 리스트",
        "main_structures": [{"kind": "table", "location": "sheet=S", "purpose": "p", "row_grain_hint": "행=항목"}],
        "confidence": 0.8,
    }
    prof = DocumentProfile.from_dict(d)
    assert prof.doc_type == "excel"
    assert prof.main_structures[0].kind == "table"
    assert prof.to_dict() == d


def test_document_profile_from_llm_fills_doc_type():
    prof = DocumentProfile.from_llm({"main_purpose": "x"}, fallback_doc_type="word")
    assert prof.doc_type == "word"
    assert prof.confidence == 0.0  # 누락 시 기본값


def test_document_profile_tolerates_bad_confidence():
    prof = DocumentProfile.from_dict({"doc_type": "excel", "confidence": "high"})
    assert prof.confidence == 0.0


def test_table_profile_parsing():
    d = {
        "location": "sheet=StandardList",
        "header_structure": {"header_start_row": 3, "header_rows": 1, "data_start_row": 4, "header_depth": 1},
        "row_grain": {"description": "행=규격", "primary_entity_columns": ["E"]},
    }
    tp = TableProfile.from_dict(d)
    assert tp.header_structure.header_start_row == 3
    assert tp.row_grain.primary_entity_columns == ["E"]
    assert tp.to_dict() == d


def test_table_profile_header_rows_defaults_to_one():
    tp = TableProfile.from_dict({"header_structure": {}})
    assert tp.header_structure.header_rows == 1
    assert tp.header_structure.header_depth == 1


def test_column_schema_unknown_role_demoted():
    d = {
        "location": "sheet=S",
        "columns": [
            {"column": "E", "field_name": "항목", "semantic_role": "entity_name", "data_type": "string", "raw_header": ["충전환경온도"]},
            {"column": "F", "field_name": "하한", "semantic_role": "made_up", "data_type": "number", "raw_header": "하한치"},
        ],
    }
    cs = ColumnSchema.from_dict(d)
    assert cs.columns[0].semantic_role == "entity_name"
    assert cs.columns[1].semantic_role == "unknown"  # 미허용 → 강등
    assert cs.columns[1].raw_header == ["하한치"]  # str → list 보정


def test_column_schema_role_of():
    cs = ColumnSchema.from_dict({"columns": [{"column": "F", "semantic_role": "quantitative_lower_bound"}]})
    assert cs.role_of("F") == "quantitative_lower_bound"
    assert cs.role_of("Z") is None


def test_column_spec_defaults():
    spec = ColumnSpec.from_dict({"column": "A"})
    assert spec.semantic_role == "unknown"
    assert spec.data_type == "string"
    assert spec.raw_header == []
