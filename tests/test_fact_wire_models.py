"""와이어 모델(`fact/wire_models.py`) + 경계층(`fact/schemas.py`) 테스트.

⚠️ pydantic 이 필요하므로 없는 환경(`.venv`)에서는 **통째로 skip** 된다. 그 환경에서
`schema_for()` 가 `None` 을 돌려주는 것 자체는 `test_schema_for_degrades_without_pydantic`
이 아니라 970개 기존 테스트가 그대로 통과하는 것으로 증명된다 — 구조화 출력이 꺼지면
오늘과 완전히 같은 동작이기 때문이다.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

from contentcompare.fact import wire_models                      # noqa: E402
from contentcompare.fact.schemas import STAGES, schema_for       # noqa: E402
from contentcompare.fact.concept_models import RELATIONS         # noqa: E402
from contentcompare.fact.fact_types import FACT_TYPES            # noqa: E402
from contentcompare.fact.semantic_roles import SEMANTIC_ROLES    # noqa: E402


def _literal_values(model, field: str) -> tuple:
    """모델 필드의 ``Literal[...]`` 값들. 스키마에서 읽어 pydantic 내부 API 를 안 쓴다."""
    return tuple(model.model_json_schema()["properties"][field]["enum"])


def _literal_values_in_defs(model, ref: str, field: str) -> tuple:
    schema = model.model_json_schema()
    return tuple(schema["$defs"][ref]["properties"][field]["enum"])


# --------------------------------------------------------------------------- #
# 레지스트리 · 스키마 생성
# --------------------------------------------------------------------------- #
def test_every_stage_has_a_model():
    """``STAGES`` 와 ``MODEL_FOR`` 가 갈리면 그 단계만 조용히 구조화 출력이 꺼진다."""
    assert set(STAGES) == set(wire_models.MODEL_FOR)


@pytest.mark.parametrize("stage", STAGES)
def test_every_stage_builds_a_strict_schema(stage):
    """strict 규격 위반은 ``strict_schema`` 가 ValueError 로 올린다 — 여기서 죽어야 한다."""
    schema = schema_for(stage)
    assert schema is not None
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["title"] == stage
    assert schema["required"] == list(schema["properties"])


def _schema_nodes(node, *, path="$"):
    """스키마 **노드**만 훑는다(``properties``/``$defs`` 의 *키* 는 이름이지 키워드가 아니다).

    단순 문자열 검색으로는 안 된다 — ``WireRowGrain.description`` 처럼 ``description``
    이라는 **정당한 데이터 필드**가 있어서 속성 이름까지 잡힌다.
    """
    if isinstance(node, list):
        for i, item in enumerate(node):
            yield from _schema_nodes(item, path=f"{path}[{i}]")
        return
    if not isinstance(node, dict):
        return
    yield path, node
    for key, value in node.items():
        if key in ("properties", "$defs", "definitions") and isinstance(value, dict):
            for name, sub in value.items():
                yield from _schema_nodes(sub, path=f"{path}.{key}.{name}")
        elif key in ("items", "anyOf", "oneOf", "allOf", "prefixItems", "not"):
            yield from _schema_nodes(value, path=f"{path}.{key}")


@pytest.mark.parametrize("stage", STAGES)
def test_no_docstring_noise_reaches_the_wire(stage):
    """이 파일의 독스트링은 길다 — 그것이 매 요청에 실리면 안 된다.

    실측으로 6개 스키마 합계가 9654자 → 6630자(31% 감소)였고, F2 는 배치마다 나가므로
    배치 수만큼 곱해진다. 내용도 모델 지시가 아니라 우리 구현 노트다.
    """
    schema = schema_for(stage)
    for path, node in _schema_nodes(schema):
        assert "description" not in node, f"{path} 에 독스트링이 실렸다"
        if path == "$":
            assert node["title"] == stage      # 봉투 name 과 짝이라 루트만 남는다
        else:
            assert "title" not in node, f"{path} 에 title 이 남았다"


def test_unknown_stage_returns_none_instead_of_raising():
    assert schema_for("존재하지않는단계") is None


def test_schema_is_cached_so_hundreds_of_calls_are_free():
    assert schema_for("record") is schema_for("record")


# --------------------------------------------------------------------------- #
# 어휘 일치 — Literal 을 손으로 적었으므로 여기서 고정한다
# --------------------------------------------------------------------------- #
def test_semantic_role_literal_matches_the_vocabulary():
    """``WireColumnSpec.semantic_role`` 이 ``SEMANTIC_ROLES`` 와 어긋나면 strict 가
    **정상 값을 거절**한다 — 코드는 받아들이는데 서버가 막는 상태가 된다."""
    got = _literal_values_in_defs(wire_models.SchemaResponse, "WireColumnSpec",
                                  "semantic_role")
    assert set(got) == set(SEMANTIC_ROLES)


def test_fact_type_literal_matches_the_vocabulary():
    got = _literal_values_in_defs(wire_models.FactsResponse, "WireFact", "fact_type")
    assert set(got) == set(FACT_TYPES)


def test_relation_literal_matches_the_vocabulary():
    got = _literal_values_in_defs(wire_models.ConceptResponse, "WirePairVerdict",
                                  "relation")
    assert set(got) == set(RELATIONS)


def test_compare_result_literal_matches_the_comparator_vocabulary():
    from contentcompare.fact.fact_comparator import _RESULTS

    assert set(_literal_values(wire_models.CompareResponse, "result")) == set(_RESULTS)


def test_finding_result_excludes_missing():
    """후보 **한 건**에 대한 판정이라 ``missing`` 이 없다 — 그건 종합 판정에서만 뜻이 있다."""
    got = _literal_values_in_defs(wire_models.CompareResponse, "WireFinding", "result")
    assert "missing" not in got
    assert set(got) == {"match", "mismatch", "unknown"}


def test_fields_without_a_code_side_normalizer_stay_free_strings():
    """``kind``/``data_type`` 를 enum 으로 좁히면 모델이 **거짓말을 하도록 강제**된다 —
    "차트"를 만났을 때 정직하게 새 낱말을 쓰는 대신 ``table`` 이라고 답하게 된다."""
    profiler = wire_models.ProfilerResponse.model_json_schema()
    kind = profiler["$defs"]["WireMainStructure"]["properties"]["kind"]
    assert kind["type"] == "string" and "enum" not in kind

    schema = wire_models.SchemaResponse.model_json_schema()
    data_type = schema["$defs"]["WireColumnSpec"]["properties"]["data_type"]
    assert data_type["type"] == "string" and "enum" not in data_type


# --------------------------------------------------------------------------- #
# 와이어 모델이 **따로 있는 이유** 자체를 고정한다
# --------------------------------------------------------------------------- #
#: 단계 → 코드가 소유하므로 와이어에 있으면 안 되는 필드.
#: strict 는 모든 속성을 required 로 만들므로, 여기 남은 필드는 "무시됨"이 아니라
#: **"LLM 이 반드시 지어내야 함"** 이 된다.
_CODE_OWNED = {
    "fact": ("fact_id", "source", "search_text"),
    "record": ("sheet", "cell_range"),
    "schema": ("location",),
}


@pytest.mark.parametrize("stage,banned", sorted(_CODE_OWNED.items()))
def test_code_owned_fields_never_appear_on_the_wire(stage, banned):
    """저장 모델과 합치는 리팩터링이 **여기서 죽어야 한다.**

    ``fact_id`` 가 와이어로 돌아오면 LLM 이 만든 id 를 코드가 덮어쓰는 상태가 상시화되고,
    좌표(``cell_range``)는 ``record_normalizer`` 가 "LLM 이 준 좌표는 신뢰하지 않음"이라고
    명시적으로 막은 것이다. 어떤 런타임 테스트도 그 상태를 못 본다.

    **속성 이름으로** 검사한다 — 문자열 검색은 ``source`` 가 ``source_ids`` 에 걸리는 식의
    오탐/누락이 난다.
    """
    for path, node in _schema_nodes(schema_for(stage)):
        names = node.get("properties")
        if not isinstance(names, dict):
            continue
        for field in banned:
            assert field not in names, f"{path} 에 코드 소유 필드 {field!r} 가 있다"


def test_optional_fields_allow_null_so_the_model_can_say_it_does_not_know():
    """strict 는 모든 속성을 required 로 만든다 — null 을 못 쓰면 행 번호를 지어낸다."""
    source = schema_for("record")["$defs"]["WireRecordSource"]
    assert {"type": "null"} in source["properties"]["row"]["anyOf"]

    header = schema_for("schema")["$defs"]["WireHeaderStructure"]
    assert {"type": "null"} in header["properties"]["header_start_row"]["anyOf"]


def test_attributes_are_arrays_not_free_key_maps():
    """자유 키 map 은 strict 로 표현할 수 없다. 저장은 계속 map 이고 변환은
    ``record_models.parse_attributes`` 한 곳에서만 일어난다."""
    for stage, holder in (("record", "WireRecord"), ("fact", "WireFact")):
        attrs = schema_for(stage)["$defs"][holder]["properties"]["attributes"]
        assert attrs["type"] == "array"
        assert attrs["items"] == {"$ref": "#/$defs/WireAttribute"}


def test_cell_value_keeps_numbers_as_numbers():
    """문자열로 통일하면 ``123`` 이 ``"123"`` 이 되어 artifacts·golden 에 diff 가 생긴다."""
    value = schema_for("record")["$defs"]["WireAttribute"]["properties"]["value"]
    assert {"type": "number"} in value["anyOf"]
    assert {"type": "string"} in value["anyOf"]
    assert {"type": "null"} in value["anyOf"]
