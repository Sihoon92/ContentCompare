"""F7 파이프라인 배선 테스트 — 가짜 추출기/chat/임베더(COM·네트워크 불필요).

기존 tests/test_fact_pipeline_smoke.py 의 주입 패턴을 따른다.
"""

import json

from contentcompare.config import AppConfig, FactConfig
from contentcompare.fact.concept_models import DIFFERS_BY
from contentcompare.fact.fact_models import Fact, FactSet
from contentcompare.fact.fact_store import DocFacts, FactStore
from contentcompare.fact.pipeline import FactPipeline


class _ScriptedChat:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        return self.responses.pop(0) if self.responses else "{}"


class _FakeEmbedder:
    def embed(self, texts, kind="passage"):
        return [[1.0, 0.9] if "온도" in t else [1.0, 0.0] for t in texts]


def _fact(fact_id, name, evidence) -> Fact:
    return Fact(fact_id=fact_id, entity_name=name, search_text=name, evidence_text=evidence)


def _store() -> FactStore:
    store = FactStore()
    store.add(DocFacts(doc_name="기준.xlsx", facts=FactSet(facts=[
        _fact("fact-row-20", "1개월저장온도", "-10.0, 35.0, 80.0")])), is_reference=True)
    store.add(DocFacts(doc_name="규격서.docx", facts=FactSet(facts=[
        _fact("fact-word-11", "표준환경온도", "표준환경온도, 21 ~ 29, ℃")])))
    return store


def _config(tmp_path, **fact_kw) -> AppConfig:
    cfg = AppConfig()
    cfg.fact = FactConfig(artifacts_dir=str(tmp_path / "artifacts"), **fact_kw)
    return cfg


def _reply() -> str:
    return json.dumps({"pairs": [{
        "left_fact_id": "fact-row-20", "right_fact_id": "fact-word-11",
        "relation": DIFFERS_BY, "axis": "측정조건", "reason": "저장 조건과 환경 조건",
    }]}, ensure_ascii=False)


def test_concept_graph_prevents_comparison_of_different_concepts(tmp_path):
    """F7 의 존재 이유 — 비교 대상이 아닌 쌍은 mismatch 가 아니라 missing 이다."""
    cfg = _config(tmp_path, ontology_path=str(tmp_path / "없음.yaml"))
    pipe = FactPipeline(cfg, chat=_ScriptedChat([_reply()]), embedder=_FakeEmbedder())
    result = pipe._compare_from_store(_store(), "기준.xlsx", ["규격서.docx"])
    assert [c.result for c in result.comparisons] == ["missing"]


def test_concept_graph_artifact_is_saved(tmp_path):
    cfg = _config(tmp_path, ontology_path=str(tmp_path / "없음.yaml"))
    pipe = FactPipeline(cfg, chat=_ScriptedChat([_reply()]), embedder=_FakeEmbedder())
    pipe._compare_from_store(_store(), "기준.xlsx", ["규격서.docx"])
    saved = json.loads((tmp_path / "artifacts" / "기준_xlsx" / "concept_graph.json").read_text(encoding="utf-8"))
    assert saved["edges"][0]["relation"] == DIFFERS_BY
    assert (tmp_path / "artifacts" / "기준_xlsx" / "concept_validation.json").exists()


def test_promoted_ontology_skips_llm(tmp_path):
    onto = tmp_path / "o.yaml"
    onto.write_text('differs_by:\n  - names: ["1개월저장온도", "표준환경온도"]\n    axis: "측정조건"\n',
                    encoding="utf-8")
    cfg = _config(tmp_path, ontology_path=str(onto))
    chat = _ScriptedChat([])
    pipe = FactPipeline(cfg, chat=chat, embedder=_FakeEmbedder())
    result = pipe._compare_from_store(_store(), "기준.xlsx", ["규격서.docx"])
    assert chat.calls == 0
    assert [c.result for c in result.comparisons] == ["missing"]


def test_use_concept_graph_false_falls_back_to_similarity(tmp_path):
    """롤백 스위치 — 기존 유사도 매칭 경로가 그대로 동작한다."""
    cfg = _config(tmp_path, use_concept_graph=False, compare_use_llm=False,
                  match_min_score=0.5)
    pipe = FactPipeline(cfg, chat=_ScriptedChat([]), embedder=_FakeEmbedder())
    result = pipe._compare_from_store(_store(), "기준.xlsx", ["규격서.docx"])
    assert result.comparisons and result.comparisons[0].match_method == "embed"


def test_missing_reason_points_at_the_concept_not_the_threshold(tmp_path):
    """개념 경로의 ``missing`` 사유가 '유사도 임계 미달'이라고 말하면 안 된다.

    라이브에서 ``1개월저장온도`` 가 ``missing`` 이 된 실제 이유는 임계값이 아니라
    개념 차단이었다. 문구가 틀리면 사용자는 F7 에서 쓰이지도 않는
    ``match_min_score`` 를 조정하러 간다.
    """
    cfg = _config(tmp_path, ontology_path=str(tmp_path / "없음.yaml"))
    pipe = FactPipeline(cfg, chat=_ScriptedChat([_reply()]), embedder=_FakeEmbedder())
    result = pipe._compare_from_store(_store(), "기준.xlsx", ["규격서.docx"])
    reason = result.comparisons[0].reason
    assert "유사도 임계" not in reason
    assert "개념" in reason
    assert "측정조건" in reason  # 차단한 differs_by 의 축이 실린다


def test_rollback_path_keeps_the_similarity_wording(tmp_path):
    """``use_concept_graph: false`` 는 예전 문구를 그대로 유지한다(그 경로에선 사실이다)."""
    cfg = _config(tmp_path, use_concept_graph=False, compare_use_llm=False,
                  match_min_score=0.99)
    # 대상 이름에 '온도'가 없어야 _FakeEmbedder 가 낮은 코사인을 준다(→ 임계 미달).
    store = FactStore()
    store.add(DocFacts(doc_name="기준.xlsx", facts=FactSet(facts=[
        _fact("fact-row-20", "1개월저장온도", "-10.0, 35.0, 80.0")])), is_reference=True)
    store.add(DocFacts(doc_name="규격서.docx", facts=FactSet(facts=[
        _fact("fact-word-3", "정격전압", "정격전압, 4.55, V")])))
    pipe = FactPipeline(cfg, chat=_ScriptedChat([]), embedder=_FakeEmbedder())
    result = pipe._compare_from_store(store, "기준.xlsx", ["규격서.docx"])
    assert result.comparisons[0].result == "missing"
    assert "유사도 임계 미달" in result.comparisons[0].reason


def test_compare_stats_include_concept_counters(tmp_path):
    cfg = _config(tmp_path, ontology_path=str(tmp_path / "없음.yaml"))
    pipe = FactPipeline(cfg, chat=_ScriptedChat([_reply()]), embedder=_FakeEmbedder())
    result = pipe._compare_from_store(_store(), "기준.xlsx", ["규격서.docx"])
    assert result.compare_stats["concept"]["differs_by"] == 1
