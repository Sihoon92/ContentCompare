"""Fact 비교 파이프라인 — 신규 엔진(현행 ComparePipeline 과 별개).

설계는 ``docs/FACT_PIPELINE_PLAN.md`` 참고. 구현 범위(F0~F6, F4b 제외):

    Raw Extractor → Raw Compactor → artifacts 저장        (F0)
    Document Profiler → Schema Inducer(Excel)             (F1, LLM)
    Record Normalizer(Excel)                              (F2, LLM)
    Fact Extractor(Excel 코드 / Word·PPT LLM)            (F3)
    Rule Validator                                        (F4a, 코드)
    Fact Store → Matcher → Comparator                     (F5, 코드+LLM 하이브리드)
    Report                                                (F6)

F4b(LLM Repair Loop)는 아직 없다 — F3.5 실측에서 JSON 준수도가 양호해 우선순위가
낮고, 실패 분포를 보고 설계해야 사변이 되지 않는다.

문서 단위 처리는 **서로 격리**되어 하나가 실패해도 나머지가 계속되고(F3.5), 문서별
계측이 ``run_stats.json`` 에 남는다 — 오판·누락 추적이 이 설계의 핵심 가치다.

테스트 용이성을 위해 추출기/압축기/chat/embed 를 주입할 수 있다(기본은 COM 기반
``raw.extract_raw`` 와 ``llm.factory.build_clients``).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..config import AppConfig, FactConfig
from ..knowledge import load_knowledge
from ..llm.tracing import stage
from ..raw import compact_raw, extract_raw
from ..readers import close_all_office
from .artifacts import ArtifactStore
from .fact_comparator import MATCH, UNKNOWN, FactComparator, FactComparison
from .review_router import AcceptanceGate, gate_stats
from .fact_extractor import build_facts_by_block, extract_facts
from .fact_matcher import FactMatcher
from .fact_models import FactSet
from .fact_store import DocFacts, FactStore
from .llm_stage import LlmRunner
from .profiler import profile_document
from .record_normalizer import normalize_records
from .schema_inducer import induce_schema
from .schema_models import ColumnSchema
from .validator import validate_facts

logger = logging.getLogger(__name__)


@dataclass
class FactRunResult:
    """fact 엔진 1회 실행의 결과 전체."""

    summaries: list[dict] = field(default_factory=list)
    """문서별 처리 결과(status/stages/llm_calls/stats) — 실패 격리 보고용."""

    comparisons: list[FactComparison] = field(default_factory=list)
    markdown: str = ""
    compare_stats: dict = field(default_factory=dict)
    concept_graph: Any = None
    """F7 개념 그래프(사용 안 하면 None)."""

    @property
    def failed_docs(self) -> list[dict]:
        return [s for s in self.summaries if s.get("status") != "ok"]


class FactPipeline:
    """fact 기반 비교 엔진 — 문서를 fact 로 정규화한 뒤 fact 끼리 대조한다."""

    def __init__(
        self,
        config: AppConfig,
        *,
        extractor: Optional[Callable[[str], Any]] = None,
        compactor: Optional[Callable[[Any], dict]] = None,
        chat: Any = None,
        embedder: Any = None,
    ) -> None:
        self.config = config
        self.fact: FactConfig = getattr(config, "fact", FactConfig())
        # 테스트는 COM/네트워크를 피하려고 가짜 추출기/압축기/chat/embed 를 주입한다.
        self._extract = extractor or extract_raw
        self._compact = compactor or compact_raw
        self._chat = chat  # None 이면 첫 사용 시 지연 생성
        self._embedder = embedder
        self._clients_built = False

    def _build_clients(self) -> None:
        if self._clients_built:
            return
        if self._chat is None or self._embedder is None:
            from ..llm.factory import build_clients

            chat, embed = build_clients(self.config)
            self._chat = self._chat or chat
            self._embedder = self._embedder or self._wrap_cache(embed)
        self._clients_built = True

    def _wrap_cache(self, embed: Any) -> Any:
        """임베딩에 디스크 캐시를 씌운다(재실행 비용 0 — 현행 RAG 와 같은 장치)."""
        from ..similarity.cache import CachedEmbedder

        return CachedEmbedder(
            embed, self.config.similarity.cache_dir, self.config.llm.embed_model
        )

    def _chat_client(self) -> Any:
        self._build_clients()
        return self._chat

    def _embed_client(self) -> Any:
        self._build_clients()
        return self._embedder

    def run(
        self,
        reference: str,
        targets: list[str],
        progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> FactRunResult:
        """문서를 fact 로 정규화(F0~F3)·검증(F4a)한 뒤 fact 끼리 비교(F5)해 리포트(F6)를 만든다.

        문서 하나가 실패해도(COM 오류·LLM 예산 초과·JSON 파싱 실패) 나머지는 계속
        처리하고, 그 문서의 summary 에 ``status="error"`` 와 사유를 남긴다.
        ``finally`` 에서 열린 COM 문서를 정리한다.
        """
        docs = [reference, *targets]
        store = FactStore()
        result = FactRunResult()
        try:
            for i, path in enumerate(docs, start=1):
                summary = self._process_one_safe(path, store, is_reference=(i == 1))
                result.summaries.append(summary)
                if progress:
                    progress(i, len(docs), path)
            self._compare_and_report(store, reference, targets, result)
            return result
        finally:
            close_all_office()

    # ------------------------------------------------------------------ #
    # F5 + F6
    # ------------------------------------------------------------------ #
    def _compare_and_report(
        self,
        store: FactStore,
        reference: str,
        targets: list[str],
        result: FactRunResult,
    ) -> None:
        merged = self._compare_from_store(store, reference, targets)
        result.comparisons = merged.comparisons
        result.compare_stats = merged.compare_stats
        result.markdown = merged.markdown
        result.concept_graph = merged.concept_graph

    def _compare_from_store(
        self, store: FactStore, reference: str, targets: list[str]
    ) -> FactRunResult:
        """fact 가 모인 상태에서 개념 그래프 → 비교 → 리포트까지 한다."""
        result = FactRunResult()
        if not store.ready:
            logger.warning("[Fact] 비교 생략 — 기준/대상 fact 가 부족합니다: %s", store.summary())
            return result

        graph = self._build_graph(store)
        result.concept_graph = graph

        runner = LlmRunner(
            self._chat_client(), max_calls=self.fact.max_llm_calls_per_compare
        ) if self.fact.compare_use_llm else None
        comparator = FactComparator(
            runner=runner,
            knowledge=load_knowledge(),
            use_llm=self.fact.compare_use_llm,
        )
        ref_doc = store.reference
        assert ref_doc is not None  # store.ready 가 보장

        gate = AcceptanceGate(self.fact.fast_path)
        for target in store.targets:
            matcher = self._matcher_for(graph, ref_doc, target)
            # 후보가 없을 때의 사유는 매칭 전략이 안다(개념 경로 = '연결 없음').
            explain = getattr(matcher, "explain_missing", None)
            with stage(f"F5 값 대조 · {target.doc_name}"):
                for ref_fact in ref_doc.facts.facts:
                    candidates = matcher.search(ref_fact)
                    probe = comparator.compare_code(
                        ref_fact,
                        candidates,
                        target,
                        ref_low_confidence=ref_doc.is_low_confidence(ref_fact),
                        missing_reason=(
                            "" if candidates or explain is None else explain(ref_fact)
                        ),
                    )
                    reasons = gate.evaluate(probe)
                    # 게이트가 거부한 코드 match 만 강등한다. mismatch/unknown 은
                    # finalize 가 이미 LLM 으로 보내므로 여기서 또 밀 필요가 없다.
                    unsafe_match = bool(reasons) and probe.code_result == MATCH
                    comparison = comparator.finalize(
                        probe, force_llm=gate.enforce and unsafe_match
                    )
                    comparison.initial_result = probe.code_result or UNKNOWN
                    comparison.review_triggers = reasons
                    comparison.attribute_coverage = probe.attribute_coverage
                    result.comparisons.append(comparison)

        result.compare_stats = {
            "comparisons": len(result.comparisons),
            "decided_by_llm": sum(1 for c in result.comparisons if c.decided_by == "llm"),
            "llm_calls": comparator.llm_calls,
            "llm_failures": comparator.llm_failures,
            "concept": dict(graph.stats) if graph is not None else {},
            # 게이트가 꺼져 있으면 키를 아예 넣지 않는다 — 0 으로 채우면
            # "게이트가 아무것도 안 잡았다"로 오독된다.
            **(gate_stats(result.comparisons) if self.fact.fast_path.enabled else {}),
        }
        self._save_comparison(ref_doc, result)
        # 지연 import — report 패키지가 fact 결과 모델을 참조하므로 모듈 최상단에서
        # 서로를 부르면 순환 import 가 된다.
        from ..report.fact_report import render_fact_markdown

        result.markdown = render_fact_markdown(
            result.comparisons,
            reference_doc=reference,
            target_docs=targets,
            stats=result.compare_stats,
            graph=result.concept_graph,
        )
        logger.info("[Fact] 비교 %s", result.compare_stats)
        return result

    def _build_graph(self, store: FactStore):
        """F7 개념 그래프를 만들고 artifacts 에 남긴다(끄면 None)."""
        if not self.fact.use_concept_graph:
            return None
        from .concept_builder import build_concept_graph
        from .ontology import load_ontology
        from .validator import validate_graph

        runner = LlmRunner(
            self._chat_client(), max_calls=self.fact.max_llm_calls_per_concept
        )
        # 후보 쌍 진단은 **이번 실행의 값**이어야 하므로 캐시하지 않는다(run_stats 와 같다).
        pairs_out: Optional[dict] = {} if self.fact.save_candidate_pairs else None
        with stage("F7 개념 판정"):
            graph = build_concept_graph(
                store,
                embedder=self._embed_client(),
                runner=runner,
                ontology=load_ontology(self.fact.ontology_path),
                knowledge=load_knowledge(),
                top_k=self.fact.concept_recall_top_k,
                min_score=self.fact.concept_recall_min,
                batch_size=self.fact.concept_batch_pairs,
                pairs_out=pairs_out,
            )
        ref_doc = store.reference
        if ref_doc is not None:
            artifacts = ArtifactStore(
                self.fact.artifacts_dir, ref_doc.doc_name,
                enabled=self.fact.save_artifacts, cache=False,
            )
            artifacts.save("concept_graph", graph.to_dict())
            artifacts.save("concept_validation", validate_graph(graph).to_dict())
            if pairs_out:
                # 계측 저장 실패가 비교를 죽이면 안 된다(run_stats 와 같은 방어).
                try:
                    artifacts.save("candidate_pairs", pairs_out)
                except OSError:
                    logger.warning("[Fact] candidate_pairs 저장 실패")
        return graph

    def _matcher_for(self, graph, ref_doc: DocFacts, target: DocFacts):
        """개념 그래프가 있으면 그래프 조회, 없으면 기존 유사도 매칭."""
        if graph is not None:
            from .fact_matcher import ConceptMatcher

            return ConceptMatcher(graph, ref_doc.doc_name, target.doc_name,
                                  target.facts.facts)
        return FactMatcher(
            target.facts.facts,
            embedder=self._embed_client(),
            top_k=self.fact.match_top_k,
            min_score=self.fact.match_min_score,
            review_score=self.fact.match_review_score,
        )

    def _save_comparison(self, ref_doc: DocFacts, result: FactRunResult) -> None:
        """``comparison_result.json`` 저장 — 기준 문서의 artifacts 폴더에 남긴다(§7)."""
        store = ArtifactStore(
            self.fact.artifacts_dir, ref_doc.doc_name,
            enabled=self.fact.save_artifacts, cache=False,
        )
        store.save("comparison_result", {
            "reference": ref_doc.doc_name,
            "stats": result.compare_stats,
            "comparisons": [c.to_dict() for c in result.comparisons],
        })

    def _process_one_safe(
        self, path: str, store: FactStore, *, is_reference: bool = False
    ) -> dict:
        """문서 1개를 처리하되 예외를 격리해 summary 로 변환하고, fact 를 store 에 넣는다.

        실문서 라이브에서는 COM 추출 예외(``pywintypes.com_error``/``OSError``)가
        LLM 오류만큼 흔하므로 넓게 잡는다. traceback 은 로그 파일에 남긴다.
        """
        name = os.path.basename(path)
        # 실패해도 "어디까지 갔는지"를 보고해야 하므로 진행 상태를 밖에서 들고 있는다.
        stages: list[str] = []
        stats: dict[str, Any] = {}
        try:
            summary = self._process_one(path, stages, stats, store, is_reference)
            summary["status"] = "ok"
            logger.info("[Fact] ✅ %s (LLM %d회)", name, summary.get("llm_calls", 0))
            return summary
        except Exception as e:  # noqa: BLE001 — 문서 단위 격리가 목적
            logger.exception("[Fact] ❌ %s 처리 실패", name)
            return {
                "path": path,
                "status": "error",
                "error": f"{type(e).__name__}: {e}",
                "stages": stages,
                "llm_calls": stats.get("llm", {}).get("calls", 0),
                "stats": stats,
            }

    def _process_one(
        self,
        path: str,
        stages: list[str],
        stats: dict,
        fact_store: FactStore,
        is_reference: bool,
    ) -> dict:
        """문서 1개: F0(raw/compact) → F1(profile, Excel 은 schema) → F2(records) → F3(facts) → F4a(검증).

        ``stages``/``stats`` 는 호출자가 준 out-param 이다 — 중간에 실패해도 그때까지의
        진행 단계와 계측값을 잃지 않기 위해서다.
        """
        doc_label = os.path.basename(path)
        store = ArtifactStore(
            self.fact.artifacts_dir,
            doc_label,
            enabled=self.fact.save_artifacts,
            cache=self.fact.cache,
        )
        # F0: raw → physical_raw, compact → compact_raw
        raw_obj = self._extract(path)
        store.save("physical_raw", raw_obj.to_dict())
        stages.append("physical_raw")
        compact = self._compact(raw_obj)
        store.save("compact_raw", compact)
        stages.append("compact_raw")

        # F1: Document Profiler (+ Excel 은 Schema Inducer)
        runner = LlmRunner(
            self._chat_client(), max_calls=self.fact.max_llm_calls_per_doc
        )
        record_stats: dict[str, Any] = {}
        fact_stats: dict[str, Any] = {}
        column_schema: Optional[ColumnSchema] = None
        facts = FactSet()
        try:
            # ``stage(...)`` 는 Langfuse trace 에 붙일 단계 이름이다(추적이 꺼져 있으면
            # 컨텍스트 변수만 설정하는 사실상 무비용 연산). 이름이 없으면 trace 가
            # 한 덩어리로 뭉쳐 어느 단계의 프롬프트인지 알 수 없다.
            with stage(f"F1 document_profile · {doc_label}"):
                profile = profile_document(compact, runner, store)
            stages.append("document_profile")
            if compact.get("doc_type") == "excel":
                with stage(f"F1 column_schema · {doc_label}"):
                    tp, cs = induce_schema(compact, profile, runner, store)
                column_schema = cs
                stages += ["table_profile", "column_schema"]
                with stage(f"F2 records · {doc_label}"):
                    records = normalize_records(
                        compact, tp, cs, runner,
                        batch_rows=self.fact.record_batch_rows, store=store,
                        stats=record_stats,
                    )
                stages.append("records")
                # F3: records → facts (코드 결정적, 무 LLM)
                facts = extract_facts(compact, records=records, store=store, stats=fact_stats)
            else:
                # F3: Word/PPT 는 블록/도형 → facts 직행 (LLM)
                with stage(f"F3 facts · {doc_label}"):
                    facts = extract_facts(
                        compact, profile=profile, runner=runner,
                        store=store, batch_blocks=self.fact.fact_batch_blocks,
                        stats=fact_stats,
                    )
            stages.append("facts")

            # 블록 ↔ fact 매핑(진단). 추출 결과에서 역산하므로 캐시 히트에도 남는다.
            # physical_raw 를 함께 넘겨 **줄 단위** 커버리지까지 낸다 — 블록 단위만
            # 보면 한 문단에 조건이 넷인데 첫 줄만 인용해도 cited 로 보인다(§12.3).
            if self.fact.save_facts_by_block:
                try:
                    store.save("facts_by_block", build_facts_by_block(
                        compact, facts, fact_stats, lines_by_block=raw_obj.to_dict(),
                    ))
                except OSError:
                    logger.warning("[Fact] facts_by_block 저장 실패: %s", path)

            # F4a: 코드 검증(무 LLM). error 가 붙은 fact 는 버리지 않고 저신뢰로 표시해
            # F5 가 unknown 판정 근거로 쓴다.
            report = validate_facts(facts, compact, column_schema=column_schema)
            store.save("validation_report", report.to_dict())
            stages.append("validation_report")
            stats["validation"] = report.to_dict()["overall"]
            fact_store.add(
                DocFacts(
                    doc_name=os.path.basename(path),
                    doc_type=str(compact.get("doc_type") or ""),
                    facts=facts,
                    low_confidence_ids=report.low_confidence_ids,
                ),
                is_reference=is_reference,
            )
        finally:
            # 실패해도 "죽기 전까지의 계측"이 남아야 원인을 본다(F3.5). 계측 산출물은
            # 캐시하지 않는다 — 항상 이번 실행의 값이어야 한다.
            stats["llm"] = runner.stats()
            if record_stats:
                stats["records"] = record_stats
            if fact_stats:
                stats["facts"] = fact_stats
            try:
                store.save("run_stats", {"path": path, "stages": stages, **stats})
            except OSError:  # 계측 저장 실패가 본 예외를 가리지 않도록
                logger.warning("[Fact] run_stats 저장 실패: %s", path)

        logger.info("[Fact] %s: %s (LLM %d회)", os.path.basename(path), stages, runner.calls)
        return {
            "path": path,
            "doc_type": compact.get("doc_type"),
            "artifacts_dir": str(store.dir),
            "stages": stages,
            "llm_calls": runner.calls,
            "facts": len(facts.facts),
            "stats": stats,
        }
