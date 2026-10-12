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


# --------------------------------------------------------------------- #
# 출력 절단 → 배치 축소 (F2/F3 의 run_batch 를 F7 에도)
#
# 오늘은 ``except Exception`` 이 LengthLimitError 를 삼켜 **배치 20쌍이 통째로**
# unknown 이 된다. 쌍끼리 독립이라(carry 가 없다) F2 보다 단순하게 붙는다.
# --------------------------------------------------------------------- #
class _TruncatingConceptRunner:
    """쌍이 ``limit`` 개 이상 실리면 출력이 잘린 것처럼 구는 가짜 runner.

    ``always`` 에 fact_id 를 주면 그 쌍이 실린 프롬프트는 **크기와 무관하게** 잘린다 —
    더 쪼갤 수 없는 조각(1쌍)을 만들어 하한 동작을 시험하려는 것이다.
    """

    def __init__(self, limit, *, always=""):
        self.limit = limit
        self.always = always
        self.calls = 0
        self.prompts = []

    def complete_json(self, system, user, *, schema=None):
        import re

        from contentcompare.llm.truncation import LengthLimitError

        self.calls += 1
        self.prompts.append(user)
        loaded = len(re.findall(r"^\[쌍 ", user, re.M))
        if loaded >= self.limit or (self.always and self.always in user):
            raise LengthLimitError("잘렸습니다", output="{", backend="fake")
        lefts = re.findall(r"^left_fact_id: (\S+)", user, re.M)
        rights = re.findall(r"^right_fact_id: (\S+)", user, re.M)
        return {"pairs": [
            {"left_fact_id": lf, "right_fact_id": rt, "relation": DIFFERS_BY,
             "axis": "측정조건", "reason": "저장 조건과 환경 조건"}
            for lf, rt in zip(lefts, rights)
        ]}


def _pairs(n: int):
    """후보 쌍 ``n`` 건(기준 fact n개 × 대상 fact 1개)."""
    store = _store()
    for i in range(1, n):
        store.reference.facts.facts.append(
            _fact(f"fact-row-{20 + i}", f"{i + 1}개월저장온도"))
    pairs = candidate_pairs(store, embedder=_FakeEmbedder())
    assert len(pairs) == n  # 전제가 깨지면 아래 계산이 전부 무의미하다
    return pairs


def test_f7_splits_the_batch_when_output_is_truncated():
    """4쌍이 잘리면 2+2 로 갈라 다시 부른다 — 한 쌍도 잃지 않는다."""
    runner = _TruncatingConceptRunner(limit=4)
    edges, _ = judge_pairs(runner, _pairs(4), batch_size=4)

    assert runner.calls == 3                      # 실패 1 + 조각 2
    assert len(edges) == 4
    assert all(e.relation == DIFFERS_BY for e in edges)


def test_f7_unsplittable_pair_is_the_only_one_left_unknown():
    """더 못 쪼개는 쌍만 보류하고 **형제 조각은 계속 간다**.

    오늘은 그 한 쌍 때문에 배치 전체가 unknown 이 된다. 사유도 갈라야 한다 —
    "응답에 이 쌍이 없었습니다"는 절단에 대해서는 거짓말이다.
    """
    runner = _TruncatingConceptRunner(limit=99, always="fact-row-21")
    edges, _ = judge_pairs(runner, _pairs(4), batch_size=4)

    unknown = [e for e in edges if e.relation == UNKNOWN]
    assert len(edges) == 4 and len(unknown) == 1
    assert "절단" in unknown[0].reason


def test_f7_split_is_counted():
    """분할은 캐시 지문에 안 들어가므로 **계측이 유일한 증거**다."""
    runner = _TruncatingConceptRunner(limit=4)
    stats: dict = {}
    judge_pairs(runner, _pairs(4), batch_size=4, stats=stats)

    assert stats["batches_split"] == 1
    assert stats["max_split_depth"] == 1
    assert stats["min_items_used"] == 2


def test_f7_without_truncation_is_unchanged():
    """분할이 없으면 오늘과 같다 — 계측 키도 안 생긴다."""
    runner = _TruncatingConceptRunner(limit=99)
    stats: dict = {}
    edges, _ = judge_pairs(runner, _pairs(4), batch_size=2, stats=stats)

    assert runner.calls == 2 and len(edges) == 4
    assert "batches_split" not in stats and "max_split_depth" not in stats


def test_f7_split_reaches_graph_stats():
    """설정에는 있는데 호출 경로에는 없는 결함을 이 저장소는 두 번 겪었다."""
    store = _store()
    for i in range(1, 4):
        store.reference.facts.facts.append(
            _fact(f"fact-row-{20 + i}", f"{i + 1}개월저장온도"))
    graph = build_concept_graph(store, embedder=_FakeEmbedder(),
                                runner=_TruncatingConceptRunner(limit=4),
                                batch_size=4)
    assert graph.stats["batches_split"] == 1


def test_f7_reports_batch_progress():
    """F7 배치마다 진척을 알린다 — 분할이 일어나도 원래 배치 번호로만 센다."""
    from contentcompare import progress as prog

    mem = prog.MemoryProgress()
    prog.set_reporter(mem)
    try:
        prog.unit_start("concept")
        judge_pairs(_TruncatingConceptRunner(limit=99), _pairs(3), batch_size=1)
    finally:
        prog.reset_reporter()
    steps = [(e["done"], e["total"]) for e in mem.events if e["ev"] == "step"]
    assert steps == [(0, 3), (1, 3), (2, 3), (3, 3)]
    assert all(e["key"] == "concept" for e in mem.events if e["ev"] == "step")
