"""F7 후보 쌍 생성과 코드/온톨로지 확정 테스트 — 가짜 임베더, LLM 불필요."""

from contentcompare.fact.concept_builder import candidate_pairs, resolve_known
from contentcompare.fact.concept_models import BY_CODE, BY_ONTOLOGY, DIFFERS_BY, SAME_AS
from contentcompare.fact.fact_models import Fact, FactSet
from contentcompare.fact.fact_store import DocFacts, FactStore
from contentcompare.fact.ontology import Ontology, load_ontology


class _FakeEmbedder:
    """텍스트에 '온도'가 들어가면 서로 가깝게, 아니면 멀게 만드는 최소 임베더."""

    def embed(self, texts, kind="passage"):
        return [[1.0, 0.9] if "온도" in t else [1.0, 0.0] for t in texts]


def _fact(fact_id: str, name: str) -> Fact:
    return Fact(fact_id=fact_id, entity_name=name, search_text=name, evidence_text=name)


def _store(ref_names, target_names) -> FactStore:
    store = FactStore()
    store.add(DocFacts(
        doc_name="기준.xlsx",
        facts=FactSet(facts=[_fact(f"fact-row-{i}", n) for i, n in enumerate(ref_names, 1)]),
    ), is_reference=True)
    store.add(DocFacts(
        doc_name="규격서.docx",
        facts=FactSet(facts=[_fact(f"fact-word-{i}", n) for i, n in enumerate(target_names, 1)]),
    ))
    return store


def test_exact_name_pair_is_always_a_candidate():
    store = _store(["공칭용량"], ["공칭용량"])
    pairs = candidate_pairs(store, embedder=_FakeEmbedder())
    assert len(pairs) == 1 and pairs[0].exact is True


def test_spacing_difference_still_counts_as_exact():
    store = _store(["정격충전전압"], ["정격 충전 전압"])
    pairs = candidate_pairs(store, embedder=_FakeEmbedder())
    assert pairs[0].exact is True


def test_similar_names_become_candidates_for_review():
    """무관한 쌍도 후보로는 올라온다 — 판정은 개념 층이 한다."""
    store = _store(["1개월저장온도"], ["표준환경온도"])
    pairs = candidate_pairs(store, embedder=_FakeEmbedder(), min_score=0.3)
    assert len(pairs) == 1 and pairs[0].exact is False


def test_unrelated_pair_below_recall_min_is_dropped():
    store = _store(["1개월저장온도"], ["정격전압"])
    assert candidate_pairs(store, embedder=_FakeEmbedder(), min_score=0.99) == []


def test_pairs_are_generated_per_target_document():
    store = _store(["공칭용량"], ["공칭용량"])
    store.add(DocFacts(doc_name="발표.pptx", facts=FactSet(facts=[_fact("fact-ppt-1", "공칭용량")])))
    pairs = candidate_pairs(store, embedder=_FakeEmbedder())
    assert {p.right_doc for p in pairs} == {"규격서.docx", "발표.pptx"}


def test_works_without_embedder_via_bm25():
    """임베더가 없어도(오프라인) 후보 생성은 동작해야 한다."""
    store = _store(["공칭용량"], ["공칭용량"])
    assert candidate_pairs(store, embedder=None)


# --------------------------------------------------------------------- #
# 코드/온톨로지 확정
# --------------------------------------------------------------------- #
def test_exact_pair_is_confirmed_by_code_without_llm():
    store = _store(["공칭용량"], ["공칭용량"])
    edges, remaining = resolve_known(candidate_pairs(store, embedder=_FakeEmbedder()), Ontology())
    assert remaining == []
    assert edges[0].relation == SAME_AS and edges[0].decided_by == BY_CODE


def test_ontology_pair_skips_llm(tmp_path):
    p = tmp_path / "o.yaml"
    p.write_text('differs_by:\n  - names: ["1개월저장온도", "표준환경온도"]\n    axis: "측정조건"\n',
                 encoding="utf-8")
    store = _store(["1개월저장온도"], ["표준환경온도"])
    edges, remaining = resolve_known(
        candidate_pairs(store, embedder=_FakeEmbedder()), load_ontology(str(p))
    )
    assert remaining == []
    assert edges[0].relation == DIFFERS_BY
    assert edges[0].decided_by == BY_ONTOLOGY and edges[0].promoted is True
    assert edges[0].axis == "측정조건"


def test_same_name_pair_cannot_be_overridden_by_ontology(tmp_path):
    """**알려진 한계**: 온톨로지 키가 정규화 항목명이라, 이름이 같은 두 fact 를
    '사실은 다른 항목'이라고 선언할 방법이 없다. 이름이 같으면 코드가 잇는다.

    실측에서 이름 완전일치는 10/10 정확했으므로 지금은 감수한다. 실제로 문제가
    생기면 그때 항목명 자체를 구분하거나(문서 수정) 별도 예외 목록을 만든다 —
    관찰 전에 만들지 않는다.
    """
    p = tmp_path / "o.yaml"
    p.write_text('differs_by:\n  - names: ["공칭용량", "공칭 용량"]\n    axis: "대상"\n',
                 encoding="utf-8")
    store = _store(["공칭용량"], ["공칭용량"])
    edges, remaining = resolve_known(
        candidate_pairs(store, embedder=_FakeEmbedder()), load_ontology(str(p))
    )
    assert remaining == []
    assert edges[0].decided_by == BY_CODE  # 온톨로지 항목은 정규화하면 자기 자신이라 무시된다


def test_unknown_pair_is_left_for_llm():
    store = _store(["1개월저장온도"], ["표준환경온도"])
    edges, remaining = resolve_known(candidate_pairs(store, embedder=_FakeEmbedder()), Ontology())
    assert edges == [] and len(remaining) == 1
