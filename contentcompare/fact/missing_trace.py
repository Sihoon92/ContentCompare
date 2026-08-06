"""``⚪ 대상에 없음`` 의 원인을 가른다 — 이 저장소의 디버깅 공백을 메우는 모듈.

리포트는 "대상에서 못 찾았다"까지만 말한다. 그런데 못 찾은 이유는 **여섯 가지**이고
각각 조치가 완전히 다르다:

===== ================================================ ==========================
원인   무슨 일이 있었나                                   사람이 할 일
===== ================================================ ==========================
①     recall 이 후보를 못 만들었다                        임계/후보수 조정 · 온톨로지 승격
②     LLM 이 관계를 판정하지 못했다                       예산 증대 · 재실행
③     근거 게이트가 LLM 의 연결을 강등했다                 인용 확인 후 승격
④     F3 가 대상 문서에서 fact 를 안 뽑았다                배치 축소 · 추출 프롬프트 점검
⑤     사람이 "다른 개념"이라 막았다(오판 아님)             ``differs_by`` 재검토
⑥     후보는 있었는데 F5 LLM 이 없다고 답했다              판정 근거 확인
===== ================================================ ==========================

**핵심 판별**: ``comparison_result.json`` 만으로 "후보가 있었는가"를 가를 수 있다.
:meth:`~contentcompare.fact.fact_comparator.FactComparator.compare` 는 후보가 없으면
LLM 을 부르지 않고 즉시 반환하므로 ``decided_by`` 가 ``code`` 로 남고, 후보가 있는데
LLM 이 ``missing`` 이라 답한 경우에만 ``llm`` 이 된다. ``_fallback`` 은 ``missing`` 을
만들지 않으므로 이 판별에 예외가 없다.

오판·누락 추적이 이 모듈의 존재 이유다 — **분류를 단순화하려고 원인을 합치지 말 것.**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..similarity.tokenize import tokenize
from .artifact_reader import RunSnapshot, attributes_text, target_of
from .concept_assembler import REJECT_NOTES
from .concept_models import DIFFERS_BY, SAME_AS, UNKNOWN
from .fact_matcher import norm_name

# 원인 코드 — 화면·CLI 가 이 값으로 분기한다.
CAUSE_RECALL = "recall"
CAUSE_LLM_UNDECIDED = "llm_undecided"
CAUSE_EVIDENCE_GATE = "evidence_gate"
CAUSE_EXTRACTION = "extraction"
CAUSE_BLOCKED = "blocked"
CAUSE_F5_LLM = "f5_llm"
CAUSE_UNKNOWN = "unresolved"

CAUSE_LABEL = {
    CAUSE_RECALL: "① recall 실패 — 후보 쌍이 만들어지지 않음",
    CAUSE_LLM_UNDECIDED: "② LLM 미판정 — 관계를 정하지 못함",
    CAUSE_EVIDENCE_GATE: "③ 근거 게이트 강등 — 연결이 거부됨",
    CAUSE_EXTRACTION: "④ F3 추출 누락 — 대상 문서에서 fact 가 안 나옴",
    CAUSE_BLOCKED: "⑤ 다른 개념으로 차단 — 의도된 동작",
    CAUSE_F5_LLM: "⑥ F5 LLM 판정 — 후보는 있었으나 없다고 답함",
    CAUSE_UNKNOWN: "판정 불가 — 근거가 부족합니다",
}

CONFIRMED = "확정"
INFERRED = "추정"
UNRESOLVED = "판정 불가"


@dataclass
class Evidence:
    """원인 판단의 근거 한 조각. **사람이 파일에서 찾아갈 수 있어야 한다.**"""

    artifact: str
    pointer: str
    label: str
    detail: Any = None


@dataclass
class MissingTrace:
    ref_fact_id: str
    entity_name: str
    target_doc: str
    cause: str = CAUSE_UNKNOWN
    subcause: str = ""
    confidence: str = UNRESOLVED
    headline: str = ""
    next_action: str = ""
    trail: list[dict] = field(default_factory=list)
    """파이프라인 흔적 — ``{stage, who, ok, note}`` 순서대로. 화면이 그대로 그린다."""

    evidence: list[Evidence] = field(default_factory=list)

    @property
    def label(self) -> str:
        return CAUSE_LABEL.get(self.cause, self.cause)

    def to_dict(self) -> dict:
        return {
            "ref_fact_id": self.ref_fact_id,
            "entity_name": self.entity_name,
            "target_doc": self.target_doc,
            "cause": self.cause,
            "label": self.label,
            "subcause": self.subcause,
            "confidence": self.confidence,
            "headline": self.headline,
            "next_action": self.next_action,
            "trail": self.trail,
            "evidence": [
                {"artifact": e.artifact, "pointer": e.pointer,
                 "label": e.label, "detail": e.detail}
                for e in self.evidence
            ],
        }


# --------------------------------------------------------------------------- #
# 진입점
# --------------------------------------------------------------------------- #
def trace_missing(snap: RunSnapshot, comparison: dict) -> MissingTrace:
    """``missing`` 비교 항목 하나의 원인을 가린다.

    ``missing`` 이 아닌 항목을 주면 원인 없이 흔적만 채워 돌려준다(호출자 방어).
    """
    ref = comparison.get("reference") if isinstance(comparison.get("reference"), dict) else {}
    out = MissingTrace(
        ref_fact_id=str(ref.get("fact_id") or ""),
        entity_name=str(comparison.get("entity_name") or ref.get("entity_name") or ""),
        target_doc=str(comparison.get("target_doc") or ""),
    )
    if comparison.get("result") != "missing":
        out.headline = "이 항목은 '대상에 없음' 이 아닙니다."
        return out

    # ⑥ 후보는 있었다 — F5 LLM 이 없다고 답한 경우에만 decided_by 가 llm 이다.
    if comparison.get("decided_by") == "llm":
        return _f5_llm(out, comparison)

    edges = snap.index.edges_between(snap.reference_doc, out.ref_fact_id, out.target_doc)
    rejected = [e for e in edges if e.relation == UNKNOWN and e.rejected_by]
    undecided = [e for e in edges if e.relation == UNKNOWN and not e.rejected_by]
    blocked = [e for e in edges if e.relation == DIFFERS_BY]

    # 순서가 결과를 바꾼다. **구체적인 것부터** 본다.
    #
    # ``differs_by`` 는 흔하다 — 후보 top_k 개 중 대부분이 "다른 개념"으로 판정되므로,
    # 누락된 fact 라면 거의 항상 몇 개씩 달려 있다. 그것을 먼저 보면 진짜 원인
    # (이 상대와 이으려다 게이트에 막힌 것)을 덮어 버린다. 실측에서 en_word 실행의
    # 근거 게이트 강등 10건이 전부 "차단"으로 잘못 분류됐다.
    #
    # 반대로 ``rejected``/``undecided`` 는 "이 상대와 잇는 시도가 있었다"는 구체적 사건이다.
    if rejected:
        return _evidence_gate(out, snap, rejected[0])
    if undecided:
        return _llm_undecided(out, snap, undecided[0])
    if blocked:
        return _blocked(out, snap, blocked, len(edges))
    return _no_edge(out, snap, comparison)


def trace_all_missing(snap: RunSnapshot, target_doc: str = "") -> list[MissingTrace]:
    """실행 전체(또는 한 대상 문서)의 ``missing`` 을 전부 분류한다."""
    return [
        trace_missing(snap, c)
        for c in snap.comparisons_for(target_doc)
        if c.get("result") == "missing"
    ]


def summarize(traces: list[MissingTrace]) -> dict[str, int]:
    """원인별 건수 — "이 실행의 누락은 주로 무엇 때문인가"에 답한다."""
    out: dict[str, int] = {}
    for t in traces:
        out[t.cause] = out.get(t.cause, 0) + 1
    return out


# --------------------------------------------------------------------------- #
# 원인별 판정
# --------------------------------------------------------------------------- #
def _f5_llm(out: MissingTrace, comparison: dict) -> MissingTrace:
    out.cause = CAUSE_F5_LLM
    out.confidence = CONFIRMED
    out.headline = (
        "개념 그래프는 후보를 줬지만, 값 대조 단계의 LLM 이 "
        "'대상에 없다'고 판정했습니다.")
    out.next_action = (
        "LLM 사유를 확인하세요. 후보가 실제로 다른 항목이면 정상이고, 같은 항목인데도 "
        "없다고 했다면 knowledge/*.md 에 용어를 보강하거나 온톨로지로 승격하세요.")
    out.trail = [
        _step("F7 개념", "graph", True, "후보 연결 있음"),
        _step("F5 값 대조", "llm", False, "LLM 이 missing 으로 판정"),
    ]
    out.evidence.append(Evidence(
        artifact="comparison_result.json",
        pointer=f"comparisons[{out.entity_name} · {out.target_doc}]",
        label="F5 LLM 의 판정 사유",
        detail={"reason": comparison.get("reason"),
                "match_method": comparison.get("match_method"),
                "match_score": comparison.get("match_score")},
    ))
    return out


def _blocked(out: MissingTrace, snap: RunSnapshot,
             blockers: list[Any], total: int) -> MissingTrace:
    """검토한 후보가 **전부** '다른 개념'으로 판정된 경우.

    사람이 승격한 차단이 하나라도 있으면 의도된 동작이 확실하다. 전부 LLM 판정이면
    "정말 다른 항목"일 수도, "맞는 상대가 애초에 후보에 없었다"(recall 문제)일 수도
    있으므로 단정하지 않는다 — 그래서 승격 여부로 확신도를 가른다.
    """
    promoted = [e for e in blockers if e.promoted]
    lead = promoted[0] if promoted else blockers[0]
    out.cause = CAUSE_BLOCKED
    out.subcause = lead.axis or ""
    axes = sorted({e.axis for e in blockers if e.axis})
    axis_note = f"'{', '.join(axes)}' 축에서 " if axes else ""

    if promoted:
        out.confidence = CONFIRMED
        out.headline = (
            f"{axis_note}사람이 승격한 관계로 차단됐습니다 — **의도된 동작입니다.**")
        out.next_action = ("같은 항목이라고 판단되면 knowledge/ontology.yaml 의 "
                           "differs_by 를 지우거나 aliases 로 묶으세요.")
    else:
        out.confidence = INFERRED
        out.headline = (
            f"검토한 후보 {len(blockers)}건이 {axis_note}전부 '다른 개념'으로 "
            f"판정됐습니다 — 맞는 상대가 후보에 없었을 수도 있습니다.")
        out.next_action = ("후보 랭킹을 확인하세요. 맞는 상대가 목록에 없으면 recall 문제이니 "
                           "concept_recall_top_k 를 늘리거나 aliases 로 묶으세요. "
                           "목록에 있는데 다르다고 했다면 ontology.yaml 로 승격해 재현되게 하세요.")

    out.trail = [
        _step("F7 개념", _who_of(lead), False,
              f"differs_by {len(blockers)}건 / 검토 {total}건"),
        _step("F5 값 대조", "code", False, "후보 0건 → missing"),
    ]
    out.evidence.append(_edge_evidence(snap, lead, "차단한 관계"))
    slot = snap.ranked_for(out.ref_fact_id, out.target_doc)
    if slot is not None:
        out.evidence.append(Evidence(
            artifact="candidate_pairs.json",
            pointer=f"by_ref[{out.ref_fact_id}].targets[{out.target_doc}]",
            label="이 항목이 검토한 후보 전체",
            detail={"ranked": (slot.get("ranked") or [])[:8]},
        ))
    return out


def _evidence_gate(out: MissingTrace, snap: RunSnapshot, edge: Any) -> MissingTrace:
    out.cause = CAUSE_EVIDENCE_GATE
    out.confidence = CONFIRMED
    out.subcause = edge.rejected_by
    note = REJECT_NOTES.get(edge.rejected_by, edge.rejected_by)
    out.headline = (
        f"LLM 이 같은 개념이라 했지만 코드가 연결을 거부했습니다 — {note}.")
    out.next_action = (
        "인용문이 실제 원문과 맞는지 확인하세요. 맞다면 ontology.yaml 에 same_as 로 "
        "승격하면 다음 실행부터 게이트를 거치지 않습니다."
        if edge.rejected_by == "evidence" else
        "differs_by 제약과 충돌했습니다. ontology.yaml 의 해당 항목을 재검토하세요.")
    out.trail = [
        _step("F7 recall", "embed", True, f"유사도 {edge.recall_score:.4f}"),
        _step("F7 LLM 판정", "llm", True, "same_as 제안"),
        _step("근거 검문소", "code", False, note),
        _step("F5 값 대조", "code", False, "후보 0건 → missing"),
    ]
    out.evidence.append(_edge_evidence(snap, edge, "강등된 연결"))
    return out


def _llm_undecided(out: MissingTrace, snap: RunSnapshot, edge: Any) -> MissingTrace:
    out.cause = CAUSE_LLM_UNDECIDED
    out.confidence = CONFIRMED
    out.subcause = _undecided_kind(edge.reason)
    out.headline = f"후보 쌍은 만들어졌지만 관계를 정하지 못했습니다 — {edge.reason or '사유 없음'}"
    out.next_action = {
        "budget": "max_llm_calls_per_concept 를 늘리고 다시 실행하세요 "
                  "— 예산이 소진되면 남은 쌍이 전부 '없음'으로 귀결됩니다.",
        "absent": "LLM 응답에 이 쌍이 빠졌습니다. concept_batch_pairs 를 줄이면 "
                  "한 번에 판정할 쌍이 줄어 누락이 줄어듭니다.",
        "disabled": "LLM 을 쓰지 않는 설정입니다. 온톨로지로 승격하거나 LLM 을 켜세요.",
    }.get(out.subcause, "로그에서 이 쌍의 판정 실패 원인을 확인하세요.")
    out.trail = [
        _step("F7 recall", "embed", True, f"유사도 {edge.recall_score:.4f}"),
        _step("F7 LLM 판정", "llm", False, out.subcause or "미판정"),
        _step("F5 값 대조", "code", False, "후보 0건 → missing"),
    ]
    out.evidence.append(_edge_evidence(snap, edge, "판정하지 못한 쌍"))
    return out


def _no_edge(out: MissingTrace, snap: RunSnapshot, comparison: dict) -> MissingTrace:
    """엣지가 아예 없다 — recall 실패(①) 또는 F3 추출 누락(④).

    순서가 중요하다: **대상 문서에 fact 자체가 없으면** recall 을 탓할 수 없다.
    그래서 추출 누락을 먼저 본다.
    """
    extraction = _check_extraction(out, snap)
    if extraction is not None:
        return extraction

    slot = snap.ranked_for(out.ref_fact_id, out.target_doc)
    if slot is None:
        out.cause = CAUSE_UNKNOWN
        out.confidence = UNRESOLVED
        out.headline = (
            "개념 그래프에 이 쌍의 흔적이 없고, 후보 기록(candidate_pairs.json)도 "
            "없어 recall 실패인지 확인할 수 없습니다.")
        out.next_action = (
            "fact.save_candidate_pairs 를 켜고 다시 실행하면 이 항목의 원인이 확정됩니다.")
        out.trail = [_step("F7 recall", "embed", False, "기록 없음")]
        return out

    return _recall(out, slot)


def _recall(out: MissingTrace, slot: dict) -> MissingTrace:
    ranked = [r for r in (slot.get("ranked") or []) if isinstance(r, dict)]
    kept = [r for r in ranked if r.get("kept")]
    out.cause = CAUSE_RECALL
    out.confidence = CONFIRMED
    top = ranked[0] if ranked else None

    if not ranked:
        out.subcause = "empty"
        out.headline = "대상 문서에서 후보를 하나도 만들지 못했습니다."
        out.next_action = ("대상 문서에 이 항목이 실제로 있는지 확인하세요. 있다면 "
                           "knowledge/ontology.yaml 의 aliases 로 표기를 묶으세요.")
    elif kept:
        # 후보는 남았는데 엣지가 없다 — 배치에서 통째로 빠진 경우.
        out.subcause = "no_edge"
        out.confidence = INFERRED
        out.headline = ("후보는 남았는데 개념 그래프에 엣지가 없습니다 — "
                        "판정 배치에서 누락됐을 수 있습니다.")
        out.next_action = "로그에서 [Concept] 배치 판정 실패를 확인하세요."
    else:
        out.subcause = str(top.get("cut_by") or "min_score")
        best = float(top.get("score") or 0.0)
        if out.subcause == "top_k":
            out.headline = (f"가장 가까운 후보가 순위에서 밀렸습니다(점수 {best:.4f}) "
                            f"— 임계는 넘었지만 상위 후보 수 안에 못 들었습니다.")
            out.next_action = "concept_recall_top_k 를 늘리세요."
        else:
            out.headline = (f"가장 가까운 후보의 유사도가 {best:.4f} 로 "
                            f"임계에 미치지 못했습니다.")
            out.next_action = ("concept_recall_min 을 낮추거나, 표기가 달라서 못 찾는 것이면 "
                               "ontology.yaml 의 aliases 로 묶으세요(유사도와 무관하게 이어집니다).")

    out.trail = [
        _step("F3 추출", "code", True, "대상 문서에 fact 있음"),
        _step("F7 recall", "embed", False,
              f"후보 {len(kept)}건 통과 / {len(ranked)}건 검토"),
        _step("F5 값 대조", "code", False, "후보 0건 → missing"),
    ]
    out.evidence.append(Evidence(
        artifact="candidate_pairs.json",
        pointer=f"by_ref[{out.ref_fact_id}].targets[{out.target_doc}]",
        label="후보 랭킹과 탈락 사유",
        detail={"ranked": ranked[:8], "from_ontology": slot.get("from_ontology") or []},
    ))
    return out


def _check_extraction(out: MissingTrace, snap: RunSnapshot) -> Optional[MissingTrace]:
    """대상 문서에서 이 항목이 **추출되지 않은** 것으로 보이는가(원인 ④).

    판단 근거는 두 가지다 — (a) 대상 문서에 fact 가 하나도 없다(확정),
    (b) 항목명 토큰이 등장하는데 **아무 fact 도 근거로 삼지 않은** 블록이 있다(추정).
    (b)는 텍스트 대조라 확정할 수 없으므로 ``추정`` 으로 남긴다.
    """
    doc = snap.doc(out.target_doc)
    if doc is None:
        return None
    by_block = doc.load("facts_by_block")
    if by_block is None:
        return None

    blocks = [b for b in (by_block.get("blocks") or []) if isinstance(b, dict)]
    if blocks and not any(b.get("cited") for b in blocks):
        out.cause = CAUSE_EXTRACTION
        out.confidence = CONFIRMED
        out.headline = "대상 문서에서 fact 가 하나도 추출되지 않았습니다."
        out.next_action = ("run_stats.json 의 facts 계측(dropped_*)을 확인하세요. "
                           "LLM 응답이 비었다면 num_ctx 부족일 수 있습니다.")
        out.trail = [_step("F3 추출", "llm", False, "fact 0건")]
        out.evidence.append(_block_evidence(by_block, blocks[:5], "미인용 블록"))
        return out

    hits = _uncited_blocks_mentioning(out.entity_name, blocks)
    if not hits:
        return None
    out.cause = CAUSE_EXTRACTION
    out.confidence = INFERRED
    out.headline = (
        f"항목명이 등장하는 블록이 있는데 어떤 fact 도 그것을 근거로 삼지 않았습니다 "
        f"({len(hits)}건) — F3 가 놓쳤을 가능성이 있습니다.")
    out.next_action = ("fact_batch_blocks 를 줄여 다시 실행하거나, 해당 블록이 표/도형이면 "
                       "추출 프롬프트가 그 형태를 다루는지 확인하세요.")
    out.trail = [
        _step("F3 추출", "llm", False, f"항목명 포함 블록 {len(hits)}건이 미인용"),
        _step("F7 recall", "embed", False, "대조할 fact 자체가 없음"),
    ]
    out.evidence.append(_block_evidence(by_block, hits[:5], "항목명이 등장하는 미인용 블록"))
    return out


def _uncited_blocks_mentioning(entity_name: str, blocks: list[dict]) -> list[dict]:
    """항목명 토큰이 등장하는 **미인용** 블록.

    ``norm_name`` 완전일치와 토큰 겹침을 모두 본다 — 전자는 표기 차이(공백·기호)를,
    후자는 문장 안에 섞여 있는 경우를 잡는다. 둘 다 기존 함수를 그대로 쓴다.
    """
    key = norm_name(entity_name)
    tokens = {t for t in tokenize(entity_name) if len(t) >= 2}
    if not key and not tokens:
        return []
    out = []
    for block in blocks:
        if block.get("cited"):
            continue
        preview = str(block.get("preview") or "")
        if key and key in norm_name(preview):
            out.append(block)
        elif tokens and tokens & set(tokenize(preview)):
            out.append(block)
    return out


# --------------------------------------------------------------------------- #
# 조각
# --------------------------------------------------------------------------- #
def _step(stage: str, who: str, ok: bool, note: str) -> dict:
    """파이프라인 흔적 한 칸. ``who`` 는 주체=색 규약(code/llm/embed/human/graph)."""
    return {"stage": stage, "who": who, "ok": ok, "note": note}


def _who_of(edge: Any) -> str:
    return {"ontology": "human", "code": "code", "llm": "llm"}.get(edge.decided_by, "code")


def _undecided_kind(reason: str) -> str:
    reason = reason or ""
    if "LlmBudgetExceeded" in reason or "예산" in reason:
        return "budget"
    if "응답에 이 쌍이 없" in reason:
        return "absent"
    if "쓰지 않아" in reason:
        return "disabled"
    return "failed"


def _edge_evidence(snap: RunSnapshot, edge: Any, label: str) -> Evidence:
    idx = snap.index.edge_index(edge)
    return Evidence(
        artifact="concept_graph.json",
        pointer=f"edges[{idx}]" if idx >= 0 else "edges[?]",
        label=label,
        detail=edge.to_dict(),
    )


def _block_evidence(by_block: dict, blocks: list[dict], label: str) -> Evidence:
    return Evidence(
        artifact="facts_by_block.json",
        pointer="blocks[cited=false]",
        label=label,
        detail={"doc_type": by_block.get("doc_type"),
                "summary": by_block.get("summary"),
                "blocks": blocks},
    )


# --------------------------------------------------------------------------- #
# 사람이 읽는 요약(CLI·화면 공용)
# --------------------------------------------------------------------------- #
def describe(trace: MissingTrace) -> str:
    """트레이스 하나를 여러 줄 텍스트로. CLI 가 그대로 출력한다."""
    arrow = " → ".join(
        f"{'✔' if s['ok'] else '✖'} {s['stage']}({s['note']})" for s in trace.trail
    )
    lines = [
        f"⚪ {trace.entity_name} — {trace.target_doc}",
        f"   {trace.label}  [{trace.confidence}]",
        f"   {trace.headline}",
    ]
    if arrow:
        lines.append(f"   흔적: {arrow}")
    if trace.next_action:
        lines.append(f"   👤 조치: {trace.next_action}")
    for ev in trace.evidence:
        lines.append(f"   📄 {ev.artifact} · {ev.pointer} — {ev.label}")
    return "\n".join(lines)


def describe_comparison(comparison: dict) -> str:
    """missing 이 아닌 항목을 사람이 읽는 한 줄로(대조 확인용)."""
    target = target_of(comparison)
    return (
        f"{comparison.get('result')} · {comparison.get('entity_name')} — "
        f"{comparison.get('target_doc')} | 기준 "
        f"{attributes_text((comparison.get('reference') or {}).get('attributes'))} vs 대상 "
        f"{attributes_text((target or {}).get('attributes'))}"
    )


__all__ = [
    "CAUSE_BLOCKED", "CAUSE_EVIDENCE_GATE", "CAUSE_EXTRACTION", "CAUSE_F5_LLM",
    "CAUSE_LABEL", "CAUSE_LLM_UNDECIDED", "CAUSE_RECALL", "CAUSE_UNKNOWN",
    "CONFIRMED", "INFERRED", "UNRESOLVED",
    "Evidence", "MissingTrace",
    "describe", "describe_comparison", "summarize", "trace_all_missing", "trace_missing",
]
