"""F5 Fact Comparator — fact ↔ fact 를 대조해 판정한다(하이브리드).

**코드가 먼저 결정적으로 판정하고, 애매한 것만 LLM 에 넘긴다.** fact 를
``{value, unit}`` 으로 정규화한 이유가 "값 비교는 코드가 한다"이므로 이것이 설계의
일관된 귀결이다. 부수적으로 ``decided_by`` 계측이 남아 **LLM 이 실제로 어디서 필요한지**
수치로 확인할 수 있다.

코드가 단정하는 경우(LLM 호출 0):

===========================================  ==================================
상황                                         판정
===========================================  ==================================
후보 없음(검색 임계 미달 포함)                ``missing``
공통 속성이 있고 값이 전부 같음               ``match``
공통 속성이 있고 값이 다름 + 단위가 호환       ``mismatch`` (+ 어긋난 속성)
===========================================  ==================================

그 외(공통 속성 없음 · 값도 단위도 다름 · 검색 점수 경계 · F4a ``low_confidence``)는
LLM 에 넘긴다. LLM 이 없거나 예산이 끝나면 **버리지 않고 ``unknown``** 으로 남긴다 —
확신이 없으면 보류하고 사람에게 넘긴다는 원칙(§6.2)을 fact 경로에서도 유지한다.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Optional

from .fact_matcher import MatchCandidate
from .fact_models import Fact
from .fact_store import DocFacts
from .llm_stage import LlmBudgetExceeded
from .prompts import COMPARE_SYSTEM, build_compare_user
from .record_models import Attribute

logger = logging.getLogger(__name__)

MATCH = "match"
MISMATCH = "mismatch"
MISSING = "missing"
UNKNOWN = "unknown"
_RESULTS = (MATCH, MISMATCH, MISSING, UNKNOWN)

BY_CODE = "code"
BY_LLM = "llm"

MISSING_BY_SIMILARITY = "대응하는 내용을 대상 문서에서 찾지 못했습니다(유사도 임계 미달)."
"""유사도 매칭 경로(``use_concept_graph: false``)의 기본 ``missing`` 사유.

개념 그래프 경로에서는 이 문구가 **사실이 아니다** — 호출자가 ``missing_reason`` 으로
"개념 연결이 없어 비교하지 않았습니다"를 주입한다. 문구가 틀리면 사용자는 F7 에서
쓰이지도 않는 ``match_min_score`` 를 조정하러 간다.
"""

# 최소 단위 등가 사전. **여기 없는 조합은 코드가 판단하지 않고 LLM 에 넘긴다** —
# 사전을 추측으로 부풀리면 잘못된 등가 판정을 낳는다. 도메인 지식(knowledge/*.md)은
# LLM 경로에서 주입되므로, 코드 사전은 표기 변형만 최소로 담는다.
_UNIT_ALIASES: dict[str, str] = {
    # "°c"(도 기호 U+00B0 + c)는 영어 문서에서 흔한 표기다. 없으면 canonical_unit 이
    # '모름'을 반환해 값 대조를 코드가 포기한다(2026-08-05 영어 문서 실측).
    "℃": "c", "°c": "c", "도씨": "c", "섭씨": "c", "degc": "c", "c": "c",
    "%": "pct", "%rh": "pct_rh", "rh%": "pct_rh",
    "v": "v", "볼트": "v",
    "ma": "ma", "a": "a",
    "mah": "mah", "ah": "ah",
}


@dataclass
class FactFinding:
    """후보 1건에 대한 내역. 여러 후보가 **함께** 하나의 규격을 이룰 때 쓴다.

    기준 1행 = 리포트 1줄을 유지하면서도 사람이 구간 단위로 검수할 수 있게 하는 그릇이다.
    """

    fact_id: str
    result: str = UNKNOWN
    mismatch_attributes: list[str] = field(default_factory=list)
    quote: str = ""
    """LLM 이 인용한 대상 원문."""
    quote_verified: bool = False
    """코드가 ``evidence_text`` 와 대조한 결과. 실패해도 판정은 유지하고 표시만 남긴다."""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "result": self.result,
            "mismatch_attributes": list(self.mismatch_attributes),
            "quote": self.quote,
            "quote_verified": self.quote_verified,
            "reason": self.reason,
        }


@dataclass
class FactComparison:
    """기준 fact 1건 × 대상 문서 1개의 비교 결과(계획 §6.2 스키마)."""

    reference_fact: Fact
    target_doc: str
    result: str = MISSING
    mismatch_attributes: list[str] = field(default_factory=list)
    target_fact: Optional[Fact] = None
    """대표 1건. **코드가 결정론적으로 고른다** — 프롬프트에서 ``target_fact_id`` 를
    없앴으므로 LLM 이 고르지 않는다. 리포트에서 대표로 보여줄 것은 문제가 있는 쪽이어야
    사람이 먼저 볼 곳을 찾으므로 "첫 mismatch finding → 첫 finding → ``candidates[0]``"
    순으로 정한다."""
    target_facts: list[Fact] = field(default_factory=list)
    """종합 판정에 실제로 쓰인 후보 전부. 후보가 1건이면 1건이라 기존 소비자는 무변경."""
    findings: list["FactFinding"] = field(default_factory=list)
    """후보별 내역 — 사람이 구간 단위로 검수할 수 있게 남긴다."""
    candidate_count: int = 0
    """후보 수. ``target_facts`` 는 드롭 후의 수라 1:N 계측을 대신할 수 없다."""
    match_score: float = 0.0
    match_method: str = "none"
    decided_by: str = BY_CODE
    """``code`` | ``llm`` — LLM 이 실제로 얼마나 필요했는지의 계측."""
    reason: str = ""

    # --- 판정 이력(Phase 1) — 2차 검사가 1차를 조용히 덮지 않게 하는 기반 --- #
    initial_result: str = ""
    """코드 판정. ``result`` 는 최종 판정이다. 비어 있으면 게이트를 안 거친 호출부."""
    review_triggers: list[str] = field(default_factory=list)
    """Acceptance Gate 가 붙인 검토 사유. 비면 안전하게 확정 가능."""
    attribute_coverage: float = 1.0

    @property
    def result_changed(self) -> bool:
        """코드 판정을 뒤엎었는가. 개선인지 새 오판인지 진단하려면 둘 다 있어야 한다."""
        return bool(self.initial_result) and self.initial_result != self.result

    @property
    def safe_to_finalize(self) -> bool:
        return not self.review_triggers

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_name": self.reference_fact.entity_name,
            "target_doc": self.target_doc,
            "result": self.result,
            "mismatch_attributes": self.mismatch_attributes,
            "match_score": round(self.match_score, 4),
            "match_method": self.match_method,
            "decided_by": self.decided_by,
            "initial_result": self.initial_result,
            "review_triggers": list(self.review_triggers),
            "attribute_coverage": round(self.attribute_coverage, 4),
            "result_changed": self.result_changed,
            "safe_to_finalize": self.safe_to_finalize,
            "reason": self.reason,
            "reference": _side_dict(self.reference_fact),
            "target": _side_dict(self.target_fact),
            # 1:N 종합 판정의 내역. 후보가 1건이면 findings 도 1건이라 기존 소비자는
            # 무변경이고, 여러 건일 때만 사람이 구간 단위로 검수할 거리가 생긴다.
            "candidate_count": self.candidate_count,
            "findings": [f.to_dict() for f in self.findings],
            "targets": [_side_dict(f) for f in self.target_facts],
        }


def _side_dict(fact: Optional[Fact]) -> Optional[dict]:
    """양측 근거를 나란히 싣는다 — 사람이 원문 대조로 검수할 수 있어야 한다(§6.2)."""
    if fact is None:
        return None
    return {
        "fact_id": fact.fact_id,
        "entity_name": fact.entity_name,
        "attributes": {k: a.to_dict() for k, a in fact.attributes.items()},
        "evidence_text": fact.evidence_text,
        "source": fact.source,
    }


@dataclass
class ComparisonProbe:
    """코드 판정까지만 끝낸 중간 상태 — **LLM 을 거치지 않았다**.

    ``compare()`` 안에 묻혀 있던 "코드 판정"과 "확정"을 갈라, 그 사이에
    Acceptance Gate 가 들어올 자리를 만든다. 게이트를 사후(판정이 끝난 뒤)에
    채점하지 않는 이유는 :meth:`FactComparator._decide_by_llm` 이 후보를 교체할 수
    있어, 사후 채점은 **코드 판정 시점과 다른 후보**를 채점하게 되기 때문이다.

    ``code_result`` 의 ``None`` 과 ``"unknown"`` 은 다르다 — ``None`` 은 코드가
    판단을 **포기**했다(단위 등가 미상 등)는 뜻이고, ``unknown`` 은 최종 판정
    라벨이다. 둘을 합치면 "코드가 못 정한 것"과 "사람이 봐야 하는 것"이 섞인다.
    """

    reference_fact: Fact
    target_doc: str
    candidates: list[MatchCandidate] = field(default_factory=list)
    code_result: Optional[str] = None
    mismatch_attributes: list[str] = field(default_factory=list)
    code_reason: str = ""
    attribute_coverage: float = 0.0
    uncertain: bool = False
    """후보 점수 경계·LLM 이 만든 연결·F4a 저신뢰 중 하나라도 해당."""
    missing_reason: str = ""

    @property
    def best(self) -> Optional[MatchCandidate]:
        return self.candidates[0] if self.candidates else None


class FactComparator:
    def __init__(
        self,
        *,
        runner: Any = None,
        knowledge: str = "",
        use_llm: bool = True,
    ) -> None:
        self.runner = runner
        self.knowledge = knowledge or ""
        self.use_llm = use_llm and runner is not None
        self.llm_calls = 0
        self.llm_failures = 0
        self.dropped_findings = 0
        """후보 밖 ``fact_id`` 를 가리켜 버려진 finding 수 — 드롭은 결과에 안 남으므로
        여기서 세지 않으면 영영 보이지 않는다."""
        self.quote_unverified = 0

    # ------------------------------------------------------------------ #
    def compare(
        self,
        ref: Fact,
        candidates: list[MatchCandidate],
        target: DocFacts,
        *,
        ref_low_confidence: bool = False,
        missing_reason: str = "",
    ) -> FactComparison:
        """기준 fact 1건을 대상 문서 1개와 대조한다(코드 판정 → 확정을 한 번에).

        ``missing_reason`` 은 후보가 하나도 없을 때의 사유를 **호출자가 주입**하는
        자리다. 후보가 왜 없는지는 매칭 전략마다 다르다 — 유사도 경로는 임계 미달,
        F7 개념 경로는 "개념이 ``same_as`` 로 이어지지 않음"이다. 비우면 유사도
        경로의 기본 문구를 쓴다(롤백 경로 ``use_concept_graph: false`` 가 그대로 사용).

        Acceptance Gate 를 끼우려면 :meth:`compare_code` 와 :meth:`finalize` 를
        직접 부른다. 이 메서드는 그 둘을 잇는 호환 래퍼다.
        """
        return self.finalize(self.compare_code(
            ref, candidates, target,
            ref_low_confidence=ref_low_confidence,
            missing_reason=missing_reason,
        ))

    def compare_code(
        self,
        ref: Fact,
        candidates: list[MatchCandidate],
        target: DocFacts,
        *,
        ref_low_confidence: bool = False,
        missing_reason: str = "",
    ) -> ComparisonProbe:
        """코드 판정까지만 한다 — **LLM 을 절대 호출하지 않는다.**

        이 계약이 깨지면 게이트가 채점하기도 전에 비용이 나가므로 테스트로 고정한다.
        """
        if not candidates:
            return ComparisonProbe(
                reference_fact=ref,
                target_doc=target.doc_name,
                code_result=MISSING,
                code_reason=missing_reason.strip() or MISSING_BY_SIMILARITY,
                missing_reason=missing_reason,
            )

        best = candidates[0]
        probe = ComparisonProbe(
            reference_fact=ref,
            target_doc=target.doc_name,
            candidates=candidates,
            attribute_coverage=attribute_coverage(ref, best.fact),
            uncertain=(
                best.needs_review
                or ref_low_confidence
                or target.is_low_confidence(best.fact)
            ),
            missing_reason=missing_reason,
        )
        verdict = self._decide_by_code(ref, best.fact)
        if verdict is not None:
            probe.code_result, probe.mismatch_attributes, probe.code_reason = verdict
        return probe

    def finalize(
        self, probe: ComparisonProbe, *, force_llm: bool = False
    ) -> FactComparison:
        """probe 를 최종 판정으로 만든다. 필요할 때만(그리고 그때만) LLM 을 부른다.

        ``force_llm`` 은 Acceptance Gate 가 거부한 코드 ``match`` 를 강등하는
        자리다(``fast_path.enforce``). 기본 False 에서는 분리 이전과 동작이 같다.

        **후보가 2건 이상이면 인자와 무관하게 LLM 으로 보낸다.** 동명 fact 들은 recall
        점수로 갈리지 않으므로 ``candidates[0]`` 축약은 사실상 임의 선택이고, 그 위의
        판정은 원리적으로 신뢰할 수 없다. 이 규칙을 호출부가 아니라 여기에 두는 것은
        ``compare()`` 래퍼와 롤백 경로까지 한 번에 덮기 위해서다 — 게이트와는 독립이라
        ``fast_path.enabled: false`` 여도 살아 있어야 한다(게이트는 라우팅 실험이고
        이것은 정확성 버그 수정이다).
        """
        if not probe.candidates:
            return FactComparison(
                reference_fact=probe.reference_fact,
                target_doc=probe.target_doc,
                result=MISSING,
                reason=probe.code_reason,
            )

        best = probe.candidates[0]
        multi = len(probe.candidates) >= 2
        out = FactComparison(
            reference_fact=probe.reference_fact,
            target_doc=probe.target_doc,
            target_fact=best.fact,
            target_facts=[best.fact],
            match_score=best.score,
            match_method=best.method,
            candidate_count=len(probe.candidates),
        )
        verdict = (
            None if probe.code_result is None
            else (probe.code_result, probe.mismatch_attributes, probe.code_reason)
        )
        if verdict is not None and not probe.uncertain and not force_llm and not multi:
            out.result, out.mismatch_attributes, out.reason = verdict
            return out

        # 코드가 단정하지 못했거나 근거가 불안정하거나 게이트가 거부 → LLM(없으면 보류).
        return self._decide_by_llm(
            out, probe.reference_fact, probe.candidates, verdict, probe.uncertain,
            multi=multi,
        )

    # ------------------------------------------------------------------ #
    # 코드 결정적 판정
    # ------------------------------------------------------------------ #
    @staticmethod
    def _decide_by_code(ref: Fact, target: Fact) -> Optional[tuple[str, list[str], str]]:
        """확정 가능하면 ``(result, mismatch_attributes, reason)``, 아니면 ``None``."""
        shared = [k for k in ref.attributes if k in target.attributes]
        if not shared:
            return _compare_single_attributes(ref, target)

        same: list[str] = []
        differ: list[str] = []
        for key in shared:
            state = _compare_attribute(ref.attributes[key], target.attributes[key])
            if state is None:
                return None  # 단위 등가를 알 수 없음 → 판단 위임
            (same if state else differ).append(key)

        if not differ:
            return MATCH, [], f"공통 속성 {', '.join(same)} 의 값이 모두 일치합니다."
        detail = ", ".join(
            f"{k}(기준 {_fmt(ref.attributes[k])} vs 대상 {_fmt(target.attributes[k])})"
            for k in differ
        )
        return MISMATCH, differ, f"값이 다릅니다: {detail}"

    # ------------------------------------------------------------------ #
    # LLM 판정 (위임된 건만)
    # ------------------------------------------------------------------ #
    def _decide_by_llm(
        self,
        out: FactComparison,
        ref: Fact,
        candidates: list[MatchCandidate],
        code_verdict: Optional[tuple[str, list[str], str]],
        uncertain: bool,
        *,
        multi: bool = False,
    ) -> FactComparison:
        if not self.use_llm:
            return self._fallback(
                out, code_verdict, "LLM 판정이 꺼져 있어 보류합니다.", multi=multi
            )

        user = build_compare_user(
            ref, [c.fact for c in candidates], knowledge=self.knowledge
        )
        try:
            parsed = self.runner.complete_json(COMPARE_SYSTEM, user)
            self.llm_calls += 1
        except (LlmBudgetExceeded, ValueError) as e:
            self.llm_failures += 1
            logger.warning("[Fact] 비교 LLM 실패(%s) → 보류: %s", type(e).__name__, ref.entity_name)
            return self._fallback(
                out, code_verdict, f"LLM 판정 실패({type(e).__name__})로 보류합니다.",
                multi=multi,
            )

        result = str(parsed.get("result") or "").strip().lower()
        if result not in _RESULTS:
            result = UNKNOWN

        by_id = {c.fact.fact_id: c for c in candidates}
        findings, dropped = _parse_findings(parsed.get("findings"), by_id)
        self.dropped_findings += dropped
        self.quote_unverified += sum(1 for f in findings if not f.quote_verified)

        # LLM 이 finding 을 냈는데 **전부** 드롭됐다면 근거가 하나도 없는 판정이다.
        # finding 을 애초에 하나도 안 낸 경우는 여기에 해당하지 않는다 — 그것은
        # ``missing``("후보 중 기준과 같은 대상이 없다")의 정상 형태이므로, 합치면
        # 정당한 missing 이 unknown 으로 바뀐다.
        if dropped and not findings:
            out.result = UNKNOWN
            out.decided_by = BY_LLM
            out.reason = "LLM 이 제시한 근거가 모두 후보 밖의 id 를 가리켜 보류합니다."
            return out

        attrs = _dedupe(a for f in findings for a in f.mismatch_attributes)
        if findings:
            out.findings = findings
            out.target_facts = [by_id[f.fact_id].fact for f in findings]
            # 대표는 **문제가 있는 쪽** — 사람이 먼저 볼 곳을 찾아야 한다.
            rep = next((f for f in findings if f.result == MISMATCH), findings[0])
            chosen = by_id[rep.fact_id]
            out.target_fact = chosen.fact
            out.match_score = chosen.score
            out.match_method = chosen.method
        if result == MISSING:
            out.target_fact = None
            out.target_facts = []

        out.result = result
        out.decided_by = BY_LLM
        out.mismatch_attributes = attrs or [
            str(a) for a in (parsed.get("mismatch_attributes") or []) if str(a)
        ]
        reason = str(parsed.get("reason") or "").strip()
        out.reason = reason or "(사유 없음)"
        if uncertain and result != UNKNOWN:
            out.reason += " (근거 신뢰도가 낮아 검토 대상입니다.)"
        return out

    @staticmethod
    def _fallback(
        out: FactComparison,
        code_verdict: Optional[tuple[str, list[str], str]],
        note: str,
        *,
        multi: bool = False,
    ) -> FactComparison:
        """LLM 을 못 쓸 때 — 코드 판정이 있으면 그대로, 없으면 ``unknown``.

        단 후보가 2건 이상이면 **되돌아가지 않는다.** 그 코드 판정은 ``candidates[0]``
        축약이 만든 임의 선택이라, 복귀시키면 지금 고치려는 오판이 조용히 그대로 남는다.
        확신이 없으면 보류한다는 ``unknown`` 원칙의 정확한 적용 대상이다.
        """
        if multi:
            out.result = UNKNOWN
            out.reason = (
                f"후보 {out.candidate_count}건을 종합 판정할 수 없어 보류합니다. {note}"
            )
            return out
        if code_verdict is not None:
            out.result, out.mismatch_attributes, out.reason = code_verdict
            out.reason += f" {note}"
            return out
        out.result = UNKNOWN
        out.reason = (
            "대상에서 관련 내용을 찾았으나 대조할 공통 속성이 없거나 단위 등가를 "
            f"확정할 수 없습니다. {note}"
        )
        return out


# --------------------------------------------------------------------------- #
# 값·단위 비교
# --------------------------------------------------------------------------- #
# 의미가 정반대라 값이 같아도 같은 주장으로 볼 수 없는 속성 쌍("최소 X" vs "최대 X").
_OPPOSITE = frozenset({frozenset({"lower_limit", "upper_limit"})})


def attribute_coverage(ref: Fact, target: Optional[Fact]) -> float:
    """기준 fact 의 속성 중 대상에서 대응된 비율.

    :meth:`FactComparator._decide_by_code` 는 **공통 속성만** 보기 때문에 기준에
    세 속성이 있고 대상에 하나뿐이어도 그 하나가 같으면 ``match`` 가 된다. 이
    함수가 그 구멍을 수치로 드러내고, Acceptance Gate 가 그것으로 라우팅한다.

    분모는 기준 fact 의 **전체** 속성이다. 값이 빈 속성을 빼는 느슨한 정의는
    placeholder 판별 규칙이 하나 더 필요해져 채택하지 않았다.

    **단일 속성 예외에 연결 출처 가드를 두지 않는다.** 초안은 "LLM 이 만든 개념
    연결 위에서는 예외를 적용하지 않는다"로 두었으나 라이브 실측에서 지표가
    무너졌다(2026-08-10, 한↔영 문서쌍 평균 0.53 vs 가드 제거 시 0.94). 한국어
    기준 ↔ 영어 대상은 이름 완전일치가 원리적으로 불가능해 개념 연결이 거의 전부
    LLM 산이고(실측 same_as 12건 중 확정 연결 3건), 그래서 가드가 **항상** 걸렸다.

    더 나쁜 것은 :func:`_compare_single_attributes` 와 규칙이 어긋난 점이다 —
    그쪽은 가드 없이 키가 달라도 1:1 이면 ``match`` 를 낸다. 같은 쌍을 한쪽은
    일치로, 다른 쪽은 커버리지 0 으로 채점하니 그런 match 가 전부 unsafe 로
    거부됐다(실측 ``unsafe_match_rate`` 1.0). 연결이 불안하다는 신호는
    ``low_confidence`` 사유가 **이미 따로** 싣는다. 커버리지에 이중으로 반영하면
    "속성이 실제로 안 적혀 있다"와 "연결을 LLM 이 만들었다"가 한 숫자에 섞여,
    그 숫자로 내리려던 판단(임계를 풀지 말지)이 불가능해진다.
    """
    if target is None:
        return 0.0
    if not ref.attributes:
        return 1.0  # 비교할 속성이 없으면 커버리지 논의가 무의미하다
    if len(ref.attributes) == 1 and len(target.attributes) == 1:
        # 근거는 _compare_single_attributes 와 같다 — 속성이 하나뿐인 fact 의 키
        # 이름은 원본 표의 **열 위치**에서 온 것이라 의미 구분이 아닌 경우가 많다.
        return 1.0
    covered = sum(1 for k in ref.attributes if k in target.attributes)
    return covered / len(ref.attributes)


def _compare_single_attributes(ref: Fact, target: Fact) -> Optional[tuple[str, list[str], str]]:
    """공통 키가 없지만 **양쪽 다 속성이 하나뿐**일 때의 비교.

    속성이 하나뿐인 fact 는 "이 항목의 값은 X"라는 뜻이고, 키 이름은 원본 표의 **열
    위치**에서 온 것이라 의미 구분이 아닌 경우가 많다. 실측이 정확히 그랬다:
    기준 문서가 공칭용량 1150 을 '하한치' 열에 적어 ``lower_limit`` 이 됐고 대상은
    ``target_value`` 였다 — 같은 값인데 키가 달라 판정이 보류됐다.

    단, ``lower_limit`` vs ``upper_limit`` 처럼 의미가 정반대인 쌍은 값이 같아도
    같은 주장이 아니므로 코드가 단정하지 않는다.
    """
    if len(ref.attributes) != 1 or len(target.attributes) != 1:
        return None
    (ref_key, ref_attr), = ref.attributes.items()
    (tgt_key, tgt_attr), = target.attributes.items()
    if frozenset({ref_key, tgt_key}) in _OPPOSITE:
        return None

    state = _compare_attribute(ref_attr, tgt_attr)
    if state is None:
        return None
    if state:
        return MATCH, [], (
            f"값이 일치합니다({_fmt(ref_attr)}). 속성 이름은 다르지만"
            f"({ref_key} / {tgt_key}) 양쪽 모두 이 항목의 단일 값입니다."
        )
    return MISMATCH, [ref_key], (
        f"값이 다릅니다: 기준 {_fmt(ref_attr)} vs 대상 {_fmt(tgt_attr)}"
    )


def _as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def canonical_unit(unit: str) -> Optional[str]:
    """단위 표기를 표준형으로. 사전에 없으면 ``None``(= 모름, 판단 위임)."""
    u = str(unit or "").strip().lower().replace(" ", "")
    if not u:
        return ""  # 단위 없음은 '모름'과 구분한다
    return _UNIT_ALIASES.get(u)


def _units_compatible(a: Attribute, b: Attribute) -> Optional[bool]:
    """단위가 호환되는가. 판단 불가면 ``None``.

    한쪽만 단위가 있는 경우는 **호환으로 본다** — 기준 문서의 단위 열이 통째로 비어
    있는 실제 사례가 흔하고(실측 20 fact 중 16), 그때 값이 같으면 같다고 보는 것이
    사람의 판단과 일치한다. 단위가 양쪽 다 있는데 다르면 코드가 단정하지 않는다.
    """
    ua, ub = canonical_unit(a.unit), canonical_unit(b.unit)
    if ua is None or ub is None:
        return None  # 사전에 없는 단위 → 모름
    if ua == "" or ub == "":
        return True
    return ua == ub


def _is_decimal_scaled(a: float, b: float) -> bool:
    """두 값이 10의 거듭제곱 배수 관계인가(1495 ↔ 1.495 = 1000배).

    단위 접두어 차이(mA↔A, mAh↔Ah)의 전형적 흔적이다. 한쪽 단위를 모르는 상태에서
    이런 값을 보면 '다르다'가 아니라 '같은 값의 다른 단위 표기'일 수 있다.
    """
    if a == 0 or b == 0:
        return False
    ratio = abs(a / b)
    if ratio < 1:
        ratio = 1 / ratio
    exponent = round(math.log10(ratio))
    return exponent >= 1 and math.isclose(ratio, 10 ** exponent, rel_tol=1e-6)


def _compare_attribute(a: Attribute, b: Attribute) -> Optional[bool]:
    """두 속성값이 같은가. 판단 불가(단위 미상 등)면 ``None``."""
    na, nb = _as_number(a.value), _as_number(b.value)
    if na is not None and nb is not None:
        compatible = _units_compatible(a, b)
        if compatible is None:
            return None
        if not compatible:
            # 단위가 다르면 값 비교 자체가 의미 없다(1495mA vs 1.495A) → 위임.
            return None
        if na == nb:
            return True
        # 값이 다른데 한쪽 단위를 모르고 십진 배수 관계면, 단위 접두어 차이일 수
        # 있으므로 코드가 '다르다'고 단정하지 않는다(실측: 최대충전전류 1495 vs 1.495A).
        if not (a.unit and b.unit) and _is_decimal_scaled(na, nb):
            return None
        return False
    # 문자열은 **같을 때만** 코드가 단정한다. 다르다고 곧 불일치는 아니기 때문이다 —
    # 실측: 기준 "배터리승인규격 ver 4.7 SEC Req. ver.4.7" vs 대상 "SEC Req. ver.4.7"
    # 은 문자열이 다르지만 같은 규격을 가리킨다. 표현 차이 판단은 LLM 의 몫이다.
    sa, sb = _norm_text(a.value), _norm_text(b.value)
    if not sa or not sb:
        return None
    return True if sa == sb else None


def _norm_text(value: Any) -> str:
    return "".join(str(value or "").split()).lower()


def _fmt(attr: Attribute) -> str:
    unit = f"{attr.unit}" if attr.unit else ""
    return f"{attr.value}{unit}"


def _parse_findings(
    raw: Any, by_id: dict[str, MatchCandidate]
) -> tuple[list[FactFinding], int]:
    """``findings`` 배열 → 검증된 내역 + 드롭 수.

    제안은 LLM, 차단은 코드 — F7 의 인용 검증과 같은 권한 비대칭이다. 후보 밖 id 는
    드롭하지만(할루시네이션 방지), **인용 불일치는 드롭하지 않는다** — 드롭하면 종합
    판정이 통째로 날아가 사용성이 크게 떨어지므로 표시만 남겨 사람이 검수하게 한다.
    """
    items = raw if isinstance(raw, list) else []
    out: list[FactFinding] = []
    dropped = 0
    for item in items:
        cand = by_id.get(str(item.get("fact_id") or "")) if isinstance(item, dict) else None
        if cand is None:
            dropped += 1
            continue
        result = str(item.get("result") or "").strip().lower()
        quote = str(item.get("quote") or "").strip()
        out.append(FactFinding(
            fact_id=cand.fact.fact_id,
            result=result if result in (MATCH, MISMATCH, UNKNOWN) else UNKNOWN,
            mismatch_attributes=[
                str(a) for a in (item.get("mismatch_attributes") or []) if str(a)
            ],
            quote=quote,
            quote_verified=_quote_in_evidence(quote, cand.fact.evidence_text),
            reason=str(item.get("reason") or "").strip(),
        ))
    return out, dropped


def _quote_in_evidence(quote: str, evidence: str) -> bool:
    """인용이 그 fact 의 근거 원문 안에 실재하는가(공백 병합 후 부분일치).

    정규화 규약은 ``fact_extractor._norm`` 과 같다 — 공백만 병합하고 대소문자·기호는
    건드리지 않는다. 두 곳이 갈리면 같은 인용이 한쪽에서만 검증에 통과한다.
    """
    q = " ".join(str(quote or "").split())
    return bool(q) and q in " ".join(str(evidence or "").split())


def _dedupe(values: Any) -> list[str]:
    """순서를 지키는 중복 제거 — 리포트에서 속성 순서가 흔들리면 대조가 어렵다."""
    seen: dict[str, None] = {}
    for v in values:
        seen.setdefault(str(v), None)
    return list(seen)
