"""F5 하이브리드 판정 테스트.

핵심 계약: **코드가 단정할 수 있는 건 LLM 을 부르지 않는다.** 그래야 재현성·비용이
지켜지고, ``decided_by`` 계측이 "LLM 이 실제로 어디서 필요한가"를 말해준다.
"""

from __future__ import annotations

import json

import pytest

from contentcompare.fact.fact_comparator import (
    BY_CODE,
    BY_LLM,
    MATCH,
    MISMATCH,
    MISSING,
    UNKNOWN,
    ComparisonProbe,
    FactComparator,
    attribute_coverage,
    canonical_unit,
)
from contentcompare.fact.fact_matcher import EMBED, EXACT, MatchCandidate
from contentcompare.fact.fact_models import Fact, FactSet
from contentcompare.fact.fact_store import DocFacts
from contentcompare.fact.llm_stage import LlmRunner
from contentcompare.fact.record_models import Attribute


def _fact(fid: str, name: str, evidence: str = "근거", **attrs) -> Fact:
    """attrs 는 ``key=(값, 단위)`` 또는 ``key=값``."""
    parsed = {}
    for k, v in attrs.items():
        parsed[k] = Attribute(*v) if isinstance(v, tuple) else Attribute(v, "")
    return Fact(fact_id=fid, entity_name=name, attributes=parsed, evidence_text=evidence)


def _cand(fact: Fact, score: float = 0.9, method: str = EMBED, review: bool = False):
    return [MatchCandidate(fact, score, method, needs_review=review)]


def _target(*facts: Fact, low: set[str] | None = None) -> DocFacts:
    return DocFacts("발표.pptx", "ppt", FactSet(facts=list(facts)), low_confidence_ids=low or set())


class _CountingChat:
    def __init__(self, response: dict):
        self.response = response
        self.calls = 0
        self.prompts: list[str] = []

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        self.prompts.append(user)
        return json.dumps(self.response, ensure_ascii=False)


def _comparator(response: dict | None = None, **kw) -> tuple[FactComparator, _CountingChat]:
    chat = _CountingChat(response or {"result": "unknown", "reason": "판단 보류"})
    return FactComparator(runner=LlmRunner(chat), **kw), chat


# --------------------------------------------------------------------------- #
# 코드 결정적 경로 — LLM 을 부르지 않아야 한다
# --------------------------------------------------------------------------- #
def test_no_candidate_is_missing_without_llm():
    cmp_, chat = _comparator()
    out = cmp_.compare(_fact("r1", "deltaOCV"), [], _target())
    assert out.result == MISSING and out.decided_by == BY_CODE
    assert out.target_fact is None and chat.calls == 0


def test_all_values_equal_is_match_without_llm():
    ref = _fact("r1", "방전환경온도", lower_limit=(-30, ""), target_value=(30, ""), upper_limit=(90, ""))
    tgt = _fact("t1", "방전환경온도", lower_limit=(-30, "℃"), target_value=(30, "℃"), upper_limit=(90, "℃"))
    cmp_, chat = _comparator()
    out = cmp_.compare(ref, _cand(tgt, method=EXACT, score=1.0), _target(tgt))
    assert out.result == MATCH and out.decided_by == BY_CODE and chat.calls == 0
    assert out.match_method == EXACT


def test_value_difference_is_mismatch_with_attribute_names():
    ref = _fact("r1", "충전환경온도", lower_limit=(-5, ""), target_value=(35, ""), upper_limit=(85, ""))
    tgt = _fact("t1", "충전환경온도", lower_limit=(-5, "℃"), target_value=(35, "℃"), upper_limit=(80, "℃"))
    cmp_, chat = _comparator()
    out = cmp_.compare(ref, _cand(tgt), _target(tgt))
    assert out.result == MISMATCH and out.mismatch_attributes == ["upper_limit"]
    assert out.decided_by == BY_CODE and chat.calls == 0
    assert "85" in out.reason and "80" in out.reason


def test_numeric_notation_difference_is_still_match():
    """3 vs 3.0, 1,200 vs 1200 같은 표기 차는 흡수한다."""
    ref = _fact("r1", "방전종지전압", target_value=(3, ""))
    tgt = _fact("t1", "방전 종지 전압", target_value=("3.0", "V"))
    cmp_, chat = _comparator()
    assert cmp_.compare(ref, _cand(tgt), _target(tgt)).result == MATCH
    assert chat.calls == 0


def test_reference_without_unit_matches_target_with_unit():
    """기준 문서의 단위 열이 비어 있는 실제 상황(실측 20 중 16) — 값이 같으면 match."""
    ref = _fact("r1", "표준충전전류", target_value=(230, ""))
    tgt = _fact("t1", "표준충전전류", target_value=(230, "mA"))
    cmp_, _ = _comparator()
    assert cmp_.compare(ref, _cand(tgt), _target(tgt)).result == MATCH


# --------------------------------------------------------------------------- #
# LLM 위임 경로
# --------------------------------------------------------------------------- #
def test_note_absorbed_attribute_is_decided_by_code():
    """PPT 스피커노트가 '충전 규격 상세조건.공칭전압' 처럼 항목을 속성으로 흡수한 실제 사례.

    양쪽 다 단일 속성이므로 키 이름이 달라도 값으로 판정한다(LLM 불필요).
    """
    ref = _fact("r1", "공칭전압", target_value=(3.89, ""))
    tgt = _fact("t1", "충전 규격 상세조건", 공칭전압=(3.89, "V"))
    cmp_, chat = _comparator()
    out = cmp_.compare(ref, _cand(tgt), _target(tgt))
    assert out.result == MATCH and out.decided_by == BY_CODE and chat.calls == 0


def test_single_attribute_pair_matches_despite_different_key_names():
    """실측: 기준이 공칭용량 1150 을 '하한치' 열에 적어 키가 lower_limit 이 됐다.

    양쪽 다 속성이 하나뿐이면 키 이름은 원본 열 위치의 흔적일 뿐이므로 값으로 판정한다.
    """
    ref = _fact("r1", "공칭용량", lower_limit=(1150, ""))
    tgt = _fact("t1", "공칭용량", target_value=(1150, "mAh"))
    cmp_, chat = _comparator()
    out = cmp_.compare(ref, _cand(tgt, method=EXACT, score=1.0), _target(tgt))
    assert out.result == MATCH and out.decided_by == BY_CODE and chat.calls == 0


def test_single_attribute_pair_reports_mismatch_on_different_value():
    ref = _fact("r1", "공칭용량", lower_limit=(1150, ""))
    tgt = _fact("t1", "공칭용량", target_value=(1100, "mAh"))
    cmp_, chat = _comparator()
    out = cmp_.compare(ref, _cand(tgt), _target(tgt))
    assert out.result == MISMATCH and out.mismatch_attributes == ["lower_limit"]
    assert chat.calls == 0


def test_opposite_bounds_are_not_equated_even_when_values_match():
    """'최소 85' 와 '최대 85' 는 값이 같아도 같은 주장이 아니다 → 코드가 단정하지 않는다."""
    ref = _fact("r1", "충전환경온도", lower_limit=(85, "℃"))
    tgt = _fact("t1", "충전환경온도", upper_limit=(85, "℃"))
    cmp_, chat = _comparator({"result": "unknown", "reason": "경계 의미가 반대"})
    out = cmp_.compare(ref, _cand(tgt), _target(tgt))
    assert chat.calls == 1 and out.decided_by == BY_LLM


def test_multiple_attributes_without_shared_key_still_delegated():
    """속성이 여럿인데 키가 안 겹치면 어느 것끼리 비교할지 알 수 없다 → 위임."""
    ref = _fact("r1", "충전환경온도", lower_limit=(-5, ""), upper_limit=(85, ""))
    tgt = _fact("t1", "충전환경온도", 최소=(-5, "℃"), 최대=(85, "℃"))
    cmp_, chat = _comparator({"result": "match", "reason": "같음"})
    cmp_.compare(ref, _cand(tgt), _target(tgt))
    assert chat.calls == 1


def test_decimal_scaled_value_with_unknown_unit_is_delegated():
    """1495(단위불명) vs 1.495A — 1000배 관계면 단위 접두어 차이일 수 있다.

    코드가 '다르다'고 단정하면 실제로는 같은 값을 mismatch 로 오판한다(골든 정답: unknown).
    """
    ref = _fact("r1", "최대충전전류", target_value=(1495, ""))
    tgt = _fact("t1", "최대 충전 전류", target_value=(1.495, "A"))
    cmp_, chat = _comparator({"result": "unknown", "reason": "기준 단위 불명"})
    out = cmp_.compare(ref, _cand(tgt), _target(tgt))
    assert chat.calls == 1 and out.result == UNKNOWN and out.decided_by == BY_LLM


def test_plain_value_difference_with_unknown_unit_is_still_mismatch():
    """배수 관계가 아니면(3.89 vs 3.85) 단위와 무관하게 값이 다른 것이다."""
    ref = _fact("r1", "공칭전압", target_value=(3.89, ""))
    tgt = _fact("t1", "공칭전압", target_value=(3.85, "V"))
    cmp_, chat = _comparator()
    out = cmp_.compare(ref, _cand(tgt), _target(tgt))
    assert out.result == MISMATCH and out.decided_by == BY_CODE and chat.calls == 0


def test_unknown_unit_pair_is_delegated():
    ref = _fact("r1", "무게", target_value=(10, "돈"))     # 사전에 없는 단위
    tgt = _fact("t1", "무게", target_value=(37.5, "g"))
    cmp_, chat = _comparator({"result": "unknown", "reason": "단위 등가 불명"})
    out = cmp_.compare(ref, _cand(tgt), _target(tgt))
    assert chat.calls == 1 and out.result == UNKNOWN and out.decided_by == BY_LLM


def test_borderline_score_forces_llm_even_when_code_could_decide():
    """점수 경계는 코드 판정을 신뢰할 수 없다(실측: 정답 0.697 vs 오매칭 0.700)."""
    ref = _fact("r1", "공칭용량", target_value=(1150, ""))
    tgt = _fact("t1", "공칭용량", target_value=(1150, "mAh"))
    cmp_, chat = _comparator({"result": "missing", "reason": "다른 항목임"})
    out = cmp_.compare(ref, _cand(tgt, score=0.70, review=True), _target(tgt))
    assert chat.calls == 1 and out.result == MISSING and out.target_fact is None


def test_low_confidence_target_forces_llm():
    """F4a 가 error 를 매긴 fact 는 코드 단정 대상에서 뺀다."""
    ref = _fact("r1", "충전환경온도", upper_limit=(85, ""))
    tgt = _fact("t1", "충전환경온도", upper_limit=(85, "℃"))
    cmp_, chat = _comparator({"result": "unknown", "reason": "근거 불명"})
    out = cmp_.compare(ref, _cand(tgt), _target(tgt, low={"t1"}))
    assert chat.calls == 1 and out.decided_by == BY_LLM


def test_llm_choice_limited_to_given_candidates():
    """LLM 이 없는 id 를 지목해도 코드가 준 후보를 벗어나지 않는다."""
    ref = _fact("r1", "충전환경온도", lower_limit=(-5, ""), upper_limit=(85, ""))
    tgt = _fact("t1", "충전 조건", 최소=(-5, "℃"), 최대=(85, "℃"))
    cmp_, _ = _comparator({"result": "match", "target_fact_id": "없는id", "reason": "x"})
    out = cmp_.compare(ref, _cand(tgt), _target(tgt))
    assert out.target_fact.fact_id == "t1"  # 원래 top1 유지


def test_invalid_llm_result_falls_back_to_unknown():
    ref = _fact("r1", "충전환경온도", lower_limit=(-5, ""), upper_limit=(85, ""))
    tgt = _fact("t1", "충전 조건", 최소=(-5, "℃"), 최대=(85, "℃"))
    cmp_, _ = _comparator({"result": "아마도같음", "reason": "?"})
    assert cmp_.compare(ref, _cand(tgt), _target(tgt)).result == UNKNOWN


# --------------------------------------------------------------------------- #
# LLM 을 못 쓸 때 — 버리지 않고 보류
# --------------------------------------------------------------------------- #
def test_llm_disabled_keeps_code_verdict():
    ref = _fact("r1", "충전환경온도", upper_limit=(85, ""))
    tgt = _fact("t1", "충전환경온도", upper_limit=(80, "℃"))
    cmp_ = FactComparator(runner=None, use_llm=False)
    out = cmp_.compare(ref, _cand(tgt), _target(tgt))
    assert out.result == MISMATCH and out.decided_by == BY_CODE


def test_llm_disabled_yields_unknown_when_code_cannot_decide():
    ref = _fact("r1", "충전환경온도", lower_limit=(-5, ""), upper_limit=(85, ""))
    tgt = _fact("t1", "충전 조건", 최소=(-5, "℃"), 최대=(85, "℃"))
    cmp_ = FactComparator(runner=None, use_llm=False)
    out = cmp_.compare(ref, _cand(tgt), _target(tgt))
    assert out.result == UNKNOWN and out.decided_by == BY_CODE


def test_llm_failure_is_isolated_as_unknown():
    class _BadChat:
        calls = 0

        def complete(self, system, user, *, temperature=0.0):
            _BadChat.calls += 1
            return "JSON 아님"

    ref = _fact("r1", "충전환경온도", lower_limit=(-5, ""), upper_limit=(85, ""))
    tgt = _fact("t1", "충전 조건", 최소=(-5, "℃"), 최대=(85, "℃"))
    cmp_ = FactComparator(runner=LlmRunner(_BadChat()))
    out = cmp_.compare(ref, _cand(tgt), _target(tgt))
    assert out.result == UNKNOWN and cmp_.llm_failures == 1


# --------------------------------------------------------------------------- #
# 직렬화 — 양측 근거가 반드시 남아야 한다(사람 검수용)
# --------------------------------------------------------------------------- #
def test_to_dict_carries_both_sides_evidence_and_source():
    ref = _fact("r1", "충전환경온도", "충전환경온도, -5, 35, 85", upper_limit=(85, ""))
    ref.source = {"doc_type": "excel", "sheet": "데이터", "cell_range": "E17:H17"}
    tgt = _fact("t1", "충전환경온도", "충전환경온도, -5, 35, 80, ℃", upper_limit=(80, "℃"))
    tgt.source = {"doc_type": "ppt", "slide_no": 2, "shape_ids": ["p002_s002"]}
    cmp_, _ = _comparator()
    d = cmp_.compare(ref, _cand(tgt), _target(tgt)).to_dict()

    assert d["result"] == MISMATCH and d["mismatch_attributes"] == ["upper_limit"]
    assert d["reference"]["evidence_text"] and d["target"]["evidence_text"]
    assert d["reference"]["source"]["cell_range"] == "E17:H17"
    assert d["target"]["source"]["slide_no"] == 2


# --------------------------------------------------------------------------- #
# 단위 사전
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,expected", [
    ("℃", "c"), ("°C", "c"), ("도씨", "c"), ("degC", "c"), ("", ""),
])
def test_canonical_unit_known(raw, expected):
    """``°C``(도 기호 + C)는 영어 문서에서 흔한 표기 — ``℃``(U+2103)와 다른 문자다."""
    assert canonical_unit(raw) == expected


def test_canonical_unit_unknown_is_none():
    """사전에 없으면 '모름' — 추측하지 않고 LLM 에 넘기기 위한 신호."""
    assert canonical_unit("돈") is None


# --------------------------------------------------------------------------- #
# attribute_coverage — _decide_by_code 가 공통 속성만 보는 구멍을 수치화한다
# --------------------------------------------------------------------------- #
def test_coverage_is_partial_when_target_has_fewer_attributes():
    """기준 3속성 · 대상 1속성 → 그 하나가 같으면 match 지만 커버리지는 1/3."""
    ref = _fact("r", "전지", nominal=("3.85", "V"), upper=("4.55", "V"), lower=("3.0", "V"))
    tgt = _fact("t", "전지", nominal=("3.85", "V"))
    assert attribute_coverage(ref, tgt) == pytest.approx(1 / 3)


def test_coverage_is_full_when_all_reference_attributes_are_covered():
    ref = _fact("r", "전지", nominal=("3.85", "V"))
    tgt = _fact("t", "전지", nominal=("3.85", "V"), extra=("1", ""))
    assert attribute_coverage(ref, tgt) == 1.0


def test_coverage_without_reference_attributes_is_full():
    """비교할 속성이 없으면 커버리지 논의 자체가 무의미하다 — 게이트가 헛돌면 안 된다."""
    ref = _fact("r", "전지")
    tgt = _fact("t", "전지", nominal=("3.85", "V"))
    assert attribute_coverage(ref, tgt) == 1.0


def test_coverage_without_target_is_zero():
    ref = _fact("r", "전지", nominal=("3.85", "V"))
    assert attribute_coverage(ref, None) == 0.0


def test_single_attribute_pair_counts_as_full_regardless_of_key_name():
    """양쪽 속성이 하나씩이면 키 이름이 달라도 같은 항목의 단일 값이다.

    실측 근거는 _compare_single_attributes 와 같다 — 기준이 공칭용량을 '하한치'
    열에 적어 lower_limit 이 됐고 대상은 target_value 였다. **두 함수의 조건이
    같아야 한다** — 어긋나면 같은 쌍을 한쪽은 match, 다른 쪽은 커버리지 0 으로
    채점해 그 match 가 전부 unsafe 로 거부된다(2026-08-10 실측).
    """
    ref = _fact("r", "용량", lower_limit=("1150", "mAh"))
    tgt = _fact("t", "용량", target_value=("1150", "mAh"))
    assert attribute_coverage(ref, tgt) == 1.0


def test_single_attribute_exception_does_not_depend_on_link_provenance():
    """LLM 이 만든 연결이어도 커버리지는 깎지 않는다.

    연결이 불안하다는 신호는 low_confidence 사유가 **이미 따로** 싣는다. 커버리지에
    이중 반영하면 "속성이 안 적혀 있다"와 "연결을 LLM 이 만들었다"가 한 숫자에
    섞여, 그 숫자로 내리려던 판단이 불가능해진다.
    """
    ref = _fact("r", "용량", lower_limit=("1150", "mAh"))
    tgt = _fact("t", "용량", target_value=("1150", "mAh"))
    cmp_, _ = _comparator()
    probe = cmp_.compare_code(ref, _cand(tgt, review=True), _target(tgt))
    assert probe.attribute_coverage == 1.0      # 커버리지는 온전
    assert probe.uncertain is True              # 불안함은 여기로 표현된다


# --------------------------------------------------------------------------- #
# compare_code / finalize 분리 — probe 단계는 LLM 을 절대 부르지 않는다
# --------------------------------------------------------------------------- #
def test_compare_code_never_calls_llm_even_when_uncertain():
    """분리의 핵심 계약. 이게 깨지면 게이트가 채점하기 전에 비용이 나간다."""
    cmp_, chat = _comparator()
    ref = _fact("r", "전지", nominal=("3.85", "V"))
    tgt = _fact("t", "전지", nominal=("3.85", "무슨단위"))   # 단위 미상 → 코드 포기
    probe = cmp_.compare_code(ref, _cand(tgt, review=True), _target(tgt))
    assert chat.calls == 0
    assert probe.code_result is None          # 코드가 판단을 포기한 상태
    assert probe.uncertain is True


def test_compare_code_without_candidates_carries_missing_reason():
    cmp_, chat = _comparator()
    probe = cmp_.compare_code(
        _fact("r", "전지"), [], _target(), missing_reason="개념 연결이 없습니다."
    )
    assert chat.calls == 0
    assert probe.code_result == MISSING
    assert probe.code_reason == "개념 연결이 없습니다."
    assert probe.best is None


def test_compare_code_records_coverage_and_candidates():
    cmp_, _ = _comparator()
    ref = _fact("r", "전지", nominal=("3.85", "V"), upper=("4.55", "V"))
    tgt = _fact("t", "전지", nominal=("3.85", "V"))
    probe = cmp_.compare_code(ref, _cand(tgt), _target(tgt))
    assert probe.code_result == MATCH          # 공통 속성만 보면 일치
    assert probe.attribute_coverage == pytest.approx(0.5)
    assert probe.best is not None and probe.best.fact is tgt


def test_finalize_reproduces_compare():
    """compare() 는 compare_code() + finalize() 의 래퍼여야 한다."""
    cmp_, _ = _comparator()
    ref = _fact("r", "전지", nominal=("3.85", "V"))
    tgt = _fact("t", "전지", nominal=("3.85", "V"))
    direct = cmp_.compare(ref, _cand(tgt), _target(tgt))
    staged = cmp_.finalize(cmp_.compare_code(ref, _cand(tgt), _target(tgt)))
    assert (direct.result, direct.decided_by, direct.reason) == (
        staged.result, staged.decided_by, staged.reason
    )


def test_force_llm_demotes_a_code_match():
    """게이트가 거부한 match 를 LLM 으로 강등하는 경로(enforce 모드)."""
    cmp_, chat = _comparator({"result": "mismatch", "reason": "조건이 다릅니다"})
    ref = _fact("r", "전지", nominal=("3.85", "V"))
    tgt = _fact("t", "전지", nominal=("3.85", "V"))
    probe = cmp_.compare_code(ref, _cand(tgt), _target(tgt))
    assert probe.code_result == MATCH
    out = cmp_.finalize(probe, force_llm=True)
    assert chat.calls == 1
    assert out.result == MISMATCH and out.decided_by == BY_LLM


def test_force_llm_falls_back_to_code_when_llm_is_off():
    """LLM 을 못 쓰면 강등해도 코드 판정을 버리지 않는다(§6.2 보류 원칙)."""
    cmp_ = FactComparator(runner=None, use_llm=False)
    ref = _fact("r", "전지", nominal=("3.85", "V"))
    tgt = _fact("t", "전지", nominal=("3.85", "V"))
    probe = cmp_.compare_code(ref, _cand(tgt), _target(tgt))
    out = cmp_.finalize(probe, force_llm=True)
    assert out.result == MATCH and out.decided_by == BY_CODE


# --------------------------------------------------------------------------- #
# 판정 이력 — 2차 검사가 1차를 조용히 덮지 않게 하는 기반
# --------------------------------------------------------------------------- #
def test_to_dict_adds_history_fields_and_keeps_existing_keys():
    cmp_, _ = _comparator()
    ref = _fact("r", "전지", nominal=("3.85", "V"))
    tgt = _fact("t", "전지", nominal=("3.85", "V"))
    out = cmp_.compare(ref, _cand(tgt), _target(tgt))
    out.initial_result = MATCH
    out.review_triggers = ["duplicate_entity_facts"]
    out.attribute_coverage = 0.5

    d = out.to_dict()
    # 새 키
    assert d["initial_result"] == MATCH
    assert d["review_triggers"] == ["duplicate_entity_facts"]
    assert d["attribute_coverage"] == 0.5
    assert d["safe_to_finalize"] is False
    assert d["result_changed"] is False
    # 기존 소비자(missing_trace/artifact_reader/why_missing)가 쓰는 키는 그대로
    for key in ("entity_name", "target_doc", "result", "decided_by", "reason",
                "mismatch_attributes", "match_score", "match_method",
                "reference", "target"):
        assert key in d


def test_result_changed_detects_llm_overriding_code():
    cmp_, _ = _comparator()
    out = cmp_.compare(_fact("r", "전지"), [], _target())
    out.initial_result = MATCH          # 코드는 match 라 했는데
    out.result = MISMATCH               # 최종은 mismatch
    assert out.result_changed is True


def test_result_changed_is_false_without_initial_result():
    """initial_result 를 안 채운 호출부(롤백 경로)에서 오탐이 나면 안 된다."""
    cmp_, _ = _comparator()
    out = cmp_.compare(_fact("r", "전지"), [], _target())
    assert out.initial_result == ""
    assert out.result_changed is False
