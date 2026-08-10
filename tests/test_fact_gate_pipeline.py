"""Acceptance Gate 파이프라인 배선 — 가짜 chat/임베더(COM·네트워크 불필요).

가장 중요한 계약은 **shadow 기본값에서 아무것도 변하지 않는다**는 것이다.
판정도, LLM 호출 수도 게이트 도입 전과 같아야 한다.
"""

from __future__ import annotations

import json

import pytest

from contentcompare.config import AppConfig, FactConfig, FastPathConfig
from contentcompare.fact.fact_models import Fact, FactSet
from contentcompare.fact.fact_store import DocFacts, FactStore
from contentcompare.fact.pipeline import FactPipeline
from contentcompare.fact.record_models import Attribute
from contentcompare.fact.review_router import PARTIAL_ATTRIBUTE_COVERAGE


class _ScriptedChat:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        return self.responses.pop(0) if self.responses else "{}"


class _FakeEmbedder:
    def embed(self, texts, kind="passage"):
        return [[1.0, 0.0] for _ in texts]


def _fact(fact_id: str, name: str, **attrs) -> Fact:
    return Fact(
        fact_id=fact_id,
        entity_name=name,
        search_text=name,
        evidence_text=f"{name} 근거",
        source={"block_id": "b01"},
        attributes={k: Attribute(v, "V") for k, v in attrs.items()},
    )


def _store() -> FactStore:
    """기준 2속성 · 대상 1속성 → 코드는 match, 커버리지는 0.5(= unsafe match)."""
    store = FactStore()
    store.add(DocFacts(doc_name="기준.xlsx", facts=FactSet(facts=[
        _fact("fact-row-1", "공칭전압", nominal="3.85", upper="4.55")])), is_reference=True)
    store.add(DocFacts(doc_name="규격서.docx", facts=FactSet(facts=[
        _fact("fact-word-1", "공칭전압", nominal="3.85")])))
    return store


def _config(tmp_path, **fact_kw) -> AppConfig:
    """양쪽 entity_name 이 같으므로 concept_builder 가 LLM 없이 same_as 를 만든다.

    ``concept_builder`` 의 exact 경로가 ``decided_by=BY_CODE`` 로 확정하고,
    ``ConceptMatcher`` 는 그것을 ``needs_review=False`` 로 본다 — 그래서 이
    테스트의 unsafe 사유는 커버리지 하나뿐이다. 온톨로지는 실행 환경의
    ``knowledge/ontology.yaml`` 이 끼어들지 않도록 없는 경로를 준다
    (``tests/test_fact_pipeline_concept.py`` 와 같은 방식).
    """
    cfg = AppConfig()
    cfg.fact = FactConfig(
        artifacts_dir=str(tmp_path / "artifacts"),
        ontology_path=str(tmp_path / "없음.yaml"),
        **fact_kw,
    )
    return cfg


def _run(tmp_path, chat, **fact_kw):
    pipe = FactPipeline(_config(tmp_path, **fact_kw), chat=chat, embedder=_FakeEmbedder())
    return pipe._compare_from_store(_store(), "기준.xlsx", ["규격서.docx"])


# --------------------------------------------------------------------------- #
# shadow — 도입 전과 동일해야 한다
# --------------------------------------------------------------------------- #
def test_shadow_keeps_the_code_verdict_and_calls_no_extra_llm(tmp_path):
    chat = _ScriptedChat()
    result = _run(tmp_path, chat)
    (c,) = result.comparisons
    assert c.result == "match"          # 판정 무변경
    assert c.decided_by == "code"       # LLM 으로 강등되지 않았다
    assert chat.calls == 0


def test_shadow_still_records_the_gate_verdict(tmp_path):
    """분기는 안 바꾸되 사유는 남긴다 — 그래야 enforce 비용을 예측할 수 있다."""
    result = _run(tmp_path, _ScriptedChat())
    (c,) = result.comparisons
    assert c.initial_result == "match"
    assert c.review_triggers == [PARTIAL_ATTRIBUTE_COVERAGE]
    assert c.safe_to_finalize is False
    assert c.attribute_coverage == pytest.approx(0.5)


def test_stats_expose_unsafe_match_rate(tmp_path):
    result = _run(tmp_path, _ScriptedChat())
    assert result.compare_stats["unsafe_match_rate"] == pytest.approx(1.0)
    assert result.compare_stats["fast_path_rate"] == pytest.approx(0.0)
    assert result.compare_stats["review_reasons"] == {PARTIAL_ATTRIBUTE_COVERAGE: 1}
    # 기존 키가 사라지면 리포트가 깨진다
    for key in ("comparisons", "decided_by_llm", "llm_calls", "llm_failures", "concept"):
        assert key in result.compare_stats


def test_artifact_carries_history_fields(tmp_path):
    _run(tmp_path, _ScriptedChat())
    saved = json.loads(
        (tmp_path / "artifacts" / "기준_xlsx" / "comparison_result.json")
        .read_text(encoding="utf-8")
    )
    row = saved["comparisons"][0]
    assert row["initial_result"] == "match"
    assert row["review_triggers"] == [PARTIAL_ATTRIBUTE_COVERAGE]
    assert row["safe_to_finalize"] is False
    assert row["result"] == "match"          # 기존 키 보존


# --------------------------------------------------------------------------- #
# disabled / enforce
# --------------------------------------------------------------------------- #
def test_disabled_gate_omits_stats(tmp_path):
    """0 으로 채우면 '게이트가 아무것도 안 잡았다'로 오독된다 — 아예 넣지 않는다."""
    result = _run(tmp_path, _ScriptedChat(), fast_path=FastPathConfig(enabled=False))
    (c,) = result.comparisons
    assert c.review_triggers == []
    assert "unsafe_match_rate" not in result.compare_stats


def test_enforce_demotes_the_unsafe_match_to_llm(tmp_path):
    chat = _ScriptedChat([json.dumps(
        {"result": "mismatch", "reason": "상한값이 대상에 없습니다"}, ensure_ascii=False
    )])
    result = _run(tmp_path, chat, fast_path=FastPathConfig(enforce=True))
    (c,) = result.comparisons
    assert chat.calls == 1
    assert c.initial_result == "match" and c.result == "mismatch"
    assert c.result_changed is True
    assert result.compare_stats["result_changed_count"] == 1
