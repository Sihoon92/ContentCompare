"""Record 데이터 모델 테스트 — 직렬화/관대 처리(네트워크 불필요)."""

from __future__ import annotations

from contentcompare.fact.record_models import (
    Entity,
    QuantSpec,
    Record,
    RecordSet,
    RecordSource,
)


def test_entity_from_llm_builds_path_from_nonempty_parts():
    ent = Entity.from_llm({"category": "기본사양", "subcategory": "", "display_name": "충전환경온도"})
    assert ent.path == ["기본사양", "충전환경온도"]  # 빈 subcategory 제외


def test_entity_from_llm_keeps_explicit_path():
    ent = Entity.from_llm({"category": "A", "display_name": "B", "path": ["A", "B"]})
    assert ent.path == ["A", "B"]


def test_quantspec_is_empty():
    assert QuantSpec().is_empty() is True
    assert QuantSpec(lower=-5).is_empty() is False
    assert QuantSpec(unit="℃").is_empty() is False


def test_record_from_llm_is_tolerant_and_fills_id_and_sheet():
    rec = Record.from_llm(
        {
            "entity": {"display_name": "충전환경온도"},
            "quantitative_spec": {"lower": -5, "upper": 55},
            "source": {"row": 4},
            "confidence": "0.9",  # 문자열도 관대 처리
        },
        sheet_name="StandardList",
    )
    assert rec.record_id == "row-4"            # id 미지정 → row 기반 생성
    assert rec.source.sheet == "StandardList"  # sheet 미지정 → 주입값
    assert rec.entity.display_name == "충전환경온도"
    assert rec.quantitative_spec.lower == -5
    assert rec.confidence == 0.9


def test_record_from_llm_drops_empty_quantspec():
    rec = Record.from_llm({"entity": {"display_name": "X"}, "quantitative_spec": {}, "source": {"row": 2}})
    assert rec.quantitative_spec is None  # 빈 정량규격 → None


def test_from_llm_row_less_index_fallback_produces_unique_ids():
    """row 없는 record 에 index 를 주면 row-idx-N 으로 고유 id 를 만든다."""
    rec0 = Record.from_llm({"entity": {"display_name": "A"}}, index=0)
    rec1 = Record.from_llm({"entity": {"display_name": "B"}}, index=1)
    assert rec0.record_id == "row-idx-0"
    assert rec1.record_id == "row-idx-1"
    assert rec0.record_id != rec1.record_id


def test_recordset_roundtrip():
    rs = RecordSet(
        location="sheet=S",
        records=[
            Record(
                record_id="row-2",
                entity=Entity(display_name="X", path=["X"]),
                quantitative_spec=QuantSpec(lower=1, upper=3, unit="℃"),
                source=RecordSource(sheet="S", row=2, cell_range="E2:F2"),
            )
        ],
    )
    again = RecordSet.from_dict(rs.to_dict())
    assert again.location == "sheet=S"
    assert again.records[0].entity.display_name == "X"
    assert again.records[0].quantitative_spec.unit == "℃"
    assert again.records[0].source.cell_range == "E2:F2"
