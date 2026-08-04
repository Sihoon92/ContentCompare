"""F7 그래프 조립 테스트 — 근거 검증과 병합 제약(전부 순수 코드)."""

from contentcompare.fact.concept_assembler import assemble, verify_evidence
from contentcompare.fact.concept_models import (
    BY_LLM,
    BY_ONTOLOGY,
    DIFFERS_BY,
    SAME_AS,
    UNKNOWN,
    ConceptEdge,
    ConceptMember,
    FactRef,
)
from contentcompare.fact.fact_models import Fact

REF_A = FactRef("기준.xlsx", "fact-row-1")
TGT_A = FactRef("규격서.docx", "fact-word-7")
TGT_B = FactRef("규격서.docx", "fact-word-11")


def _fact(fact_id: str, name: str, evidence: str) -> Fact:
    return Fact(fact_id=fact_id, entity_name=name, evidence_text=evidence,
                search_text=f"{name} {evidence}")


def _facts() -> dict:
    return {
        REF_A.key: _fact("fact-row-1", "공칭용량", "1150"),
        TGT_A.key: _fact("fact-word-7", "공칭용량", "공칭용량, 1150, mAh"),
        TGT_B.key: _fact("fact-word-11", "표준환경온도", "표준환경온도, 21 ~ 29, ℃"),
    }


def _members() -> list[ConceptMember]:
    return [
        ConceptMember("기준.xlsx", "fact-row-1", "공칭용량"),
        ConceptMember("규격서.docx", "fact-word-7", "공칭용량"),
        ConceptMember("규격서.docx", "fact-word-11", "표준환경온도"),
    ]


def _same(left=REF_A, right=TGT_A, **kw) -> ConceptEdge:
    kw.setdefault("left_text", "1150")
    kw.setdefault("right_text", "공칭용량, 1150, mAh")
    return ConceptEdge(SAME_AS, left, right, **kw)


# --------------------------------------------------------------------- #
# 근거 검증
# --------------------------------------------------------------------- #
def test_quoted_evidence_present_in_source_passes():
    facts = _facts()
    assert verify_evidence(_same(), facts[REF_A.key], facts[TGT_A.key]) is True


def test_fabricated_quote_is_rejected():
    """LLM 이 same_as 를 남발해도 근거가 없으면 성립하지 않는다(설계 §2.3)."""
    facts = _facts()
    edge = _same(left_text="존재하지 않는 문구입니다", right_text="이것도 없습니다")
    assert verify_evidence(edge, facts[REF_A.key], facts[TGT_A.key]) is False


def test_empty_quote_is_rejected():
    facts = _facts()
    assert verify_evidence(_same(left_text="", right_text=""), facts[REF_A.key],
                           facts[TGT_A.key]) is False


def test_separator_difference_still_passes():
    """LLM 이 셀 구분자를 바꿔 옮겨도 실재하는 근거는 통과해야 한다."""
    facts = _facts()
    edge = _same(right_text="공칭용량 1150 mAh")
    assert verify_evidence(edge, facts[REF_A.key], facts[TGT_A.key]) is True


# --------------------------------------------------------------------- #
# 병합
# --------------------------------------------------------------------- #
def test_same_as_merges_members_into_one_node():
    g = assemble(_members(), [_same()], _facts())
    assert g.node_id_of("기준.xlsx", "fact-row-1") == g.node_id_of("규격서.docx", "fact-word-7")
    assert g.stats["same_as"] == 1


def test_unmerged_facts_stay_in_their_own_nodes():
    g = assemble(_members(), [], _facts())
    assert len({n.concept_id for n in g.nodes}) == 3
    assert g.partners("기준.xlsx", "fact-row-1", "규격서.docx") == []


def test_rejected_evidence_prevents_merge():
    edge = _same(left_text="지어낸 문구", right_text="지어낸 문구")
    g = assemble(_members(), [edge], _facts())
    assert g.node_id_of("기준.xlsx", "fact-row-1") != g.node_id_of("규격서.docx", "fact-word-7")
    assert g.edges[0].relation == UNKNOWN
    assert g.edges[0].rejected_by == "evidence"
    assert g.stats["rejected_evidence"] == 1


def test_differs_by_blocks_merge_of_same_pair():
    """같은 쌍에 same_as 와 differs_by 가 함께 오면 병합하지 않는다."""
    edges = [
        _same(right=TGT_B, right_text="표준환경온도, 21 ~ 29, ℃"),
        ConceptEdge(DIFFERS_BY, REF_A, TGT_B, axis="측정조건"),
    ]
    g = assemble(_members(), edges, _facts())
    assert g.node_id_of("기준.xlsx", "fact-row-1") != g.node_id_of("규격서.docx", "fact-word-11")
    downgraded = [e for e in g.edges if e.rejected_by == "differs_by"]
    assert len(downgraded) == 1 and downgraded[0].relation == UNKNOWN


def test_differs_by_blocks_transitive_merge():
    """A=B, B=C 로 이어지려는데 A≠C 가 있으면 두 번째 병합을 막는다."""
    members = _members()
    edges = [
        _same(right=TGT_A),
        _same(left=TGT_A, right=TGT_B, left_text="공칭용량, 1150, mAh",
              right_text="표준환경온도, 21 ~ 29, ℃"),
        ConceptEdge(DIFFERS_BY, REF_A, TGT_B, axis="측정조건"),
    ]
    g = assemble(members, edges, _facts())
    assert g.node_id_of("기준.xlsx", "fact-row-1") == g.node_id_of("규격서.docx", "fact-word-7")
    assert g.node_id_of("규격서.docx", "fact-word-11") != g.node_id_of("기준.xlsx", "fact-row-1")


def test_promoted_edge_is_applied_before_llm_edge():
    """사람이 승격한 관계가 LLM 판단보다 먼저 적용된다."""
    llm_edge = _same(right=TGT_B, right_text="표준환경온도, 21 ~ 29, ℃", decided_by=BY_LLM)
    promoted = ConceptEdge(DIFFERS_BY, REF_A, TGT_B, axis="측정조건",
                           decided_by=BY_ONTOLOGY, promoted=True)
    g = assemble(_members(), [llm_edge, promoted], _facts())
    assert g.node_id_of("기준.xlsx", "fact-row-1") != g.node_id_of("규격서.docx", "fact-word-11")


def test_node_ids_are_stable_and_labelled():
    g = assemble(_members(), [_same()], _facts())
    ids = [n.concept_id for n in g.nodes]
    assert ids == sorted(ids) and ids[0] == "c-0001"
    assert g.nodes[0].label == "공칭용량"


def test_unknown_relation_never_merges():
    g = assemble(_members(), [ConceptEdge(UNKNOWN, REF_A, TGT_A)], _facts())
    assert g.node_id_of("기준.xlsx", "fact-row-1") != g.node_id_of("규격서.docx", "fact-word-7")
    assert g.stats["unknown"] == 1


def test_edge_referencing_unknown_fact_is_ignored():
    edge = _same(right=FactRef("규격서.docx", "없는id"))
    g = assemble(_members(), [edge], _facts())
    assert g.stats["rejected_evidence"] == 0
    assert len({n.concept_id for n in g.nodes}) == 3
