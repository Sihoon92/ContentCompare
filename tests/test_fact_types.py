"""fact_type 어휘 / semantic_role → attribute 매핑 테스트 (네트워크 불필요)."""

from __future__ import annotations

from contentcompare.fact.fact_types import (
    ATTR_LOWER,
    ATTR_TARGET,
    ATTR_UPPER,
    FACT_TYPES,
    FT_DESCRIPTIVE,
    FT_QUALITATIVE,
    FT_QUANTITATIVE,
    ROLE_TO_ATTR,
    normalize_fact_type,
)
from contentcompare.fact.semantic_roles import (
    QUALITATIVE,
    QUANT_LOWER,
    QUANT_TARGET,
    QUANT_UPPER,
)


def test_fact_types_vocabulary():
    assert FACT_TYPES == (FT_QUANTITATIVE, FT_QUALITATIVE, FT_DESCRIPTIVE)


def test_normalize_fact_type_keeps_valid():
    assert normalize_fact_type(FT_QUANTITATIVE) == FT_QUANTITATIVE
    assert normalize_fact_type(FT_QUALITATIVE) == FT_QUALITATIVE


def test_normalize_fact_type_demotes_unknown_to_descriptive():
    assert normalize_fact_type("made_up_type") == FT_DESCRIPTIVE
    assert normalize_fact_type("") == FT_DESCRIPTIVE
    assert normalize_fact_type(None) == FT_DESCRIPTIVE


def test_role_to_attr_maps_only_bound_roles_to_canonical():
    assert ROLE_TO_ATTR[QUANT_LOWER] == ATTR_LOWER
    assert ROLE_TO_ATTR[QUANT_TARGET] == ATTR_TARGET
    assert ROLE_TO_ATTR[QUANT_UPPER] == ATTR_UPPER
    # 정성은 canonical 이 아니라 field_name 을 쓰므로 매핑에 없다(결정 G-2).
    assert QUALITATIVE not in ROLE_TO_ATTR
