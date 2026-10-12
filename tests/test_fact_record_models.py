"""Record 데이터 모델 테스트 — attributes 통합/직렬화/관대 처리(네트워크 불필요)."""

from __future__ import annotations

from contentcompare.fact.record_models import (
    Attribute,
    Entity,
    Record,
    RecordSet,
    RecordSource,
    parse_attributes,
    parse_metadata,
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


# --------------------------------------------------------------------------- #
# 와이어 배열 포맷 — strict JSON Schema 는 자유 키 map 을 표현할 수 없다
# --------------------------------------------------------------------------- #
def test_attributes_accept_the_stored_map_shape():
    """저장 포맷(기존 artifacts·golden)은 그대로 읽혀야 한다."""
    got = parse_attributes({"lower_limit": {"value": 1, "unit": "V"}})
    assert got == {"lower_limit": Attribute(value=1, unit="V")}


def test_attributes_accept_the_wire_array_shape():
    got = parse_attributes([{"name": "lower_limit", "value": 1, "unit": "V"}])
    assert got == {"lower_limit": Attribute(value=1, unit="V")}


def test_both_shapes_converge_on_the_same_result():
    """캐시된 산출물과 새로 뽑은 산출물이 갈리면 안 된다 — 그것이 이 설계의 요점이다."""
    as_map = parse_attributes({"a": {"value": 1, "unit": "V"},
                               "b": {"value": "x", "unit": ""}})
    as_list = parse_attributes([{"name": "a", "value": 1, "unit": "V"},
                                {"name": "b", "value": "x", "unit": ""}])
    assert as_map == as_list


def test_duplicate_names_in_the_array_let_the_last_one_win():
    """``json.loads`` 가 중복 키를 다루는 방식과 맞춘다 — 두 모양의 결과가 같아야 한다."""
    got = parse_attributes([{"name": "a", "value": 1, "unit": ""},
                            {"name": "a", "value": 2, "unit": ""}])
    assert got == {"a": Attribute(value=2, unit="")}


def test_nameless_array_items_are_dropped_not_given_a_placeholder_key():
    """빈 키를 만들면 두 번째 무명 항목이 첫 번째를 조용히 덮어쓴다 — 보이지 않는 손실."""
    got = parse_attributes([{"name": "", "value": 1, "unit": ""},
                            {"value": 2, "unit": ""},
                            {"name": " a ", "value": 3, "unit": ""}])
    assert got == {"a": Attribute(value=3, unit="")}


def test_empty_attributes_are_still_excluded_in_the_array_shape():
    got = parse_attributes([{"name": "a", "value": None, "unit": ""},
                            {"name": "b", "value": 0, "unit": ""}])
    assert list(got) == ["b"]


def test_garbage_array_items_are_skipped():
    assert parse_attributes(["문자열", None, 42]) == {}


def test_attributes_reject_neither_map_nor_list():
    assert parse_attributes("문자열") == {}
    assert parse_attributes(None) == {}


def test_metadata_accepts_both_shapes():
    assert parse_metadata({"작성일": "2026-01-01"}) == {"작성일": "2026-01-01"}
    assert parse_metadata([{"name": "작성일", "value": "2026-01-01"}]) == {
        "작성일": "2026-01-01"}
    assert parse_metadata(None) == {}
    assert parse_metadata("문자열") == {}


def test_record_from_llm_accepts_the_wire_array_shape():
    rec = Record.from_llm({
        "record_id": "row-2",
        "entity": {"display_name": "충전환경온도"},
        "attributes": [{"name": "lower_limit", "value": -5, "unit": "C"}],
        "metadata": [{"name": "작성일", "value": "2026-01-01"}],
        "source": {"row": 2},
    }, sheet_name="Sheet1")
    assert rec.attributes == {"lower_limit": Attribute(value=-5, unit="C")}
    assert rec.metadata == {"작성일": "2026-01-01"}


def test_round_trip_still_stores_maps_so_artifacts_do_not_change():
    """저장 포맷이 바뀌면 기존 캐시·golden·리포트가 전부 흔들린다."""
    rec = Record.from_llm({
        "attributes": [{"name": "a", "value": 1, "unit": "V"}],
        "metadata": [{"name": "m", "value": "x"}],
        "source": {"row": 1},
    })
    stored = rec.to_dict()
    assert stored["attributes"] == {"a": {"value": 1, "unit": "V"}}
    assert stored["metadata"] == {"m": "x"}
    assert Record.from_dict(stored).attributes == rec.attributes
