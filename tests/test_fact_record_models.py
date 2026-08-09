"""Record 데이터 모델 테스트 — attributes 통합/직렬화/관대 처리(네트워크 불필요)."""

from __future__ import annotations

from contentcompare.fact.record_models import (
    Attribute,
    Entity,
    Record,
    RecordSet,
    RecordSource,
)


def test_entity_from_llm_builds_path_from_nonempty_parts():
    ent = Entity.from_llm({"category": "기본사양", "subcategory": "", "display_name": "충전환경온도"})
    assert ent.path == ["기본사양", "충전환경온도"]  # 빈 subcategory 제외


def test_attribute_from_dict_coerces_scalar_and_reads_dict():
    assert Attribute.from_dict(25).value == 25          # {value,unit} 아니면 원값을 value 로
    a = Attribute.from_dict({"value": -5, "unit": "℃"})
    assert a.value == -5 and a.unit == "℃"


def test_record_from_llm_parses_attributes_and_fills_id_sheet():
    rec = Record.from_llm(
        {
            "entity": {"display_name": "충전환경온도"},
            "attributes": {
                "lower_limit": {"value": -5, "unit": "℃"},
                "정격전압": {"value": 3.7, "unit": "V"},  # 일반 field_name 속성
            },
            "source": {"row": 4},
            "confidence": "0.9",  # 문자열도 관대 처리
        },
        sheet_name="StandardList",
    )
    assert rec.record_id == "row-4"            # id 미지정 → row 기반 생성
    assert rec.source.sheet == "StandardList"  # sheet 미지정 → 주입값
    assert rec.attributes["lower_limit"].value == -5
    assert rec.attributes["정격전압"].value == 3.7   # 다중/일반 속성 무손실
    assert rec.confidence == 0.9


def test_record_from_llm_drops_empty_attributes():
    rec = Record.from_llm(
        {"entity": {"display_name": "X"}, "attributes": {"lower_limit": {"value": None, "unit": ""}}}
    )
    assert "lower_limit" not in rec.attributes  # 빈 속성 → 제외


def test_record_from_llm_preserves_metadata():
    rec = Record.from_llm(
        {"entity": {"display_name": "X"}, "metadata": {"순번": "12"}, "source": {"row": 2}}
    )
    assert rec.metadata == {"순번": "12"}  # 비교 비대상이지만 보존


def test_from_llm_row_less_index_fallback_produces_unique_ids():
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
                attributes={
                    "lower_limit": Attribute(value=1, unit="℃"),
                    "재질": Attribute(value="알루미늄"),
                },
                source=RecordSource(sheet="S", row=2, cell_range="E2:F2"),
            )
        ],
    )
    again = RecordSet.from_dict(rs.to_dict())
    assert again.location == "sheet=S"
    r = again.records[0]
    assert r.entity.display_name == "X"
    assert r.attributes["lower_limit"].unit == "℃"
    assert r.attributes["재질"].value == "알루미늄"
    assert r.source.cell_range == "E2:F2"
