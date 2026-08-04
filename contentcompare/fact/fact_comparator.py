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
    "℃": "c", "도씨": "c", "섭씨": "c", "degc": "c", "c": "c",
    "%": "pct", "%rh": "pct_rh", "rh%": "pct_rh",
    "v": "v", "볼트": "v",
    "ma": "ma", "a": "a",
    "mah": "mah", "ah": "ah",
}


@dataclass
class FactComparison:
    """기준 fact 1건 × 대상 문서 1개의 비교 결과(계획 §6.2 스키마)."""

    reference_fact: Fact
    target_doc: str
    result: str = MISSING
    mismatch_attributes: list[str] = field(default_factory=list)
    target_fact: Optional[Fact] = None
    match_score: float = 0.0
    match_method: str = "none"
    decided_by: str = BY_CODE
    """``code`` | ``llm`` — LLM 이 실제로 얼마나 필요했는지의 계측."""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_name": self.reference_fact.entity_name,
            "target_doc": self.target_doc,
            "result": self.result,
            "mismatch_attributes": self.mismatch_attributes,
            "match_score": round(self.match_score, 4),
            "match_method": self.match_method,
            "decided_by": self.decided_by,
            "reason": self.reason,
            "reference": _side_dict(self.reference_fact),
            "target": _side_dict(self.target_fact),
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
        """기준 fact 1건을 대상 문서 1개와 대조한다.

        ``missing_reason`` 은 후보가 하나도 없을 때의 사유를 **호출자가 주입**하는
        자리다. 후보가 왜 없는지는 매칭 전략마다 다르다 — 유사도 경로는 임계 미달,
        F7 개념 경로는 "개념이 ``same_as`` 로 이어지지 않음"이다. 비우면 유사도
        경로의 기본 문구를 쓴다(롤백 경로 ``use_concept_graph: false`` 가 그대로 사용).
        """
        if not candidates:
            return FactComparison(
                reference_fact=ref, target_doc=target.doc_name, result=MISSING,
                reason=missing_reason.strip() or MISSING_BY_SIMILARITY,
            )

        best = candidates[0]
        out = FactComparison(
            reference_fact=ref,
            target_doc=target.doc_name,
            target_fact=best.fact,
            match_score=best.score,
            match_method=best.method,
        )

        verdict = self._decide_by_code(ref, best.fact)
        uncertain = (
            best.needs_review
            or ref_low_confidence
            or target.is_low_confidence(best.fact)
        )
        if verdict is not None and not uncertain:
            out.result, out.mismatch_attributes, out.reason = verdict
            return out

        # 코드가 단정하지 못했거나 근거가 불안정 → LLM 판단(없으면 보류).
        return self._decide_by_llm(out, ref, candidates, verdict, uncertain)

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
    ) -> FactComparison:
        if not self.use_llm:
            return self._fallback(out, code_verdict, "LLM 판정이 꺼져 있어 보류합니다.")

        user = build_compare_user(
            ref, [c.fact for c in candidates], knowledge=self.knowledge
        )
        try:
            parsed = self.runner.complete_json(COMPARE_SYSTEM, user)
            self.llm_calls += 1
        except (LlmBudgetExceeded, ValueError) as e:
            self.llm_failures += 1
            logger.warning("[Fact] 비교 LLM 실패(%s) → 보류: %s", type(e).__name__, ref.entity_name)
            return self._fallback(out, code_verdict, f"LLM 판정 실패({type(e).__name__})로 보류합니다.")

        result = str(parsed.get("result") or "").strip().lower()
        if result not in _RESULTS:
            result = UNKNOWN
        # LLM 이 지목한 후보로 교체(코드가 준 후보 안에서만 — 할루시네이션 방지).
        chosen = _pick_target(parsed.get("target_fact_id"), candidates)
        if chosen is not None:
            out.target_fact = chosen.fact
            out.match_score = chosen.score
            out.match_method = chosen.method
        if result == MISSING:
            out.target_fact = None

        out.result = result
        out.decided_by = BY_LLM
        out.mismatch_attributes = [
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
    ) -> FactComparison:
        """LLM 을 못 쓸 때 — 코드 판정이 있으면 그대로, 없으면 ``unknown``."""
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


def _pick_target(fact_id: Any, candidates: list[MatchCandidate]) -> Optional[MatchCandidate]:
    fid = str(fact_id or "")
    for c in candidates:
        if c.fact.fact_id == fid:
            return c
    return None
