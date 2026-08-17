"""F5 Acceptance Gate — 코드 match 를 믿어도 되는지 채점한다.

게이트는 **탐지가 아니라 라우팅**이다. "후보가 2건 이상이다" 같은 셀 수 있는
사실만 확인하고, "어느 후보가 맞는가"는 판단하지 않는다. LLM 도 Office 도 필요 없다.
"""

from __future__ import annotations

import pytest

from contentcompare.config import FastPathConfig
from contentcompare.fact.fact_comparator import (
    MATCH,
    MISMATCH,
    MISSING,
    UNKNOWN,
    ComparisonProbe,
    FactComparison,
)
from contentcompare.fact.fact_matcher import CONCEPT, MatchCandidate
from contentcompare.fact.fact_models import Fact
from contentcompare.fact.record_models import Attribute
from contentcompare.fact.review_router import (
    CODE_MISMATCH,
    CODE_MISSING,
    CODE_UNKNOWN,
    DUPLICATE_ENTITY_FACTS,
    INVALID_EVIDENCE,
    LOW_CONFIDENCE,
    PARTIAL_ATTRIBUTE_COVERAGE,
    REASON_ORDER,
    AcceptanceGate,
    gate_stats,
)


def _fact(fid="t", *, evidence="근거", source=None, **attrs) -> Fact:
    return Fact(
        fact_id=fid,
        entity_name="전지",
        attributes={k: Attribute(v, "V") for k, v in attrs.items()},
        evidence_text=evidence,
        source=source if source is not None else {"block_id": "w_b01"},
    )


def _cand(fact: Fact, review: bool = False) -> MatchCandidate:
    return MatchCandidate(fact, 0.9, CONCEPT, needs_review=review)


def _probe(*facts: Fact, code_result=MATCH, coverage=1.0, uncertain=False) -> ComparisonProbe:
    cands = [_cand(f) for f in facts]
    return ComparisonProbe(
        reference_fact=_fact("r", nominal="3.85"),
        target_doc="규격서.docx",
        candidates=cands,
        code_result=code_result,
        attribute_coverage=coverage,
        uncertain=uncertain,
    )


def _gate(**kw) -> AcceptanceGate:
    return AcceptanceGate(FastPathConfig(**kw))


# --------------------------------------------------------------------------- #
# 통과 조건
# --------------------------------------------------------------------------- #
def test_clean_single_candidate_match_passes():
    assert _gate().evaluate(_probe(_fact())) == []


def test_disabled_gate_never_reports_anything():
    """enabled=False 는 도입 전과 동일한 동작을 보장하는 탈출구다."""
    probe = _probe(_fact(evidence=""), code_result=MISMATCH, coverage=0.2)
    assert _gate(enabled=False).evaluate(probe) == []


# --------------------------------------------------------------------------- #
# 후보가 없을 때 — 사유는 하나뿐이어야 한다
# --------------------------------------------------------------------------- #
def test_no_candidate_yields_only_code_missing():
    """coverage 0.0 과 '대상 fact 없음'이 함께 붙으면 사유 통계가 무의미해진다."""
    probe = ComparisonProbe(
        reference_fact=_fact("r", nominal="3.85"),
        target_doc="규격서.docx",
        code_result=MISSING,
        attribute_coverage=0.0,
    )
    assert _gate().evaluate(probe) == [CODE_MISSING]


# --------------------------------------------------------------------------- #
# 개별 규칙
# --------------------------------------------------------------------------- #
def test_partial_coverage_is_flagged():
    assert _gate().evaluate(_probe(_fact(), coverage=0.5)) == [PARTIAL_ATTRIBUTE_COVERAGE]


def test_multiple_candidates_are_flagged():
    """같은 개념에 대상 fact 가 여럿이면 '어느 것이 맞는가'는 코드의 몫이 아니다."""
    assert _gate().evaluate(_probe(_fact("a"), _fact("b"))) == [DUPLICATE_ENTITY_FACTS]


def test_empty_evidence_text_is_flagged():
    """사람이 원문 대조로 검수할 수 없는 판정은 안전하다고 볼 수 없다."""
    assert _gate().evaluate(_probe(_fact(evidence="  "))) == [INVALID_EVIDENCE]


def test_empty_source_is_flagged():
    assert _gate().evaluate(_probe(_fact(source={}))) == [INVALID_EVIDENCE]


def test_code_mismatch_is_flagged():
    assert _gate().evaluate(_probe(_fact(), code_result=MISMATCH)) == [CODE_MISMATCH]


def test_undecided_code_is_flagged():
    assert _gate().evaluate(_probe(_fact(), code_result=None)) == [CODE_UNKNOWN]


def test_uncertain_probe_is_flagged():
    assert _gate().evaluate(_probe(_fact(), uncertain=True)) == [LOW_CONFIDENCE]


# --------------------------------------------------------------------------- #
# 누적과 순서
# --------------------------------------------------------------------------- #
def test_reasons_accumulate_in_fixed_order():
    """순서가 흔들리면 리포트·통계가 실행마다 달라 보인다."""
    probe = _probe(
        _fact("a", evidence=""), _fact("b"),
        code_result=MISMATCH, coverage=0.5, uncertain=True,
    )
    reasons = _gate().evaluate(probe)
    assert reasons == [
        CODE_MISMATCH, LOW_CONFIDENCE, PARTIAL_ATTRIBUTE_COVERAGE,
        DUPLICATE_ENTITY_FACTS, INVALID_EVIDENCE,
    ]
    assert reasons == [r for r in REASON_ORDER if r in reasons]


def test_enforce_flag_is_exposed():
    assert _gate().enforce is False
    assert _gate(enforce=True).enforce is True


# --------------------------------------------------------------------------- #
# 통계 — enforce 전환 비용을 켜기 전에 알려주는 것이 목적이다
# --------------------------------------------------------------------------- #
def _comparison(initial, final, triggers, coverage=1.0, candidates=1) -> FactComparison:
    return FactComparison(
        reference_fact=_fact("r"), target_doc="규격서.docx", result=final,
        initial_result=initial, review_triggers=list(triggers),
        attribute_coverage=coverage, candidate_count=candidates,
    )


# --------------------------------------------------------------------------- #
# enforce 순증가 — unsafe_match_rate 는 이 질문에 답하지 못한다
# --------------------------------------------------------------------------- #
def test_enforce_delta_counts_only_matches_that_are_not_already_routed():
    """게이트 사유 중 상당수는 finalize 가 이미 LLM 으로 보내는 조건과 같다.

    그것까지 세면 "켜면 얼마나 늘어나는가"가 부풀려진다(실측 3.5배).
    """
    rows = [
        _comparison(MATCH, MATCH, [PARTIAL_ATTRIBUTE_COVERAGE], 0.67),      # ★ 순증가
        _comparison(MATCH, MATCH, [LOW_CONFIDENCE]),                        # 이미 LLM
        _comparison(MATCH, MATCH, [DUPLICATE_ENTITY_FACTS], candidates=2),  # 이미 LLM
        _comparison(MATCH, MATCH, []),                                      # 안전
    ]
    s = gate_stats(rows)
    assert s["unsafe_match_rate"] == pytest.approx(0.75)      # 사유가 붙은 match 3/4
    assert s["enforce_new_llm_count"] == 1                     # 실제로 늘어나는 건 1건
    assert s["enforce_new_llm_rate"] == pytest.approx(0.25)


def test_invalid_evidence_alone_is_a_real_enforce_delta():
    rows = [_comparison(MATCH, MATCH, [INVALID_EVIDENCE])]
    assert gate_stats(rows)["enforce_new_llm_count"] == 1


def test_code_unknown_match_is_impossible_but_never_counted_as_delta():
    """코드가 포기한 건은 finalize 가 이미 LLM 으로 보낸다."""
    rows = [_comparison(MATCH, MATCH, [CODE_UNKNOWN, PARTIAL_ATTRIBUTE_COVERAGE], 0.5)]
    assert gate_stats(rows)["enforce_new_llm_count"] == 0


def test_gate_stats_reports_rates():
    rows = [
        _comparison(MATCH, MATCH, []),                                   # 안전
        _comparison(MATCH, MATCH, [PARTIAL_ATTRIBUTE_COVERAGE], 0.5),    # unsafe match
        _comparison(MISMATCH, MISMATCH, [CODE_MISMATCH]),
        _comparison(MISSING, MISSING, [CODE_MISSING], 0.0),
    ]
    s = gate_stats(rows)
    assert s["fast_path_rate"] == pytest.approx(0.25)
    assert s["secondary_review_rate"] == pytest.approx(0.75)
    assert s["unsafe_match_rate"] == pytest.approx(0.5)   # code match 2건 중 1건
    assert s["review_reasons"] == {
        CODE_MISSING: 1, CODE_MISMATCH: 1, PARTIAL_ATTRIBUTE_COVERAGE: 1,
    }
    assert s["mean_attribute_coverage"] == pytest.approx(0.8333, abs=1e-4)  # missing 제외
    assert s["result_changed_count"] == 0


def test_gate_stats_counts_overridden_code_verdicts():
    rows = [_comparison(MATCH, MISMATCH, [PARTIAL_ATTRIBUTE_COVERAGE], 0.5)]
    s = gate_stats(rows)
    assert s["result_changed_count"] == 1
    assert s["code_overridden_count"] == 1     # 코드가 match 라 했는데 뒤집혔다


def test_code_punting_is_not_counted_as_an_override():
    """코드가 판단을 포기한 것(unknown)은 '뒤엎힘'이 아니다.

    initial_result 는 probe.code_result or UNKNOWN 이라, 코드가 포기하면 unknown 이
    들어간다. 그걸 override 로 세면 shadow 모드인데도 숫자가 커져
    "LLM 이 코드를 자주 뒤엎는다"로 오독된다(2026-08-10 실측 19건이 그랬다).
    """
    rows = [_comparison(UNKNOWN, MATCH, [CODE_UNKNOWN])]
    s = gate_stats(rows)
    assert s["result_changed_count"] == 1      # 값은 바뀌었지만
    assert s["code_overridden_count"] == 0     # 코드에 의견이 없었다


def test_missing_never_counts_as_an_override():
    """후보가 없으면 LLM 을 아예 안 부르므로 뒤집힐 수 없다."""
    rows = [_comparison(MISSING, MISSING, [CODE_MISSING], 0.0)]
    assert gate_stats(rows)["code_overridden_count"] == 0


def test_gate_stats_without_code_match_has_zero_unsafe_rate():
    """분모가 0일 때 나눗셈으로 죽지 않아야 한다."""
    rows = [_comparison(MISSING, MISSING, [CODE_MISSING], 0.0)]
    s = gate_stats(rows)
    assert s["unsafe_match_rate"] == 0.0
    assert s["mean_attribute_coverage"] == 0.0


def test_gate_stats_of_empty_input_is_empty():
    assert gate_stats([]) == {}


def test_reason_order_has_no_duplicates():
    assert len(REASON_ORDER) == len(set(REASON_ORDER)) == 7
