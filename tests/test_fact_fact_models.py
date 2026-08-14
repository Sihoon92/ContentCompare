"""Fact 데이터 모델 테스트 — 직렬화/관대 처리(네트워크 불필요)."""

from __future__ import annotations

from contentcompare.fact.fact_models import Attribute, Fact, FactSet


def test_attribute_is_empty():
    assert Attribute().is_empty() is True
    assert Attribute(value=-5).is_empty() is False
    assert Attribute(unit="℃").is_empty() is False


def test_fact_from_llm_parses_value_unit_attributes():
    fact = Fact.from_llm(
        {
            "fact_type": "quantitative_spec",
            "entity_name": "충전환경온도",
            "attributes": {
                "lower_limit": {"value": -5, "unit": "℃"},
                "upper_limit": {"value": 55, "unit": "℃"},
            },
            "evidence_text": "-5 ~ 55 ℃",
            "confidence": "0.8",  # 문자열도 관대 처리
        }
    )
    assert fact.entity_name == "충전환경온도"
    assert fact.attributes["lower_limit"].value == -5
    assert fact.attributes["lower_limit"].unit == "℃"
    assert fact.confidence == 0.8


def test_fact_from_llm_coerces_scalar_attribute():
    """attribute 값이 {value,unit} dict 가 아니면 value 로 보정."""
    fact = Fact.from_llm({"entity_name": "X", "attributes": {"target_value": 25}})
    assert fact.attributes["target_value"].value == 25
    assert fact.attributes["target_value"].unit == ""


def test_fact_from_llm_drops_empty_attributes():
    fact = Fact.from_llm(
        {"entity_name": "X", "attributes": {"lower_limit": {"value": None, "unit": ""}}}
    )
    assert "lower_limit" not in fact.attributes


def test_fact_from_llm_demotes_unknown_fact_type():
    fact = Fact.from_llm({"entity_name": "X", "fact_type": "weird"})
    assert fact.fact_type == "descriptive"


def test_fact_from_llm_builds_entity_path_from_name():
    fact = Fact.from_llm({"entity_name": "충전환경온도"})
    assert fact.entity_path == ["충전환경온도"]


def test_fact_from_llm_keeps_explicit_entity_path():
    fact = Fact.from_llm({"entity_name": "온도", "entity_path": ["기본사양", "충전", "온도"]})
    assert fact.entity_path == ["기본사양", "충전", "온도"]


def test_factset_roundtrip():
    fs = FactSet(
        location="sheet=S",
        facts=[
            Fact(
                fact_id="fact-row-2",
                fact_type="quantitative_spec",
                entity_name="X",
                entity_path=["X"],
                attributes={"lower_limit": Attribute(value=1, unit="℃")},
                search_text="X 1 ℃",
                source={"doc_type": "excel", "row": 2},
                evidence_text="X 1 ℃",
                confidence=0.9,
            )
        ],
    )
    again = FactSet.from_dict(fs.to_dict())
    assert again.location == "sheet=S"
    f = again.facts[0]
    assert f.fact_id == "fact-row-2"
    assert f.entity_name == "X"
    assert f.attributes["lower_limit"].value == 1
    assert f.attributes["lower_limit"].unit == "℃"
    assert f.source["doc_type"] == "excel"


def test_fact_roundtrips_inherited_from():
    """LLM 이 표시한 상속 출처가 산출물까지 왕복한다."""
    from contentcompare.fact.fact_models import Fact

    fact = Fact.from_llm({
        "entity_name": "충전온도범위",
        "attributes": {"lower_limit": {"value": 15, "unit": "℃"}},
        "inherited_from": ["w_b245", 7, "", None],
    })

    assert fact.inherited_from == ["w_b245", "7"]      # 문자열화 + 빈 값 제거
    assert fact.to_dict()["inherited_from"] == ["w_b245", "7"]
    assert Fact.from_dict(fact.to_dict()).inherited_from == ["w_b245", "7"]


def test_fact_without_inherited_from_defaults_empty():
    """옛 산출물을 읽어도 깨지지 않는다."""
    from contentcompare.fact.fact_models import Fact

    assert Fact.from_dict({"entity_name": "x"}).inherited_from == []
