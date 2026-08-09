"""semantic_role 사전/guess_role/normalize_role 단위테스트."""

from __future__ import annotations

from contentcompare.fact import semantic_roles as sr


def test_quantitative_bounds_korean():
    assert sr.guess_role("하한치") == sr.QUANT_LOWER
    assert sr.guess_role("중심치") == sr.QUANT_TARGET
    assert sr.guess_role("상한치") == sr.QUANT_UPPER


def test_quantitative_bounds_english():
    assert sr.guess_role("Min") == sr.QUANT_LOWER
    assert sr.guess_role("Max Value") == sr.QUANT_UPPER
    assert sr.guess_role("Nominal") == sr.QUANT_TARGET


def test_unit_and_entity():
    assert sr.guess_role("단위") == sr.UNIT
    assert sr.guess_role("항목명") == sr.ENTITY_NAME
    assert sr.guess_role("대분류") == sr.ENTITY_CATEGORY
    assert sr.guess_role("중분류") == sr.ENTITY_SUBCATEGORY


def test_priority_specific_before_general():
    # '소분류'는 subcategory 가 category 보다 우선.
    assert sr.guess_role("소분류") == sr.ENTITY_SUBCATEGORY


def test_unmatched_returns_none():
    assert sr.guess_role("뜬금없는헤더") is None
    assert sr.guess_role("") is None
    assert sr.guess_role(None) is None


def test_normalize_role():
    assert sr.normalize_role("quantitative_lower_bound") == sr.QUANT_LOWER
    assert sr.normalize_role("made_up_role") == sr.UNKNOWN
    assert sr.normalize_role(None) == sr.UNKNOWN


def test_quantitative_value_role_and_hint():
    # 경계가 아닌 단일 정량 값 역할(정격전압/규격값 등).
    assert sr.QUANT_VALUE in sr.SEMANTIC_ROLES
    assert sr.normalize_role("quantitative_value") == sr.QUANT_VALUE
    assert sr.guess_role("정격전압") == sr.QUANT_VALUE
    # 경계 역할이 여전히 우선(회귀 방지).
    assert sr.guess_role("하한치") == sr.QUANT_LOWER


def test_roles_set_integrity():
    # 사전의 모든 역할이 SEMANTIC_ROLES 에 포함된다.
    for role in sr.ROLE_SYNONYMS:
        assert role in sr.SEMANTIC_ROLES
    assert sr.UNKNOWN in sr.SEMANTIC_ROLES
