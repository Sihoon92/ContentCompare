"""F7 개념 판정 프롬프트 테스트 — 문자열 조립만."""

from contentcompare.fact.concept_builder import CandidatePair
from contentcompare.fact.fact_models import Fact
from contentcompare.fact.prompts import CONCEPT_SYSTEM, build_concept_user
from contentcompare.fact.record_models import Attribute


def _pair() -> CandidatePair:
    left = Fact(fact_id="fact-row-20", entity_name="1개월저장온도",
                attributes={"lower_limit": Attribute(value=-10.0, unit="")},
                evidence_text="-10.0, 35.0, 80.0")
    right = Fact(fact_id="fact-word-11", entity_name="표준환경온도",
                 attributes={"lower_limit": Attribute(value=21, unit="℃")},
                 evidence_text="표준환경온도, 21 ~ 29 (중심 25), ℃")
    return CandidatePair("기준.xlsx", left, "규격서.docx", right, score=0.61)


def test_system_prompt_forbids_value_based_reasoning():
    """값이 다른 것은 differs_by 의 근거가 아니다 — 이게 빠지면 개념 층이
    정상적인 불일치를 삼켜버린다(설계 §5)."""
    assert "값" in CONCEPT_SYSTEM
    assert "same_as" in CONCEPT_SYSTEM and "differs_by" in CONCEPT_SYSTEM
    assert "unknown" in CONCEPT_SYSTEM


def test_system_prompt_requires_quotes_for_same_as():
    assert "인용" in CONCEPT_SYSTEM


def test_system_prompt_does_not_fix_axis_vocabulary():
    """축 이름은 도메인마다 다르므로 목록을 고정하지 않는다."""
    assert "정해진 목록은 없" in CONCEPT_SYSTEM


def test_user_prompt_contains_both_facts_and_ids():
    text = build_concept_user([_pair()])
    for token in ("fact-row-20", "fact-word-11", "1개월저장온도", "표준환경온도"):
        assert token in text


def test_user_prompt_includes_units_and_evidence():
    text = build_concept_user([_pair()])
    assert "℃" in text and "표준환경온도, 21 ~ 29" in text


def test_user_prompt_includes_context_when_given():
    text = build_concept_user([_pair()], knowledge="용어: 셀=배터리",
                              purpose="배터리 셀 규격 정의",
                              ontology_summary="- 가 / 나 → same_as")
    assert "셀=배터리" in text and "배터리 셀 규격 정의" in text and "가 / 나" in text


def test_user_prompt_omits_empty_context_sections():
    text = build_concept_user([_pair()])
    assert "참고자료" not in text


def test_multiple_pairs_are_numbered():
    text = build_concept_user([_pair(), _pair()])
    assert "[쌍 1]" in text and "[쌍 2]" in text
