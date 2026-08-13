"""F5 Acceptance Gate — 코드가 확정한 ``match`` 를 믿어도 되는지 채점한다.

**게이트는 탐지가 아니라 라우팅이다.** 코드가 하는 일은 "후보가 2건 이상이다",
"커버리지가 1.0 미만이다" 같은 **셀 수 있는 사실**의 확인이고, "이 후보들 중 어느
것이 맞는가"는 판단하지 않는다 — 그것은 2차 Evidence 검사가 원문을 보고 할 일이다.
이 경계를 흐리면 게이트가 또 하나의 축약된 판정기가 되어, 지금 고치려는 문제를
그대로 재생산한다.

**이 단계는 비용을 줄이지 않는다.** 오늘도 코드 ``match`` 는 LLM 을 안 부르므로
(:meth:`FactComparator.finalize`), 게이트의 실제 효과는 지금까지 조용히 확정되던
unsafe match 를 LLM 으로 보내는 것이다. 그래서 기본값이 shadow 이고,
:func:`gate_stats` 의 ``unsafe_match_rate`` 가 "켜면 얼마나 늘어나는가"를 미리
알려주는 것이 이 모듈의 존재 이유다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .fact_comparator import MATCH, MISMATCH, MISSING, ComparisonProbe, FactComparison

if TYPE_CHECKING:  # 런타임 import 를 피해 config ↔ fact 결합을 늘리지 않는다
    from ..config import FastPathConfig

CODE_MISSING = "code_missing"
CODE_MISMATCH = "code_mismatch"
CODE_UNKNOWN = "code_unknown"
LOW_CONFIDENCE = "low_confidence"
PARTIAL_ATTRIBUTE_COVERAGE = "partial_attribute_coverage"
DUPLICATE_ENTITY_FACTS = "duplicate_entity_facts"
INVALID_EVIDENCE = "invalid_evidence"

REASON_ORDER = (
    CODE_MISSING,
    CODE_MISMATCH,
    CODE_UNKNOWN,
    LOW_CONFIDENCE,
    PARTIAL_ATTRIBUTE_COVERAGE,
    DUPLICATE_ENTITY_FACTS,
    INVALID_EVIDENCE,
)
"""사유 순서 고정 — 리포트·통계가 실행마다 같은 순서로 재현되게 한다."""


class AcceptanceGate:
    """코드 판정 결과(:class:`ComparisonProbe`)를 채점해 검토 사유를 돌려준다."""

    def __init__(self, cfg: "FastPathConfig") -> None:
        self.enabled = bool(cfg.enabled)
        self.enforce = bool(cfg.enforce)

    def evaluate(self, probe: ComparisonProbe) -> list[str]:
        """검토가 필요한 사유들. 빈 리스트면 안전하게 확정할 수 있다."""
        if not self.enabled:
            return []
        if not probe.candidates:
            # 후보가 없으면 사유는 하나뿐이다. 나머지 규칙을 함께 평가하면 모든
            # missing 에 coverage 0.0 과 '대상 fact 없음'이 따라붙어, 사유 통계가
            # "거의 전부 partial_coverage" 로 보이면서 진짜 원인을 덮는다.
            return [CODE_MISSING]

        reasons: list[str] = []
        if probe.code_result == MISMATCH:
            reasons.append(CODE_MISMATCH)
        if probe.code_result is None:
            reasons.append(CODE_UNKNOWN)
        if probe.uncertain:
            reasons.append(LOW_CONFIDENCE)
        if probe.attribute_coverage < 1.0:
            reasons.append(PARTIAL_ATTRIBUTE_COVERAGE)
        if len(probe.candidates) > 1:
            reasons.append(DUPLICATE_ENTITY_FACTS)
        if _evidence_invalid(probe):
            reasons.append(INVALID_EVIDENCE)
        return reasons


def _evidence_invalid(probe: ComparisonProbe) -> bool:
    """사람이 원문 대조로 검수할 수 없는 판정은 안전하다고 볼 수 없다(설계 §6.2)."""
    best = probe.best
    if best is None:
        return True
    return not best.fact.evidence_text.strip() or not best.fact.source


def _already_routed_to_llm(c: FactComparison) -> bool:
    """게이트와 **무관하게** :meth:`FactComparator.finalize` 가 이미 LLM 으로 보내는가.

    finalize 는 ``code_result is None`` · ``probe.uncertain`` · 후보 2건 이상이면
    ``force_llm`` 없이도 LLM 을 부른다. 게이트 사유 중 셋이 그 조건과 **같은 사실**을
    가리키므로, 그것들은 enforce 를 켜도 새로 늘어나지 않는다.

    ==============================  ===================================
    게이트 사유                      finalize 의 같은 조건
    ==============================  ===================================
    ``low_confidence``              ``probe.uncertain``
    ``code_unknown``                ``code_result is None``
    ``duplicate_entity_facts``      ``len(candidates) >= 2`` (1:N 라우팅)
    ==============================  ===================================
    """
    return (
        c.candidate_count >= 2
        or LOW_CONFIDENCE in c.review_triggers
        or CODE_UNKNOWN in c.review_triggers
    )


def gate_stats(comparisons: list[FactComparison]) -> dict:
    """게이트 계측 — ``enforce`` 전환 비용을 켜기 전에 알려준다.

    **``enforce_new_llm_count`` 가 그 비용이다.** ``unsafe_match_rate`` 는 게이트가
    얼마나 조이는지를 재는 별개의 값이고, 전환 비용으로 읽으면 안 된다 — 거부된
    match 의 상당수는 finalize 가 **이미** LLM 으로 보내고 있어서 enforce 를 켜도
    늘어나지 않는다(실측 `artifacts/자표준문서_xlsx`: unsafe 7건 중 5건이 그렇고,
    ``unsafe_match_rate`` 0.88 이 실제 순증가 10% 를 3.5배 부풀렸다).
    """
    total = len(comparisons)
    if not total:
        return {}

    safe = sum(1 for c in comparisons if c.safe_to_finalize)
    code_match = [c for c in comparisons if c.initial_result == MATCH]
    unsafe = [c for c in code_match if not c.safe_to_finalize]
    new_llm = [c for c in unsafe if not _already_routed_to_llm(c)]

    reasons: dict[str, int] = {}
    for c in comparisons:
        for r in c.review_triggers:
            reasons[r] = reasons.get(r, 0) + 1

    # 후보가 없는 비교의 coverage 0.0 은 "커버리지가 낮다"가 아니라 "잴 대상이
    # 없다"이므로 평균에서 뺀다. 섞으면 missing 이 많은 문서에서 평균이 함께
    # 내려가 게이트가 과하게 조인 것처럼 보인다.
    measured = [c for c in comparisons if c.initial_result != MISSING]

    return {
        "fast_path_rate": round(safe / total, 4),
        "secondary_review_rate": round(1 - safe / total, 4),
        "unsafe_match_rate": round(len(unsafe) / len(code_match), 4) if code_match else 0.0,
        "enforce_new_llm_count": len(new_llm),
        "enforce_new_llm_rate": round(len(new_llm) / total, 4),
        "review_reasons": {k: reasons[k] for k in REASON_ORDER if k in reasons},
        "mean_attribute_coverage": (
            round(sum(c.attribute_coverage for c in measured) / len(measured), 4)
            if measured else 0.0
        ),
        "result_changed_count": sum(1 for c in comparisons if c.result_changed),
        "code_overridden_count": sum(1 for c in comparisons if _code_overridden(c)),
    }


def _code_overridden(c: FactComparison) -> bool:
    """코드가 **의견을 냈는데** 최종 판정이 그것과 다른가.

    ``result_changed`` 와 갈라야 하는 이유가 있다. ``initial_result`` 는
    ``probe.code_result or UNKNOWN`` 이므로, 코드가 판단을 **포기**했을 때도
    ``unknown`` 이 들어간다. 그러면 LLM 이 무슨 답을 하든 ``result_changed`` 가 켜져
    "LLM 이 코드를 뒤엎었다"로 읽히지만 실제로는 **코드에 의견이 없었던 것**이다
    (2026-08-10 실측: shadow 모드인데 19건이 changed 로 잡혔다 — 대부분 이 경우).

    ``missing`` 은 후보가 없어 LLM 을 아예 부르지 않으므로 뒤집힐 수 없다.
    따라서 코드의 '의견'은 ``match``/``mismatch`` 둘뿐이다.
    """
    return c.initial_result in (MATCH, MISMATCH) and c.result != c.initial_result
