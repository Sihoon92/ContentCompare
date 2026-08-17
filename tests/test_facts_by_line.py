"""line 단위 인용 커버리지 — 블록 단위 계측의 사각지대를 메운다(Phase 4a).

``facts_by_block`` 은 블록이 인용됐는지만 본다. 그래서 한 문단에 조건이 넷 있는데
fact 가 첫 줄만 인용해도 그 블록은 ``cited=true`` 로 보인다 — 나머지 셋이 통째로
버려진 것을 알 방법이 없다(설계 §12.3).

Phase 3 이 보존한 line 을 fact 의 ``evidence_text`` 와 대조해 **어느 줄이 실제로
근거로 쓰였는지**를 역산한다. LLM 을 부르지 않으므로 캐시 히트에도 남는다.
"""

from __future__ import annotations

from contentcompare.fact.fact_extractor import build_facts_by_block
from contentcompare.fact.fact_models import Fact, FactSet

CONDS = [
    "Charge temperature ranges:",
    "-5~5℃, 0.1C(4.55V)",
    "5~12℃, 0.3C(4.55V)",
    "12~15℃, 0.7C(4.55V)",
    "15~45℃, 1.2C(4.20V)",
]

COMPACT = {
    "doc_type": "word",
    "file_name": "spec.docx",
    "blocks": [
        {"id": "w_b001", "type": "paragraph", "text": "Battery Cell Specification"},
        {"id": "w_b002", "type": "paragraph", "text": " ".join(CONDS)},
        {"id": "w_b003", "type": "table", "rows": [["항목", "규격"], ["공칭용량", "1150"]]},
    ],
}

RAW = {
    "doc_type": "word",
    "file_name": "spec.docx",
    "blocks": [
        {"block_id": "w_b001", "order": 1, "type": "paragraph",
         "text": "Battery Cell Specification",
         "lines": [{"line_id": "w_b001:l01", "order": 1,
                    "raw_text": "Battery Cell Specification"}]},
        {"block_id": "w_b002", "order": 2, "type": "paragraph",
         "text": " ".join(CONDS),
         "lines": [{"line_id": f"w_b002:l{i:02d}", "order": i, "raw_text": t}
                   for i, t in enumerate(CONDS, start=1)]},
        {"block_id": "w_b003", "order": 3, "type": "table",
         "rows": [["항목", "규격"], ["공칭용량", "1150"]]},
    ],
}


def _fact(fact_id: str, block_id: str, evidence: str) -> Fact:
    return Fact(
        fact_id=fact_id, entity_name="충전온도범위", evidence_text=evidence,
        source={"doc_type": "word", "block_ids": [block_id]},
    )


def _blocks(out: dict) -> dict:
    return {b["id"]: b for b in out["blocks"]}


# --------------------------------------------------------------------------- #
# 사각지대 — 블록은 cited 인데 줄은 대부분 버려진 경우
# --------------------------------------------------------------------------- #
def test_one_quoted_line_leaves_the_rest_uncited():
    """상위 설계 §2 대표 사례. 블록 계측만으로는 이 손실이 안 보인다."""
    facts = FactSet(location="spec.docx", facts=[
        _fact("f1", "w_b002", "-5~5℃, 0.1C(4.55V)"),
    ])
    b = _blocks(build_facts_by_block(COMPACT, facts, lines_by_block=RAW))["w_b002"]

    assert b["cited"] is True            # 블록 단위로는 '인용됨'
    assert b["units_in"] == 5
    assert b["units_linked"] == 1        # 그러나 실제로는 한 줄뿐
    assert b["units_uncited"] == 4
    cited = {l["line_id"]: l["cited"] for l in b["lines"]}
    assert cited == {
        "w_b002:l01": False, "w_b002:l02": True,
        "w_b002:l03": False, "w_b002:l04": False, "w_b002:l05": False,
    }


def test_each_line_records_the_facts_that_quoted_it():
    facts = FactSet(location="spec.docx", facts=[
        _fact("f1", "w_b002", "5~12℃, 0.3C(4.55V)"),
        _fact("f2", "w_b002", "12~15℃, 0.7C(4.55V)"),
    ])
    b = _blocks(build_facts_by_block(COMPACT, facts, lines_by_block=RAW))["w_b002"]
    by_line = {l["line_id"]: l["fact_ids"] for l in b["lines"]}
    assert by_line["w_b002:l03"] == ["f1"]
    assert by_line["w_b002:l04"] == ["f2"]
    assert by_line["w_b002:l02"] == []
    assert b["units_linked"] == 2 and b["units_uncited"] == 3


def test_fact_quoting_the_whole_block_covers_every_line():
    """전부 인용했으면 전부 covered 다 — 이 계측은 '어느 줄이 인용됐나'만 답한다.

    ⚠️ 근거는 다 담겼는데 attributes 가 한 조건만 담은 축약은 **이 지표로 안 보인다**.
    그것은 evidence 대 attributes 의 문제이고 conditional_series 게이트의 몫이다.
    """
    facts = FactSet(location="spec.docx", facts=[_fact("f1", "w_b002", " ".join(CONDS))])
    b = _blocks(build_facts_by_block(COMPACT, facts, lines_by_block=RAW))["w_b002"]
    assert b["units_linked"] == 5 and b["units_uncited"] == 0


def test_fragment_of_a_line_still_counts_as_cited():
    """LLM 이 줄 일부만 따온 경우도 그 줄은 근거로 쓰인 것이다."""
    facts = FactSet(location="spec.docx", facts=[_fact("f1", "w_b002", "0.7C(4.55V)")])
    b = _blocks(build_facts_by_block(COMPACT, facts, lines_by_block=RAW))["w_b002"]
    assert [l["line_id"] for l in b["lines"] if l["cited"]] == ["w_b002:l04"]


def test_whitespace_differences_do_not_break_matching():
    """compact 는 공백을 병합하고 line 은 원문을 남긴다 — 정규화 후 대조해야 한다."""
    facts = FactSet(location="spec.docx", facts=[
        _fact("f1", "w_b002", "5~12℃,   0.3C(4.55V)"),
    ])
    b = _blocks(build_facts_by_block(COMPACT, facts, lines_by_block=RAW))["w_b002"]
    assert [l["line_id"] for l in b["lines"] if l["cited"]] == ["w_b002:l03"]


def test_evidence_from_another_block_does_not_leak():
    """다른 블록을 근거로 든 fact 가 이 블록의 줄을 켜면 안 된다."""
    facts = FactSet(location="spec.docx", facts=[
        _fact("f1", "w_b001", "-5~5℃, 0.1C(4.55V)"),   # 근거는 b001 이라고 주장
    ])
    b = _blocks(build_facts_by_block(COMPACT, facts, lines_by_block=RAW))["w_b002"]
    assert b["units_linked"] == 0 and b["units_uncited"] == 5


def test_fact_without_evidence_text_links_no_line():
    """근거 원문이 없으면 어느 줄에서 왔는지 알 수 없다 — 추측하지 않는다."""
    facts = FactSet(location="spec.docx", facts=[_fact("f1", "w_b002", "")])
    b = _blocks(build_facts_by_block(COMPACT, facts, lines_by_block=RAW))["w_b002"]
    assert b["cited"] is True            # 블록은 인용했다고 주장했으므로
    assert b["units_linked"] == 0        # 그러나 줄은 특정할 수 없다


# --------------------------------------------------------------------------- #
# 요약 계측
# --------------------------------------------------------------------------- #
def test_summary_totals_only_count_blocks_that_have_lines():
    """표·Excel 행은 이미 블록 자체가 최소 단위다 — 분모에 섞으면 비율이 흐려진다."""
    facts = FactSet(location="spec.docx", facts=[
        _fact("f1", "w_b002", "-5~5℃, 0.1C(4.55V)"),
    ])
    s = build_facts_by_block(COMPACT, facts, lines_by_block=RAW)["summary"]
    assert s["units_in"] == 6            # b001 의 1줄 + b002 의 5줄 (b003 표 제외)
    assert s["units_linked"] == 1
    assert s["units_uncited"] == 5


def test_table_block_has_no_line_fields():
    facts = FactSet(location="spec.docx", facts=[])
    b = _blocks(build_facts_by_block(COMPACT, facts, lines_by_block=RAW))["w_b003"]
    assert "lines" not in b and "units_in" not in b


# --------------------------------------------------------------------------- #
# 하위호환 — lines 를 안 주면 예전 그대로
# --------------------------------------------------------------------------- #
def test_without_lines_the_output_is_unchanged():
    """Excel/PPT 와 line 이 없는 기존 산출물은 스키마가 변하면 안 된다."""
    facts = FactSet(location="spec.docx", facts=[
        _fact("f1", "w_b002", "-5~5℃, 0.1C(4.55V)"),
    ])
    out = build_facts_by_block(COMPACT, facts)
    b = _blocks(out)["w_b002"]
    assert set(b) == {"id", "kind", "preview", "fact_ids", "cited"}
    assert "units_in" not in out["summary"]
