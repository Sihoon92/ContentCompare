"""``⚪ 대상에 없음`` 원인 분류기 테스트 — 합성 산출물만 쓰고 LLM/네트워크 불필요.

원인 여섯 가지를 각각 재현한다. **분류 순서**가 이 모듈의 정확성을 좌우하므로
(실측에서 ``differs_by`` 가 근거 게이트 강등을 덮었다) 우선순위 테스트를 따로 둔다.
"""

from __future__ import annotations

import json

from contentcompare.fact.artifact_reader import RunRef, load_snapshot
from contentcompare.fact.missing_trace import (
    CAUSE_BLOCKED,
    CAUSE_EVIDENCE_GATE,
    CAUSE_EXTRACTION,
    CAUSE_F5_LLM,
    CAUSE_LLM_UNDECIDED,
    CAUSE_RECALL,
    CAUSE_UNKNOWN,
    CONFIRMED,
    INFERRED,
    UNRESOLVED,
    describe,
    summarize,
    trace_all_missing,
    trace_missing,
)

REF_DOC = "기준.xlsx"
TGT_DOC = "규격서.docx"
REF_ID = "fact-row-7"


def _write(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _comparison(result="missing", decided_by="code", entity="공칭전압") -> dict:
    return {
        "entity_name": entity, "target_doc": TGT_DOC, "result": result,
        "mismatch_attributes": [], "match_score": 0.0, "match_method": "none",
        "decided_by": decided_by, "reason": "사유",
        "reference": {"fact_id": REF_ID, "entity_name": entity, "attributes": {},
                      "evidence_text": "3.89", "source": {"doc_type": "excel", "row": 7}},
        "target": None,
    }


def _edge(relation, right_id="fact-word-4", **kw) -> dict:
    edge = {
        "relation": relation,
        "left": {"doc": REF_DOC, "fact_id": REF_ID},
        "right": {"doc": TGT_DOC, "fact_id": right_id},
        "axis": "", "left_text": "", "right_text": "", "reason": "",
        "decided_by": "llm", "promoted": False, "recall_score": 0.65, "rejected_by": "",
    }
    edge.update(kw)
    return edge


def _snap(tmp_path, *, edges=None, comparisons=None, pairs=None, by_block=None):
    """합성 실행 하나를 만들고 읽어 온다."""
    d = tmp_path / "기준_xlsx"
    _write(d / "comparison_result.json", {
        "reference": REF_DOC, "stats": {},
        "comparisons": comparisons if comparisons is not None else [_comparison()],
    })
    _write(d / "concept_graph.json",
           {"nodes": [], "edges": edges or [], "stats": {}})
    if pairs is not None:
        _write(d / "candidate_pairs.json", pairs)
    if by_block is not None:
        _write(tmp_path / "규격서_docx" / "facts_by_block.json", by_block)
    return load_snapshot(RunRef(label="기준_xlsx", dir=d))


def _trace(snap):
    return trace_missing(snap, snap.comparisons[0])


def _ranked(*rows, from_ontology=None):
    return {"by_ref": [{
        "ref_fact_id": REF_ID, "entity_name": "공칭전압",
        "targets": [{"doc": TGT_DOC, "ranked": list(rows),
                     "from_ontology": from_ontology or []}],
    }]}


def _row(fact_id="fact-word-9", score=0.2, kept=False, cut_by="min_score",
         method="embed", entity="Cell weight"):
    return {"fact_id": fact_id, "entity_name": entity, "score": score,
            "method": method, "kept": kept, "cut_by": cut_by}


# --------------------------------------------------------------------------- #
# 원인 ⑥ — 후보는 있었는데 F5 LLM 이 없다고 답함
# --------------------------------------------------------------------------- #
def test_f5_llm_said_missing(tmp_path):
    snap = _snap(tmp_path, comparisons=[_comparison(decided_by="llm")],
                 edges=[_edge("same_as")])
    trace = _trace(snap)
    assert trace.cause == CAUSE_F5_LLM
    assert trace.confidence == CONFIRMED
    assert trace.evidence[0].artifact == "comparison_result.json"


def test_decided_by_code_never_reads_as_f5_llm(tmp_path):
    """후보가 없으면 LLM 을 부르지 않으므로 decided_by 가 code 로 남는다 — 판별의 핵심."""
    snap = _snap(tmp_path, edges=[_edge("differs_by")])
    assert _trace(snap).cause != CAUSE_F5_LLM


# --------------------------------------------------------------------------- #
# 원인 ③ — 근거 게이트 강등
# --------------------------------------------------------------------------- #
def test_evidence_gate_rejection(tmp_path):
    snap = _snap(tmp_path, edges=[
        _edge("unknown", rejected_by="evidence",
              reason="[거부됨: 근거 인용이 원문에 없음] 거부된 주장: 같은 항목입니다.")])
    trace = _trace(snap)
    assert trace.cause == CAUSE_EVIDENCE_GATE
    assert trace.confidence == CONFIRMED
    assert trace.subcause == "evidence"
    assert trace.evidence[0].artifact == "concept_graph.json"
    assert trace.evidence[0].pointer == "edges[0]"
    assert "인용" in trace.next_action


def test_differs_by_rejection_suggests_ontology_review(tmp_path):
    snap = _snap(tmp_path, edges=[_edge("unknown", rejected_by="differs_by")])
    trace = _trace(snap)
    assert trace.cause == CAUSE_EVIDENCE_GATE
    assert "differs_by" in trace.next_action or "ontology" in trace.next_action


# --------------------------------------------------------------------------- #
# 원인 ② — LLM 미판정
# --------------------------------------------------------------------------- #
def test_budget_exhausted_is_named(tmp_path):
    snap = _snap(tmp_path, edges=[
        _edge("unknown", reason="LLM 판정 실패(LlmBudgetExceeded)")])
    trace = _trace(snap)
    assert trace.cause == CAUSE_LLM_UNDECIDED
    assert trace.subcause == "budget"
    assert "max_llm_calls_per_concept" in trace.next_action


def test_pair_absent_from_response_is_named(tmp_path):
    snap = _snap(tmp_path, edges=[
        _edge("unknown", reason="LLM 응답에 이 쌍이 없었습니다")])
    trace = _trace(snap)
    assert trace.subcause == "absent"
    assert "concept_batch_pairs" in trace.next_action


def test_llm_disabled_is_named(tmp_path):
    snap = _snap(tmp_path, edges=[
        _edge("unknown", reason="LLM 을 쓰지 않아 판정하지 않음", decided_by="")])
    assert _trace(snap).subcause == "disabled"


# --------------------------------------------------------------------------- #
# 원인 ⑤ — 다른 개념으로 차단
# --------------------------------------------------------------------------- #
def test_human_promoted_block_is_confirmed(tmp_path):
    snap = _snap(tmp_path, edges=[
        _edge("differs_by", axis="측정조건", decided_by="ontology", promoted=True)])
    trace = _trace(snap)
    assert trace.cause == CAUSE_BLOCKED
    assert trace.confidence == CONFIRMED
    assert "의도된 동작" in trace.headline


def test_llm_only_block_stays_inferred(tmp_path):
    """전부 LLM 이 '다르다'고 한 경우 — 맞는 상대가 후보에 없었을 수도 있다."""
    snap = _snap(tmp_path, edges=[
        _edge("differs_by", right_id="fact-word-1", axis="물리량"),
        _edge("differs_by", right_id="fact-word-2", axis="물리량"),
    ])
    trace = _trace(snap)
    assert trace.cause == CAUSE_BLOCKED
    assert trace.confidence == INFERRED
    assert "2건" in trace.headline


def test_block_attaches_candidate_ranking_when_available(tmp_path):
    snap = _snap(tmp_path, edges=[_edge("differs_by")],
                 pairs=_ranked(_row(kept=True, cut_by="", score=0.7)))
    artifacts = [e.artifact for e in _trace(snap).evidence]
    assert "candidate_pairs.json" in artifacts


# --------------------------------------------------------------------------- #
# 분류 우선순위 — 실측이 드러낸 결함
# --------------------------------------------------------------------------- #
def test_rejection_wins_over_common_differs_by(tmp_path):
    """``differs_by`` 는 후보마다 흔히 붙는다. 그것이 진짜 원인을 덮으면 안 된다.

    실측(_runs/en_word)에서 근거 게이트 강등 8건이 전부 '차단'으로 잘못 분류됐다.
    """
    snap = _snap(tmp_path, edges=[
        _edge("differs_by", right_id="fact-word-1"),
        _edge("differs_by", right_id="fact-word-2"),
        _edge("unknown", right_id="fact-word-4", rejected_by="evidence"),
    ])
    assert _trace(snap).cause == CAUSE_EVIDENCE_GATE


def test_undecided_wins_over_differs_by(tmp_path):
    snap = _snap(tmp_path, edges=[
        _edge("differs_by", right_id="fact-word-1"),
        _edge("unknown", right_id="fact-word-4", reason="LLM 응답에 이 쌍이 없었습니다"),
    ])
    assert _trace(snap).cause == CAUSE_LLM_UNDECIDED


def test_edges_to_other_documents_are_ignored(tmp_path):
    """다른 대상 문서와의 관계가 이 문서의 판정을 설명하면 안 된다."""
    other = _edge("unknown", rejected_by="evidence")
    other["right"] = {"doc": "발표.pptx", "fact_id": "fact-ppt-1"}
    snap = _snap(tmp_path, edges=[other, _edge("differs_by")])
    assert _trace(snap).cause == CAUSE_BLOCKED


# --------------------------------------------------------------------------- #
# 원인 ① — recall 실패
# --------------------------------------------------------------------------- #
def test_recall_below_threshold(tmp_path):
    snap = _snap(tmp_path, edges=[], pairs=_ranked(_row(score=0.28, cut_by="min_score")))
    trace = _trace(snap)
    assert trace.cause == CAUSE_RECALL
    assert trace.confidence == CONFIRMED
    assert trace.subcause == "min_score"
    assert "concept_recall_min" in trace.next_action
    assert trace.evidence[0].artifact == "candidate_pairs.json"


def test_recall_rank_overflow_suggests_top_k(tmp_path):
    snap = _snap(tmp_path, edges=[], pairs=_ranked(_row(score=0.55, cut_by="top_k")))
    trace = _trace(snap)
    assert trace.subcause == "top_k"
    assert "concept_recall_top_k" in trace.next_action


def test_recall_with_no_candidates_at_all(tmp_path):
    snap = _snap(tmp_path, edges=[], pairs=_ranked())
    trace = _trace(snap)
    assert trace.cause == CAUSE_RECALL
    assert trace.subcause == "empty"


def test_candidate_kept_but_no_edge_is_inferred(tmp_path):
    snap = _snap(tmp_path, edges=[], pairs=_ranked(_row(kept=True, cut_by="", score=0.8)))
    trace = _trace(snap)
    assert trace.subcause == "no_edge"
    assert trace.confidence == INFERRED


# --------------------------------------------------------------------------- #
# 원인 ④ — F3 추출 누락
# --------------------------------------------------------------------------- #
def test_no_facts_extracted_at_all(tmp_path):
    snap = _snap(tmp_path, edges=[], by_block={
        "doc_type": "word", "blocks": [
            {"id": "w_b001", "kind": "text", "preview": "공칭전압은 3.85V", "cited": False}],
        "summary": {"facts_out": 0}})
    trace = _trace(snap)
    assert trace.cause == CAUSE_EXTRACTION
    assert trace.confidence == CONFIRMED
    assert trace.evidence[0].artifact == "facts_by_block.json"


def test_uncited_block_mentioning_the_entity_is_inferred(tmp_path):
    snap = _snap(tmp_path, edges=[], by_block={
        "doc_type": "word", "blocks": [
            {"id": "w_b001", "kind": "text", "preview": "다른 내용", "cited": True},
            {"id": "w_b004", "kind": "text", "preview": "공칭전압은 3.85V 이다.",
             "cited": False},
        ], "summary": {"facts_out": 1}})
    trace = _trace(snap)
    assert trace.cause == CAUSE_EXTRACTION
    assert trace.confidence == INFERRED
    assert "w_b004" in json.dumps(trace.evidence[0].detail, ensure_ascii=False)


def test_uncited_block_without_the_entity_does_not_trigger(tmp_path):
    snap = _snap(tmp_path, edges=[], pairs=_ranked(_row(score=0.2)), by_block={
        "doc_type": "word", "blocks": [
            {"id": "w_b001", "kind": "text", "preview": "무관한 문장", "cited": False},
            {"id": "w_b002", "kind": "text", "preview": "쓰인 문장", "cited": True},
        ], "summary": {"facts_out": 1}})
    assert _trace(snap).cause == CAUSE_RECALL


def test_extraction_check_runs_before_recall(tmp_path):
    """대상 문서에 fact 자체가 없으면 recall 을 탓할 수 없다."""
    snap = _snap(tmp_path, edges=[], pairs=_ranked(_row(score=0.1)), by_block={
        "doc_type": "word",
        "blocks": [{"id": "w_b001", "kind": "text", "preview": "무엇이든", "cited": False}],
        "summary": {"facts_out": 0}})
    assert _trace(snap).cause == CAUSE_EXTRACTION


# --------------------------------------------------------------------------- #
# 계측이 없을 때 — 정직하게 '판정 불가'
# --------------------------------------------------------------------------- #
def test_without_instrumentation_the_answer_is_honest(tmp_path):
    """G2/G3 없이는 ①④를 가릴 수 없다. 아는 척하지 않는다."""
    snap = _snap(tmp_path, edges=[])
    trace = _trace(snap)
    assert trace.cause == CAUSE_UNKNOWN
    assert trace.confidence == UNRESOLVED
    assert "save_candidate_pairs" in trace.next_action


def test_gate_and_block_still_resolve_without_instrumentation(tmp_path):
    """②③⑤⑥ 는 개념 그래프만으로 확정된다 — 스냅샷에서도 쓸 수 있다."""
    for edge, expected in (
        (_edge("unknown", rejected_by="evidence"), CAUSE_EVIDENCE_GATE),
        (_edge("unknown", reason="LLM 응답에 이 쌍이 없었습니다"), CAUSE_LLM_UNDECIDED),
        (_edge("differs_by", promoted=True, decided_by="ontology"), CAUSE_BLOCKED),
    ):
        assert _trace(_snap(tmp_path, edges=[edge])).cause == expected


# --------------------------------------------------------------------------- #
# 집계·출력·방어
# --------------------------------------------------------------------------- #
def test_trace_all_and_summarize(tmp_path):
    snap = _snap(tmp_path, comparisons=[
        _comparison(entity="A"), _comparison(entity="B"),
        _comparison(result="match", entity="C"),
    ], edges=[_edge("differs_by", promoted=True, decided_by="ontology")])
    traces = trace_all_missing(snap)
    assert len(traces) == 2  # match 는 제외
    assert summarize(traces) == {CAUSE_BLOCKED: 2}


def test_non_missing_comparison_is_handled(tmp_path):
    snap = _snap(tmp_path, comparisons=[_comparison(result="mismatch")])
    trace = trace_missing(snap, snap.comparisons[0])
    assert trace.cause == CAUSE_UNKNOWN
    assert "아닙니다" in trace.headline


def test_describe_renders_every_part(tmp_path):
    snap = _snap(tmp_path, edges=[_edge("unknown", rejected_by="evidence")])
    text = describe(_trace(snap))
    assert "공칭전압" in text and "규격서.docx" in text
    assert "확정" in text and "흔적:" in text and "조치:" in text
    assert "concept_graph.json" in text


def test_trail_marks_the_failing_gate(tmp_path):
    snap = _snap(tmp_path, edges=[_edge("unknown", rejected_by="evidence")])
    trail = _trace(snap).trail
    assert [s["ok"] for s in trail] == [True, True, False, False]
    assert trail[0]["who"] == "embed" and trail[1]["who"] == "llm"
