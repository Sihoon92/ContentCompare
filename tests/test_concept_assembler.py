"""F7 그래프 조립 테스트 — 근거 검증과 병합 제약(전부 순수 코드)."""

from contentcompare.fact.concept_assembler import assemble, verify_evidence
from contentcompare.fact.concept_models import (
    BY_CODE,
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
    # 인용은 최소 길이(EVIDENCE_MIN_TOKENS) 이상이어야 근거로 인정된다.
    kw.setdefault("left_text", "공칭용량 1150")
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


def test_one_token_quote_is_rejected_even_though_it_exists():
    """실재하는 단어 하나만 인용하는 것은 근거가 아니다.

    원문 토큰 집합에는 ``entity_name`` 까지 들어가므로, 한 토큰짜리 인용은 커버리지
    100% 로 통과해 게이트를 무의미하게 만든다 — 그래서 최소 토큰 수 하한을 둔다.
    """
    facts = _facts()
    edge = _same(left_text="1150", right_text="공칭용량, 1150, mAh")
    assert verify_evidence(edge, facts[REF_A.key], facts[TGT_A.key]) is False


def test_normal_length_quote_still_passes():
    """대조군 — 하한이 정상 길이 인용까지 막지는 않는다."""
    facts = _facts()
    edge = _same(left_text="공칭용량 1150", right_text="공칭용량, 1150, mAh")
    assert verify_evidence(edge, facts[REF_A.key], facts[TGT_A.key]) is True


def test_demoted_edge_reason_marks_the_rejection():
    """강등된 엣지가 LLM 이 쓴 원래 사유를 그대로 리포트에 노출하면 안 된다.

    "둘 다 같은 규격을 가리킨다" 같은 문장이 '판정하지 못한 쌍' 표에 실리면 사람이
    그것을 믿고 `same_as` 로 승격한다 — 근거 검증 게이트를 사람 손으로 우회시키는
    셈이다. 원문 사유는 보존하되 거부 사실이 앞에 와야 한다.
    """
    edge = _same(left_text="지어낸 문구입니다", right_text="이것도 지어냈습니다",
                 reason="둘 다 SEC Req. ver.4.7 을 가리킨다", decided_by=BY_LLM)
    g = assemble(_members(), [edge], _facts())
    assert g.edges[0].relation == UNKNOWN
    assert g.edges[0].rejected_by == "evidence"
    assert g.edges[0].reason.startswith("[거부됨")
    assert "SEC Req. ver.4.7" in g.edges[0].reason  # 원문 사유는 보존


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
    """LLM 이 제안한 same_as 는 지어낸 인용문이면 강등된다(decided_by 기본값 = BY_LLM)."""
    edge = _same(left_text="지어낸 문구", right_text="지어낸 문구", decided_by=BY_LLM)
    g = assemble(_members(), [edge], _facts())
    assert g.node_id_of("기준.xlsx", "fact-row-1") != g.node_id_of("규격서.docx", "fact-word-7")
    assert g.edges[0].relation == UNKNOWN
    assert g.edges[0].rejected_by == "evidence"
    assert g.stats["rejected_evidence"] == 1


def test_ontology_promoted_same_as_merges_without_evidence():
    """온톨로지로 승격된 연결은 인용문이 없어도 병합된다(설계 §2.3 — LLM 주장이 아니므로).

    concept_builder.resolve_known() 은 BY_ONTOLOGY 엣지의 left_text/right_text 를
    채우지 않는다(사람은 항목명 쌍만 적을 뿐 인용문을 쓰지 않는다). 근거 검증을
    decided_by 와 무관하게 적용하면 이 연결이 100% 강등되어 승격 메커니즘이 죽는다.
    """
    edge = ConceptEdge(SAME_AS, REF_A, TGT_A, decided_by=BY_ONTOLOGY, promoted=True,
                        left_text="", right_text="")
    g = assemble(_members(), [edge], _facts())
    assert g.node_id_of("기준.xlsx", "fact-row-1") == g.node_id_of("규격서.docx", "fact-word-7")
    assert g.stats["rejected_evidence"] == 0
    assert g.stats["same_as"] == 1


def test_code_confirmed_same_as_merges_without_evidence():
    """코드가 정규화 이름 완전일치로 확정한 연결도 인용문 없이 병합된다."""
    edge = ConceptEdge(SAME_AS, REF_A, TGT_A, decided_by=BY_CODE, left_text="", right_text="")
    g = assemble(_members(), [edge], _facts())
    assert g.node_id_of("기준.xlsx", "fact-row-1") == g.node_id_of("규격서.docx", "fact-word-7")
    assert g.stats["rejected_evidence"] == 0


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
    assert downgraded[0].reason.startswith("[거부됨")


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


def test_promoted_same_as_survives_when_a_merge_must_be_sacrificed():
    """경쟁하는 두 same_as 후보 중 하나만 살아남을 수 있을 때 승격된 쪽이 이긴다.

    A=B(승격) 와 B=C(LLM) 가 경쟁하고 differs_by(A,C) 가 있어 둘 다 성립할 수는
    없다. LLM 엣지가 입력 목록에서 먼저 와도(정렬이 없다면 먼저 처리되어 B,C 가
    합쳐지고 승격 엣지가 희생됨) 승격 우선 정렬 덕분에 A=B 가 먼저 적용되어
    살아남고, 뒤늦게 처리되는 B=C 가 강등되어야 한다.
    """
    members = _members()
    llm_bc = _same(left=TGT_A, right=TGT_B, left_text="공칭용량, 1150, mAh",
                    right_text="표준환경온도, 21 ~ 29, ℃", decided_by=BY_LLM)
    promoted_ab = _same(right=TGT_A, decided_by=BY_ONTOLOGY, promoted=True)
    blocker = ConceptEdge(DIFFERS_BY, REF_A, TGT_B, axis="측정조건")
    # 입력 순서는 일부러 LLM 엣지가 먼저 오게 한다 — 정렬이 없으면 이 순서대로
    # 처리되어 반대 결과(B=C 생존, A=B 희생)가 나온다.
    g = assemble(members, [llm_bc, promoted_ab, blocker], _facts())

    assert g.node_id_of("기준.xlsx", "fact-row-1") == g.node_id_of("규격서.docx", "fact-word-7")
    assert g.node_id_of("규격서.docx", "fact-word-11") != g.node_id_of("기준.xlsx", "fact-row-1")

    downgraded = [e for e in g.edges if e.rejected_by == "differs_by"]
    assert len(downgraded) == 1
    assert downgraded[0].pair_key == tuple(sorted((TGT_A.key, TGT_B.key)))
