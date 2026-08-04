"""F7 LLM 배치 판정 + 그래프 빌드 테스트 — 가짜 chat/임베더."""

import json

import pytest

from contentcompare.fact.concept_builder import (
    build_concept_graph,
    candidate_pairs,
    judge_pairs,
)
from contentcompare.fact.concept_models import DIFFERS_BY, SAME_AS, UNKNOWN
from contentcompare.fact.fact_models import Fact, FactSet
from contentcompare.fact.fact_store import DocFacts, FactStore
from contentcompare.fact.llm_stage import LlmRunner
from contentcompare.fact.ontology import Ontology


class _ScriptedChat:
    """미리 정한 응답을 순서대로 돌려주는 chat. 호출 수를 센다."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.prompts = []

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        self.prompts.append(user)
        return self.responses.pop(0) if self.responses else "{}"


class _BoomChat:
    def complete(self, system, user, *, temperature=0.0):
        raise RuntimeError("네트워크 끊김")


class _FakeEmbedder:
    def embed(self, texts, kind="passage"):
        return [[1.0, 0.9] if "온도" in t else [1.0, 0.0] for t in texts]


def _fact(fact_id, name, evidence="") -> Fact:
    return Fact(fact_id=fact_id, entity_name=name, search_text=name,
                evidence_text=evidence or name)


def _store() -> FactStore:
    store = FactStore()
    store.add(DocFacts(doc_name="기준.xlsx", facts=FactSet(facts=[
        _fact("fact-row-20", "1개월저장온도", "-10.0, 35.0, 80.0"),
    ])), is_reference=True)
    store.add(DocFacts(doc_name="규격서.docx", facts=FactSet(facts=[
        _fact("fact-word-11", "표준환경온도", "표준환경온도, 21 ~ 29, ℃"),
    ])))
    return store


def _reply(**kw) -> str:
    base = {"left_fact_id": "fact-row-20", "right_fact_id": "fact-word-11",
            "relation": DIFFERS_BY, "axis": "측정조건", "reason": "저장 조건과 환경 조건"}
    base.update(kw)
    return json.dumps({"pairs": [base]}, ensure_ascii=False)


def test_llm_relation_becomes_edge():
    pairs = candidate_pairs(_store(), embedder=_FakeEmbedder())
    runner = LlmRunner(_ScriptedChat([_reply()]), max_calls=5)
    edges, _ = judge_pairs(runner, pairs)
    assert len(edges) == 1
    assert edges[0].relation == DIFFERS_BY and edges[0].axis == "측정조건"


def test_unknown_fact_id_in_reply_is_dropped():
    """LLM 이 주어지지 않은 id 를 지목해도 후보를 벗어나지 않는다."""
    pairs = candidate_pairs(_store(), embedder=_FakeEmbedder())
    runner = LlmRunner(_ScriptedChat([_reply(right_fact_id="없는id")]), max_calls=5)
    edges, _ = judge_pairs(runner, pairs)
    assert [e.relation for e in edges] == [UNKNOWN]


def test_missing_pair_in_reply_becomes_unknown():
    """응답이 일부 쌍을 빠뜨려도 그 쌍을 잃지 않는다."""
    pairs = candidate_pairs(_store(), embedder=_FakeEmbedder())
    runner = LlmRunner(_ScriptedChat(['{"pairs": []}']), max_calls=5)
    edges, _ = judge_pairs(runner, pairs)
    assert len(edges) == 1 and edges[0].relation == UNKNOWN


def test_batching_splits_calls():
    store = _store()
    store.reference.facts.facts.extend([
        _fact("fact-row-21", "3개월저장온도"), _fact("fact-row-22", "1년저장온도")])
    pairs = candidate_pairs(store, embedder=_FakeEmbedder())
    chat = _ScriptedChat(['{"pairs": []}'] * 3)
    judge_pairs(LlmRunner(chat, max_calls=5), pairs, batch_size=1)
    assert chat.calls == 3


def test_budget_exceeded_leaves_rest_unknown():
    store = _store()
    store.reference.facts.facts.append(_fact("fact-row-21", "3개월저장온도"))
    pairs = candidate_pairs(store, embedder=_FakeEmbedder())
    runner = LlmRunner(_ScriptedChat(['{"pairs": []}']), max_calls=1)
    edges, exhausted = judge_pairs(runner, pairs, batch_size=1)
    assert len(edges) == 2
    assert all(e.relation == UNKNOWN for e in edges)
    assert exhausted == 1  # 두 번째 배치의 1쌍이 예산 소진으로 판정되지 못했다


def test_ordinary_llm_failure_is_not_counted_as_budget_exhaustion():
    """네트워크 실패는 예산 문제가 아니다 — 카운터가 오염되면 안내가 틀린다."""
    pairs = candidate_pairs(_store(), embedder=_FakeEmbedder())
    _edges, exhausted = judge_pairs(LlmRunner(_BoomChat(), max_calls=5), pairs)
    assert exhausted == 0


def test_llm_failure_is_isolated_as_unknown():
    pairs = candidate_pairs(_store(), embedder=_FakeEmbedder())
    edges, _ = judge_pairs(LlmRunner(_BoomChat(), max_calls=5), pairs)
    assert [e.relation for e in edges] == [UNKNOWN]


# --------------------------------------------------------------------- #
# 오케스트레이션
# --------------------------------------------------------------------- #
def test_build_graph_without_llm_still_links_exact_names():
    store = FactStore()
    store.add(DocFacts(doc_name="기준.xlsx",
                       facts=FactSet(facts=[_fact("fact-row-1", "공칭용량", "1150")])),
              is_reference=True)
    store.add(DocFacts(doc_name="규격서.docx",
                       facts=FactSet(facts=[_fact("fact-word-7", "공칭용량", "공칭용량 1150 mAh")])))
    graph = build_concept_graph(store, embedder=_FakeEmbedder(), runner=None)
    assert graph.node_id_of("기준.xlsx", "fact-row-1") == graph.node_id_of("규격서.docx", "fact-word-7")
    assert graph.stats["llm_calls"] == 0


def test_build_graph_does_not_link_different_concepts():
    """이 계획의 존재 이유 — 1개월저장온도와 표준환경온도는 이어지면 안 된다."""
    runner = LlmRunner(_ScriptedChat([_reply()]), max_calls=5)
    graph = build_concept_graph(_store(), embedder=_FakeEmbedder(), runner=runner)
    assert graph.partners("기준.xlsx", "fact-row-20", "규격서.docx") == []
    assert graph.stats["differs_by"] == 1


def test_build_graph_links_when_llm_says_same_with_quotes():
    reply = _reply(relation=SAME_AS, axis="", left_text="-10.0, 35.0, 80.0",
                   right_text="표준환경온도, 21 ~ 29, ℃")
    runner = LlmRunner(_ScriptedChat([reply]), max_calls=5)
    graph = build_concept_graph(_store(), embedder=_FakeEmbedder(), runner=runner)
    assert len(graph.partners("기준.xlsx", "fact-row-20", "규격서.docx")) == 1


def test_build_graph_rejects_same_as_without_real_quotes():
    reply = _reply(relation=SAME_AS, axis="", left_text="지어낸 근거",
                   right_text="이것도 지어냄")
    runner = LlmRunner(_ScriptedChat([reply]), max_calls=5)
    graph = build_concept_graph(_store(), embedder=_FakeEmbedder(), runner=runner)
    assert graph.partners("기준.xlsx", "fact-row-20", "규격서.docx") == []
    assert graph.stats["rejected_evidence"] == 1


def test_build_graph_stats_report_pair_sources():
    runner = LlmRunner(_ScriptedChat([_reply()]), max_calls=5)
    graph = build_concept_graph(_store(), embedder=_FakeEmbedder(), runner=runner)
    for key in ("pairs_considered", "pairs_from_ontology", "pairs_by_code",
                "pairs_by_llm", "llm_calls"):
        assert key in graph.stats


def test_budget_exhaustion_is_visible_in_stats():
    """예산 초과는 조용히 전 항목 missing 으로 귀결된다 — 계측으로 드러나야 한다.

    후보 쌍 3건을 배치 1로 쪼개고 예산을 1로 두면 2쌍이 판정되지 못한다.
    """
    store = _store()
    store.reference.facts.facts.extend([
        _fact("fact-row-21", "3개월저장온도"), _fact("fact-row-22", "1년저장온도")])
    runner = LlmRunner(_ScriptedChat(['{"pairs": []}']), max_calls=1)
    graph = build_concept_graph(store, embedder=_FakeEmbedder(), runner=runner,
                                batch_size=1)
    assert graph.stats["budget_exhausted_pairs"] == 2


def test_no_budget_exhaustion_when_budget_is_enough():
    """대조군 — 예산이 넉넉하면 카운터가 0 이다."""
    runner = LlmRunner(_ScriptedChat([_reply()]), max_calls=5)
    graph = build_concept_graph(_store(), embedder=_FakeEmbedder(), runner=runner)
    assert graph.stats["budget_exhausted_pairs"] == 0


def test_edges_without_runner_are_not_attributed_to_llm():
    """LLM 을 쓰지 않아 판정하지 않은 쌍을 ``decided_by=llm`` 으로 기록하면 안 된다."""
    from contentcompare.fact.concept_models import BY_LLM, BY_NONE

    graph = build_concept_graph(_store(), embedder=_FakeEmbedder(), runner=None)
    assert [e.relation for e in graph.edges] == [UNKNOWN]
    assert graph.edges[0].decided_by == BY_NONE
    assert graph.edges[0].decided_by != BY_LLM


def test_empty_store_yields_empty_graph():
    graph = build_concept_graph(FactStore(), embedder=_FakeEmbedder(), runner=None)
    assert graph.nodes == [] and graph.edges == []
