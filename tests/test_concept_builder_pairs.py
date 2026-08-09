"""F7 후보 쌍 진단(``candidate_pairs.json``) 테스트 — 가짜 임베더, LLM 불필요.

이 계측이 답해야 하는 질문은 하나다: **"정답 쌍이 애초에 후보에 들어오긴 했는가?"**
탈락한 후보가 남지 않으면 ``missing`` 오판의 원인이 recall 실패인지 그 하류인지
영원히 구분되지 않는다. 그래서 ``cut_by`` 가 이 파일의 핵심 검증 대상이다.
"""

from __future__ import annotations

from contentcompare.fact.concept_builder import build_concept_graph, candidate_pairs
from contentcompare.fact.fact_matcher import RANKED_OUT_EXTRA
from contentcompare.fact.fact_models import Fact, FactSet
from contentcompare.fact.fact_store import DocFacts, FactStore
from contentcompare.fact.ontology import load_ontology


class _AxisEmbedder:
    """문자열마다 직교 축을 주되, 지정한 쌍만 가깝게 만든다.

    ``near`` 에 ``{텍스트: 각도가중}`` 을 주면 그 텍스트들이 첫 축을 공유해 서로 가깝다.
    같은 인스턴스를 색인·질의에 함께 쓰므로 같은 문자열은 같은 벡터를 받는다.
    """

    def __init__(self, near: dict[str, float] | None = None) -> None:
        self._near = near or {}
        self._axis: dict[str, int] = {}

    def embed(self, texts, kind="passage"):
        out = []
        for t in texts:
            vec = [0.0] * 16
            if t in self._near:
                vec[0] = 1.0
                vec[1] = self._near[t]
            else:
                self._axis.setdefault(t, len(self._axis))
                vec[2 + self._axis[t] % 14] = 1.0
            out.append(vec)
        return out


def _fact(fact_id: str, name: str) -> Fact:
    return Fact(fact_id=fact_id, entity_name=name, search_text=name, evidence_text=name)


def _store(ref_names, target_names, target_doc="규격서.docx") -> FactStore:
    store = FactStore()
    store.add(DocFacts(
        doc_name="기준.xlsx",
        facts=FactSet(facts=[_fact(f"fact-row-{i}", n)
                             for i, n in enumerate(ref_names, 1)]),
    ), is_reference=True)
    store.add(DocFacts(
        doc_name=target_doc,
        facts=FactSet(facts=[_fact(f"fact-word-{i}", n)
                             for i, n in enumerate(target_names, 1)]),
    ))
    return store


def _slot(diag: list[dict], ref_fact_id: str, doc: str) -> dict:
    entry = next(e for e in diag if e["ref_fact_id"] == ref_fact_id)
    return next(t for t in entry["targets"] if t["doc"] == doc)


# --------------------------------------------------------------------------- #
# 회귀 — out-param 을 안 주면 기존 동작과 동일해야 한다
# --------------------------------------------------------------------------- #
def test_ranked_out_absent_keeps_previous_return_value():
    store = _store(["공칭용량", "충전환경온도"], ["공칭용량", "Nominal capacity"])
    emb = _AxisEmbedder({"공칭용량": 0.1, "Nominal capacity": 0.2})

    without = candidate_pairs(store, embedder=emb)
    with_diag = candidate_pairs(store, embedder=emb, ranked_out=[])

    assert [(p.left.fact_id, p.right.fact_id, p.exact) for p in without] == \
           [(p.left.fact_id, p.right.fact_id, p.exact) for p in with_diag]


def test_diag_covers_every_reference_fact_even_without_candidates():
    """후보가 0건인 기준 fact 도 행이 있어야 '왜 없었나'를 물을 수 있다."""
    store = _store(["공칭용량", "무관항목"], ["Nominal capacity"])
    diag: list[dict] = []
    candidate_pairs(store, embedder=_AxisEmbedder(), min_score=0.9, ranked_out=diag)

    assert {e["ref_fact_id"] for e in diag} == {"fact-row-1", "fact-row-2"}
    assert all(e["targets"][0]["doc"] == "규격서.docx" for e in diag)


# --------------------------------------------------------------------------- #
# cut_by — 임계 미달 vs 순위 밀림
# --------------------------------------------------------------------------- #
def test_below_threshold_candidate_is_recorded_with_min_score_reason():
    store = _store(["공칭용량"], ["전혀다른항목"])
    diag: list[dict] = []
    pairs = candidate_pairs(store, embedder=_AxisEmbedder(), min_score=0.5,
                            ranked_out=diag)

    assert pairs == []  # 판정 경로에는 안 들어간다(기존 동작 유지)
    ranked = _slot(diag, "fact-row-1", "규격서.docx")["ranked"]
    assert len(ranked) == 1
    assert ranked[0]["fact_id"] == "fact-word-1"
    assert ranked[0]["kept"] is False
    assert ranked[0]["cut_by"] == "min_score"


def test_rank_overflow_is_distinguished_from_threshold():
    """top_k 밖으로 밀린 후보는 min_score 가 아니라 top_k 로 기록된다.

    둘을 섞으면 조치가 갈린다 — 전자는 concept_recall_min, 후자는 concept_recall_top_k.
    """
    near = {f"유사{i}": i * 0.01 for i in range(1, 5)}
    store = _store(["기준항목"], list(near))
    near["기준항목"] = 0.0
    diag: list[dict] = []
    candidate_pairs(store, embedder=_AxisEmbedder(near), top_k=2, min_score=0.1,
                    ranked_out=diag)

    ranked = _slot(diag, "fact-row-1", "규격서.docx")["ranked"]
    assert [r["kept"] for r in ranked[:2]] == [True, True]
    assert [r["cut_by"] for r in ranked[:2]] == ["", ""]
    assert [r["cut_by"] for r in ranked[2:]] == ["top_k", "top_k"]


def test_ranked_out_extends_beyond_top_k():
    near = {f"유사{i}": i * 0.01 for i in range(1, 9)}
    store = _store(["기준항목"], list(near))
    near["기준항목"] = 0.0
    diag: list[dict] = []
    candidate_pairs(store, embedder=_AxisEmbedder(near), top_k=2, min_score=0.1,
                    ranked_out=diag)

    ranked = _slot(diag, "fact-row-1", "규격서.docx")["ranked"]
    assert len(ranked) == 2 + RANKED_OUT_EXTRA


# --------------------------------------------------------------------------- #
# 조기 종료 · 온톨로지 보강 — recall 실패로 오해하면 안 되는 두 경우
# --------------------------------------------------------------------------- #
def test_exact_name_match_is_marked_so_single_row_is_not_read_as_recall_failure():
    """이름 완전일치는 나머지 후보를 만들지 않고 조기 종료한다(정상 동작)."""
    store = _store(["공칭용량"], ["공칭 용량", "다른항목"])
    diag: list[dict] = []
    candidate_pairs(store, embedder=_AxisEmbedder(), ranked_out=diag)

    ranked = _slot(diag, "fact-row-1", "규격서.docx")["ranked"]
    assert len(ranked) == 1
    assert ranked[0]["method"] == "exact" and ranked[0]["kept"] is True


def test_ontology_augmented_pair_is_separated_from_recall(tmp_path):
    """유사도로는 못 잇는 쌍을 사람이 승격한 경우 — recall 이 찾은 것처럼 보이면 안 된다."""
    onto_file = tmp_path / "ontology.yaml"
    onto_file.write_text(
        'same_as:\n  - names: ["고객 표준 버전", "문서 기준 규격"]\n    reason: "확인"\n',
        encoding="utf-8")
    store = _store(["고객 표준 버전"], ["문서 기준 규격"])
    diag: list[dict] = []
    pairs = candidate_pairs(store, embedder=_AxisEmbedder(), min_score=0.9,
                            ontology=load_ontology(str(onto_file)), ranked_out=diag)

    assert len(pairs) == 1  # 보강으로 살아난다
    slot = _slot(diag, "fact-row-1", "규격서.docx")
    assert slot["from_ontology"] == ["fact-word-1"]
    assert all(not r["kept"] for r in slot["ranked"])  # recall 은 못 찾았다


# --------------------------------------------------------------------------- #
# build_concept_graph 의 pairs_out
# --------------------------------------------------------------------------- #
def test_pairs_out_carries_params_and_rows():
    store = _store(["공칭용량"], ["공칭용량"])
    out: dict = {}
    build_concept_graph(store, embedder=_AxisEmbedder(), top_k=4, min_score=0.25,
                        batch_size=7, pairs_out=out)

    assert out["reference"] == "기준.xlsx"
    assert out["params"]["top_k"] == 4
    assert out["params"]["min_score"] == 0.25
    assert out["params"]["batch_pairs"] == 7
    assert out["by_ref"][0]["entity_name"] == "공칭용량"


def test_pairs_out_omitted_does_not_change_graph():
    store = _store(["공칭용량"], ["공칭용량"])
    a = build_concept_graph(store, embedder=_AxisEmbedder())
    b = build_concept_graph(store, embedder=_AxisEmbedder(), pairs_out={})
    assert a.to_dict()["nodes"] == b.to_dict()["nodes"]
    assert a.to_dict()["edges"] == b.to_dict()["edges"]


def test_multiple_targets_each_get_their_own_slot():
    store = _store(["공칭용량"], ["공칭용량"])
    store.add(DocFacts(doc_name="발표.pptx",
                       facts=FactSet(facts=[_fact("fact-ppt-1", "공칭용량")])))
    diag: list[dict] = []
    candidate_pairs(store, embedder=_AxisEmbedder(), ranked_out=diag)

    docs = {t["doc"] for t in diag[0]["targets"]}
    assert docs == {"규격서.docx", "발표.pptx"}
