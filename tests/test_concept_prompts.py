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
    정상적인 불일치를 삼켜버린다(설계 §5).

    이 테스트는 원칙 1 문단 자체에만 있는 구체 표현들을 요구한다.
    원칙 1 문단을 지우면 반드시 실패해야 한다."""
    # 원칙 1 의 핵심 문장 — 이 두 구절이 함께 있어야 함
    assert "값이 다른 것은" in CONCEPT_SYSTEM
    assert "근거가 아닙니다" in CONCEPT_SYSTEM
    # 원칙 1 의 구체 예시 — 이 숫자들은 원칙 1 문단에만 있음
    assert "21~29" in CONCEPT_SYSTEM and "-10~80" in CONCEPT_SYSTEM
    # 원칙 1 고유 표현 — "판단 재료"는 원칙 1 문단에만 있음
    assert "판단 재료가" in CONCEPT_SYSTEM


def test_system_prompt_requires_quotes_for_same_as():
    assert "인용" in CONCEPT_SYSTEM


def test_system_prompt_forbids_notation_difference_as_axis():
    """표기 차이(단일값 ↔ 범위)는 differs_by 사유가 아니다.

    원칙 1 은 "값이 다르다"만 금지했는데, LLM 이 같은 추론을 "측정 대상이 다르다"로
    바꿔 말해 빠져나갔다(실측: 중량 ↔ Weight Range 를 '서로 다른 물리량'으로 차단,
    12b 에서도 재현). 그 구멍을 막는 문단이 있어야 한다.
    """
    assert "표기" in CONCEPT_SYSTEM
    assert "min/typ/max" in CONCEPT_SYSTEM
    # 정보량 차이도 개념 차이가 아니라는 단서
    assert "정보량" in CONCEPT_SYSTEM


def test_system_prompt_fixes_axis_vocabulary():
    """축 이름을 고정 4종으로 제한한다 — 자유 서술을 뒤집은 결정.

    예전에는 "정해진 목록은 없습니다 — 이름을 지으세요"였다. 실측에서 differs_by
    767건에 축 이름이 46종류 난립했고(측정대상/측정 대상/측정물/측정물리량/측정항목…
    같은 말이 20종), 이는 LLM 이 차단을 먼저 정하고 이유를 지어낸다는 신호였다.
    """
    for axis in ("대상", "조건", "기간", "방식"):
        assert axis in CONCEPT_SYSTEM
    # 자유 서술 허용 문구는 사라져야 한다
    assert "정해진 목록은 없" not in CONCEPT_SYSTEM


def test_system_prompt_requires_both_sides_on_axis():
    """축 위에서 왼쪽·오른쪽이 각각 무엇인지 적게 한다.

    축 이름만 요구하면 지어내기가 공짜다. 양쪽 값을 적게 하면 표기 차이가 스스로
    드러나 규칙 A 에 걸린다 — 이것이 고정 목록의 실효를 만드는 장치다.
    """
    assert "vs" in CONCEPT_SYSTEM
    # 양쪽 값을 못 적으면 differs_by 가 아니라 unknown 이라는 탈출구 지정
    assert "적을 수 없으면" in CONCEPT_SYSTEM


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
