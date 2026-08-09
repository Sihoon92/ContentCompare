"""F4a Rule Validator — 코드가 fact 를 검증한다(LLM 미사용).

LLM 산출물을 코드가 검증·교정한다는 설계 원칙 3의 전반부다(``FACT_PIPELINE_PLAN.md`` §5).
비용이 0이라 항상 켜두고, 결과는 ``validation_report.json`` 으로 남긴다.

검사 항목은 **F3.5 라이브에서 실제로 관찰된 것만** 넣었다
(근거: ``docs/FACT_F3_5_LIVE_REPORT.md`` §7). 사변적 검사는 넣지 않는다.

``error`` 가 붙은 fact 는 버리지 않고 :attr:`ValidationReport.low_confidence_ids` 로
표시해 F5 가 ``unknown`` 판정 근거로 쓴다 — 사람 검수 대상으로 남기는 것이 목적이지
자동 삭제가 아니다(§5.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from ..similarity.tokenize import tokenize
from .fact_models import Fact, FactSet
from .schema_models import ColumnSchema
from .semantic_roles import (
    ENTITY_NAME,
    QUANT_LOWER,
    QUANT_TARGET,
    QUANT_UPPER,
    QUANT_VALUE,
    UNIT,
)

ERROR = "error"
WARN = "warn"

# 근거 문구가 원문에 실재한다고 볼 최소 토큰 포함률.
# 문자열 부분일치(naive substring)는 금지다 — LLM 이 공백·개행·셀 구분자를 바꿔
# 옮기는 경향이 있어 실재하는 근거도 실패로 뜬다(§5.1). 토큰 기준이 이에 강건하다.
EVIDENCE_MIN_COVERAGE = 0.8

# 한 표에 여러 열이 같은 역할을 갖는 것이 부자연스러운 역할들.
# metadata/qualitative_spec 등은 여러 열에 흔히 나타나므로 검사하지 않는다.
_EXCLUSIVE_ROLES = (ENTITY_NAME, QUANT_LOWER, QUANT_TARGET, QUANT_UPPER, QUANT_VALUE, UNIT)


@dataclass
class CheckResult:
    check: str
    severity: str
    fact_id: str
    reason: str
    suggestion: str = ""
    """교정 지시 후보. F4b(Repair Loop)를 붙일 때 그대로 프롬프트에 실을 수 있게 지금부터 채운다."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "severity": self.severity,
            "fact_id": self.fact_id,
            "reason": self.reason,
            "suggestion": self.suggestion,
        }


@dataclass
class ValidationReport:
    location: str = ""
    facts: int = 0
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def low_confidence_ids(self) -> set[str]:
        """``error`` 가 하나라도 붙은 fact id — F5 의 ``unknown`` 판정 입력."""
        return {c.fact_id for c in self.checks if c.severity == ERROR and c.fact_id}

    def by_check(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.checks:
            out[c.check] = out.get(c.check, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        errors = sum(1 for c in self.checks if c.severity == ERROR)
        return {
            "location": self.location,
            "overall": {
                "facts": self.facts,
                "error": errors,
                "warn": len(self.checks) - errors,
                "low_confidence": len(self.low_confidence_ids),
            },
            "by_check": self.by_check(),
            "checks": [c.to_dict() for c in self.checks],
        }


def validate_facts(
    facts: FactSet,
    compact: dict,
    *,
    column_schema: Optional[ColumnSchema] = None,
) -> ValidationReport:
    """``FactSet`` 을 검사해 :class:`ValidationReport` 를 만든다(LLM 미사용)."""
    report = ValidationReport(location=facts.location, facts=len(facts.facts))
    doc_tokens = set(_doc_tokens(compact))
    locators = _source_locators(compact)

    for fact in facts.facts:
        report.checks.extend(_check_quant_bounds(fact))
        report.checks.extend(_check_units(fact))
        report.checks.extend(_check_attributes(fact))
        report.checks.extend(_check_evidence(fact, doc_tokens))
        report.checks.extend(_check_source(fact, locators))

    if column_schema is not None:
        report.checks.extend(_check_role_duplication(column_schema))
    return report


# --------------------------------------------------------------------------- #
# 개별 검사
# --------------------------------------------------------------------------- #
def _as_number(value: Any) -> Optional[float]:
    """숫자로 볼 수 있으면 float, 아니면 None. ``1,200`` 같은 표기도 허용."""
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


def _check_quant_bounds(fact: Fact) -> list[CheckResult]:
    """``lower ≤ target ≤ upper`` — 값싼 수학적 불변식."""
    lo = _as_number(_attr_value(fact, "lower_limit"))
    tg = _as_number(_attr_value(fact, "target_value"))
    up = _as_number(_attr_value(fact, "upper_limit"))
    bad = []
    if lo is not None and up is not None and lo > up:
        bad.append(f"lower_limit({lo}) > upper_limit({up})")
    if lo is not None and tg is not None and lo > tg:
        bad.append(f"lower_limit({lo}) > target_value({tg})")
    if tg is not None and up is not None and tg > up:
        bad.append(f"target_value({tg}) > upper_limit({up})")
    if not bad:
        return []
    return [CheckResult(
        check="quant_bounds", severity=ERROR, fact_id=fact.fact_id,
        reason="정량 규격의 대소 관계가 어긋남: " + ", ".join(bad),
        suggestion="원문에서 하한/중심/상한이 어느 열·문구에 있는지 다시 확인해 값을 바로잡으세요.",
    )]


def _check_units(fact: Fact) -> list[CheckResult]:
    """숫자 값인데 단위가 비었는가.

    ``warn`` 이다. 기준 문서(자표준문서.xlsx)는 단위 열이 통째로 비어 있어 20 fact
    **전부**가 여기 걸린다 — ``error`` 로 두면 전건 실패가 되어 신호가 죽는다.
    """
    missing = [
        name for name, attr in fact.attributes.items()
        if _as_number(attr.value) is not None and not str(attr.unit or "").strip()
    ]
    if not missing:
        return []
    return [CheckResult(
        check="unit_missing", severity=WARN, fact_id=fact.fact_id,
        reason=f"수치에 단위가 없음: {', '.join(missing)}",
        suggestion="원문에 단위가 있으면 채우고, 원문 자체에 없으면 비교 시 단위 등가를 단정하지 마세요.",
    )]


def _check_attributes(fact: Fact) -> list[CheckResult]:
    if fact.attributes:
        return []
    return [CheckResult(
        check="no_attributes", severity=WARN, fact_id=fact.fact_id,
        reason="비교할 속성이 하나도 없음(값 대조 불가)",
        suggestion="원문에 값이 있는데 누락됐다면 속성으로 추출하세요.",
    )]


def evidence_coverage(claim: str, source_tokens: set[str]) -> float:
    """``claim`` 의 토큰 중 원문에 실재하는 비율(0.0~1.0).

    문자열 부분일치는 쓰지 않는다 — LLM 이 공백·개행·셀 구분자를 바꿔 옮기는 경향이
    있어 실재하는 근거도 실패로 뜬다. F7 개념 그래프의 인용 검증도 이 함수를 쓴다.
    """
    tokens = set(tokenize(claim))
    if not tokens:
        return 0.0
    return len(tokens & source_tokens) / len(tokens)


def _check_evidence(fact: Fact, doc_tokens: set[str]) -> list[CheckResult]:
    """``evidence_text`` 가 원문에 실재하는가(토큰 포함률 기준).

    근거를 요구하는 것은 **주장(속성)이 있을 때**다. 속성이 하나도 없는 fact 는
    원본이 항목명만 있는 빈 행인 경우가 많아(실측: deltaOCV·충전방법) 근거가 비어도
    할루시네이션이 아니다 — 그 상황은 ``no_attributes`` 가 이미 표시한다.
    """
    tokens = set(tokenize(fact.evidence_text))
    if not fact.evidence_text.strip():
        if not fact.attributes:
            return []
        return [CheckResult(
            check="evidence_missing", severity=ERROR, fact_id=fact.fact_id,
            reason="값을 주장하면서 근거 원문(evidence_text)이 비어 있음",
            suggestion="입력에 실제로 있는 문구를 근거로 옮기세요.",
        )]
    if not tokens:
        return []
    coverage = evidence_coverage(fact.evidence_text, doc_tokens)
    if coverage >= EVIDENCE_MIN_COVERAGE:
        return []
    unseen = sorted(tokens - doc_tokens)[:5]
    return [CheckResult(
        check="evidence_missing", severity=ERROR, fact_id=fact.fact_id,
        reason=f"근거 문구가 원문에 없음(토큰 포함률 {coverage:.0%}, 예: {', '.join(unseen)})",
        suggestion="지어낸 문구가 아닌지 확인하고 원문 문구로 교체하세요.",
    )]


def _check_source(fact: Fact, locators: dict[str, set]) -> list[CheckResult]:
    """``source`` 가 실제 존재하는 위치를 가리키는가 — 좌표 할루시네이션 검출."""
    src = fact.source or {}
    doc_type = src.get("doc_type")
    bad = ""
    if doc_type == "excel":
        row = src.get("row")
        if row is not None and locators["excel_rows"] and row not in locators["excel_rows"]:
            bad = f"시트에 없는 행 번호: {row}"
    elif doc_type == "word":
        ids = [i for i in (src.get("block_ids") or []) if i not in locators["word_blocks"]]
        if ids:
            bad = f"문서에 없는 블록 id: {', '.join(map(str, ids))}"
    elif doc_type == "ppt":
        slide = src.get("slide_no")
        if slide is not None and locators["ppt_slides"] and slide not in locators["ppt_slides"]:
            bad = f"문서에 없는 슬라이드 번호: {slide}"
        else:
            ids = [i for i in (src.get("shape_ids") or []) if i not in locators["ppt_shapes"]]
            if ids:
                bad = f"문서에 없는 도형 id: {', '.join(map(str, ids))}"
    if not bad:
        return []
    return [CheckResult(
        check="source_unresolvable", severity=ERROR, fact_id=fact.fact_id,
        reason=bad,
        suggestion="입력에 제시된 id/좌표만 근거로 쓰세요.",
    )]


def _check_role_duplication(schema: ColumnSchema) -> list[CheckResult]:
    """같은 semantic_role 이 여러 열에 붙었는가(Excel 스키마 유도 잡음).

    실측: ``F`` 와 ``I`` 가 동시에 ``quantitative_lower_bound`` 로 배정됐다. 정량 필드
    해석이 갈릴 수 있어 표시해 둔다.
    """
    by_role: dict[str, list[str]] = {}
    for col in schema.columns:
        if col.semantic_role in _EXCLUSIVE_ROLES:
            by_role.setdefault(col.semantic_role, []).append(col.column)
    out = []
    for role, cols in by_role.items():
        if len(cols) > 1:
            out.append(CheckResult(
                check="role_duplicated", severity=WARN, fact_id="",
                reason=f"semantic_role '{role}' 이 여러 열에 배정됨: {', '.join(cols)}",
                suggestion="어느 열이 진짜 그 역할인지 확인하세요(나머지는 다른 역할일 수 있음).",
            ))
    return out


# --------------------------------------------------------------------------- #
# compact_raw 에서 검사 재료 뽑기
# --------------------------------------------------------------------------- #
def _attr_value(fact: Fact, name: str) -> Any:
    attr = fact.attributes.get(name)
    return attr.value if attr is not None else None


def _doc_tokens(compact: dict) -> Iterable[str]:
    """문서 전체 텍스트의 토큰 — evidence 실재 검사의 대조 집합."""
    for text in _iter_texts(compact):
        yield from tokenize(text)


def _iter_texts(compact: dict) -> Iterable[str]:
    doc_type = compact.get("doc_type")
    if doc_type == "excel":
        for sheet in compact.get("sheets") or []:
            yield str(sheet.get("sheet_name") or "")
            for row in sheet.get("rows") or []:
                for value in (row.get("cells") or {}).values():
                    yield str(value)
    elif doc_type == "word":
        for block in compact.get("blocks") or []:
            yield str(block.get("text") or "")
            yield from _table_texts(block.get("rows"))
    else:
        for slide in compact.get("slides") or []:
            for shape in slide.get("shapes") or []:
                yield str(shape.get("text") or "")
                yield from _table_texts(shape.get("rows"))
            yield str(slide.get("notes") or "")


def _table_texts(rows: Any) -> Iterable[str]:
    for row in rows or []:
        for cell in row or []:
            yield str(cell)


def _source_locators(compact: dict) -> dict[str, set]:
    """문서에 실제로 존재하는 좌표 집합(행 번호/블록 id/슬라이드·도형 id)."""
    out: dict[str, set] = {
        "excel_rows": set(), "word_blocks": set(), "ppt_slides": set(), "ppt_shapes": set(),
    }
    for sheet in compact.get("sheets") or []:
        for row in sheet.get("rows") or []:
            if row.get("r") is not None:
                out["excel_rows"].add(row["r"])
    for block in compact.get("blocks") or []:
        if block.get("id"):
            out["word_blocks"].add(block["id"])
    for slide in compact.get("slides") or []:
        if slide.get("slide_no") is not None:
            out["ppt_slides"].add(slide["slide_no"])
        for shape in slide.get("shapes") or []:
            if shape.get("id"):
                out["ppt_shapes"].add(shape["id"])
    return out


# --------------------------------------------------------------------------- #
# F7 개념 그래프 검증
# --------------------------------------------------------------------------- #
_REJECT_CHECKS = {
    "evidence": ("concept_evidence_missing", "인용한 근거가 원문에 없어 연결을 거부했습니다"),
    "differs_by": ("concept_merge_violation", "differs_by 제약을 위반해 병합을 거부했습니다"),
}


def validate_graph(graph: Any) -> ValidationReport:
    """개념 그래프의 무결성을 검사한다(LLM 미사용).

    코드는 **위상만** 본다 — ``axis`` 문자열의 의미는 검사하지 않는다(설계 §2.1).

    .. warning::
       이 리포트의 :attr:`CheckResult.fact_id` 는 **쌍 라벨**(``"doc#id ↔ doc#id"``)
       이지 실제 fact id 가 아니다. 그래프는 실행 단위, ``validation_report.json`` 은
       문서 단위라 같은 스키마를 빌려 쓰되 키의 의미가 다르다. 따라서 이 결과를
       :attr:`DocFacts.low_confidence_ids` 같은 fact id 기반 경로에 넣으면 안 된다 —
       매칭되지 않거나 엉뚱한 fact 를 저신뢰로 표시하게 된다. 현재는
       ``concept_validation.json`` 저장 전용이다.
    """
    from .concept_models import DIFFERS_BY, SAME_AS, UNKNOWN as REL_UNKNOWN

    report = ValidationReport(location="concept_graph", facts=len(graph.nodes))
    known = {(m.doc, m.fact_id) for n in graph.nodes for m in n.members}

    seen: dict[tuple[str, str], set[str]] = {}
    for edge in graph.edges:
        pair = edge.pair_key
        label = f"{pair[0]} ↔ {pair[1]}"

        for ref in (edge.left, edge.right):
            if (ref.doc, ref.fact_id) not in known:
                report.checks.append(CheckResult(
                    check="concept_dangling_node", severity=ERROR, fact_id=label,
                    reason=f"그래프에 없는 fact 를 가리킵니다: {ref.key}",
                    suggestion="엣지를 버리거나 해당 fact 를 멤버에 넣으세요.",
                ))

        if edge.rejected_by in _REJECT_CHECKS:
            check, reason = _REJECT_CHECKS[edge.rejected_by]
            report.checks.append(CheckResult(
                check=check, severity=ERROR, fact_id=label, reason=reason,
                suggestion="사람이 확인해 knowledge/ontology.yaml 로 승격하세요.",
            ))
        elif edge.relation == REL_UNKNOWN:
            report.checks.append(CheckResult(
                check="concept_unknown_pair", severity=WARN, fact_id=label,
                reason=edge.reason or "관계를 판정하지 못했습니다",
                suggestion="리포트의 '검토 필요' 목록에서 확인하세요.",
            ))

        seen.setdefault(pair, set()).add(edge.relation)

    for pair, relations in seen.items():
        if SAME_AS in relations and DIFFERS_BY in relations:
            report.checks.append(CheckResult(
                check="concept_contradiction", severity=ERROR,
                fact_id=f"{pair[0]} ↔ {pair[1]}",
                reason="같은 쌍에 same_as 와 differs_by 가 함께 있습니다",
                suggestion="knowledge/ontology.yaml 로 어느 쪽이 맞는지 확정하세요.",
            ))
    return report
