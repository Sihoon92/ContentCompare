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


class _OrthogonalEmbedder:
    """서로 다른 텍스트는 전부 직교(코사인 0) — recall 이 어떤 쌍도 만들지 못한다.

    "유사도로는 이을 수 없는 진짜 동의어"를 재현하기 위한 것이다. 같은 인스턴스를
    색인·질의에 함께 쓰므로 같은 문자열은 같은 축을 받는다.
    """

    def __init__(self) -> None:
        self._axis: dict[str, int] = {}

    def embed(self, texts, kind="passage"):
        out = []
        for t in texts:
            self._axis.setdefault(t, len(self._axis))
            vec = [0.0] * 32
            vec[self._axis[t] % 32] = 1.0
            out.append(vec)
        return out


def _fact(fact_id: str, name: str) -> Fact:
    return Fact(fact_id=fact_id, entity_name=name, search_text=name, evidence_text=name)


def _bm25_fact(fact_id: str, entity_name: str, search_text: str) -> Fact:
    """이름은 다르되 본문 토큰이 겹치게(또는 안 겹치게) 만들 수 있는 fact.

    BM25 폴백을 실제로 태우려면 완전일치(``entity_name`` 정규화 일치)를 피하면서도
    ``search_text`` 토큰이 겹쳐야 점수(기본 ``bm25_min_score=3.0``)를 넘는다.
    """
    return Fact(fact_id=fact_id, entity_name=entity_name, search_text=search_text,
                evidence_text=search_text)


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
    """임베더가 없어도(오프라인) BM25 로 실제 매칭이 되어야 한다.

    이름을 일부러 다르게 해 완전일치 경로(``FactMatcher.search`` 의 이름 조회)를
    피하고, ``search_text`` 토큰을 겹치게 해 BM25 점수가 기본 컷오프
    (``bm25_min_score=3.0``)를 넘도록 만든다 — 그래야 이 테스트가 "이름이 같아서
    사실은 완전일치로 통과했다"는 함정 없이 BM25 분기를 실제로 검증한다.
    """
    store = FactStore()
    store.add(DocFacts(doc_name="기준.xlsx", facts=FactSet(facts=[
        _bm25_fact("fact-row-1", "공칭용량", "공칭용량 1150 mAh 정격 충전 조건에서 측정한 값 기준"),
    ])), is_reference=True)
    store.add(DocFacts(doc_name="규격서.docx", facts=FactSet(facts=[
        _bm25_fact("fact-word-1", "정격용량", "정격용량 1150 mAh 정격 충전 조건에서 측정한 값 기준"),
    ])))
    pairs = candidate_pairs(store, embedder=None)
    assert len(pairs) == 1
    assert pairs[0].exact is False  # 완전일치가 아니라 BM25 로 왔음을 증명
    assert pairs[0].score > 0


def test_bm25_fallback_drops_pair_with_no_token_overlap():
    """BM25 검증의 대조군: 토큰이 전혀 안 겹치면 후보가 0건이어야 한다.

    위 테스트가 우연히(BM25 가 고장나도) 통과하는 게 아님을 증명한다 — 본문을
    무관한 내용으로 바꾸면 같은 설정에서 후보가 사라져야 한다.
    """
    store = FactStore()
    store.add(DocFacts(doc_name="기준.xlsx", facts=FactSet(facts=[
        _bm25_fact("fact-row-1", "공칭용량", "공칭용량 1150 mAh 정격 충전 조건에서 측정한 값 기준"),
    ])), is_reference=True)
    store.add(DocFacts(doc_name="규격서.docx", facts=FactSet(facts=[
        _bm25_fact("fact-word-1", "무관항목", "완전히 다른 내용의 문서 조각 텍스트입니다"),
    ])))
    assert candidate_pairs(store, embedder=None) == []


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


# --------------------------------------------------------------------- #
# 온톨로지 보강 — recall 임계와 **독립적**이어야 한다(설계 §F7-2)
# --------------------------------------------------------------------- #
def _synonym_ontology(tmp_path) -> "object":
    p = tmp_path / "o.yaml"
    p.write_text('same_as:\n  - names: ["고객 표준 버전", "문서 기준 규격"]\n'
                 '    reason: "둘 다 SEC Req. ver.4.7 을 가리킨다"\n', encoding="utf-8")
    return load_ontology(str(p))


def test_ontology_pair_is_a_candidate_even_when_recall_drops_it(tmp_path):
    """사람이 승격한 관계가 유사도 임계 뒤에 갇히면 안 된다.

    승격이 가장 필요한 쌍이 바로 "유사도로는 못 잇는 진짜 동의어"다(설계 §3.2).
    직교 임베더 + 임계 0.99 로 recall 이 확실히 걸러내게 만든 뒤, 온톨로지를 넘겼을
    때만 후보가 생기는지 본다 — 앞의 단정이 이 테스트를 load-bearing 하게 만든다.
    """
    store = _store(["고객 표준 버전"], ["문서 기준 규격"])
    embedder = _OrthogonalEmbedder()
    assert candidate_pairs(store, embedder=embedder, min_score=0.99) == []

    pairs = candidate_pairs(store, embedder=embedder, min_score=0.99,
                            ontology=_synonym_ontology(tmp_path))
    assert len(pairs) == 1
    assert pairs[0].score == 0.0 and pairs[0].exact is False
    edges, remaining = resolve_known(pairs, _synonym_ontology(tmp_path))
    assert remaining == []
    assert edges[0].relation == SAME_AS and edges[0].decided_by == BY_ONTOLOGY


def test_ontology_pair_survives_exact_name_early_return(tmp_path):
    """``FactMatcher.search()`` 는 정규화 이름 완전일치가 있으면 그 하나만 돌려주고
    끝낸다. 그래서 이름이 일치하는 fact 가 있는 기준 항목은 승격 관계를 맺을 방법이
    아예 없었다 — 온톨로지 보강이 recall 밖에서 돌아야 하는 두 번째 이유다.
    """
    store = _store(["고객 표준 버전"], ["고객 표준 버전", "문서 기준 규격"])
    recall_only = candidate_pairs(store, embedder=_FakeEmbedder())
    assert [p.right.fact_id for p in recall_only] == ["fact-word-1"]  # 완전일치 조기 종료

    pairs = candidate_pairs(store, embedder=_FakeEmbedder(),
                            ontology=_synonym_ontology(tmp_path))
    assert {p.right.fact_id for p in pairs} == {"fact-word-1", "fact-word-2"}


def test_ontology_augmentation_does_not_duplicate_recall_candidates(tmp_path):
    """이미 recall 이 만든 쌍을 온톨로지가 중복 추가하지 않는다."""
    p = tmp_path / "o.yaml"
    p.write_text('differs_by:\n  - names: ["1개월저장온도", "표준환경온도"]\n    axis: "측정조건"\n',
                 encoding="utf-8")
    store = _store(["1개월저장온도"], ["표준환경온도"])
    onto = load_ontology(str(p))
    assert len(candidate_pairs(store, embedder=_FakeEmbedder(), min_score=0.3)) == 1
    assert len(candidate_pairs(store, embedder=_FakeEmbedder(), min_score=0.3,
                               ontology=onto)) == 1
