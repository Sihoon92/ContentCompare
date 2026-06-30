"""Fact 비교 파이프라인 — 신규 엔진(현행 ComparePipeline 과 별개).

설계는 ``docs/FACT_PIPELINE_PLAN.md`` 참고. 현재 구현 범위(F0~F2):

    Raw Extractor → Raw Compactor → artifacts 저장        (F0)
    Document Profiler → Schema Inducer(Excel)             (F1, LLM)
    Record Normalizer(Excel)                              (F2, LLM)
    Fact Extractor → ... → Comparator                    (F3~F6, 미구현)

F3 이후 LLM 단계는 :meth:`FactPipeline.run` 에서 :class:`NotImplementedError` 로
명시적으로 막는다(상위 호출부가 잡아 안내).

테스트 용이성을 위해 추출기/압축기/chat 을 주입할 수 있다(기본은 COM 기반
``raw.extract_raw`` 와 ``llm.factory.build_clients`` 의 chat).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional

from ..config import AppConfig, FactConfig
from ..raw import compact_raw, extract_raw
from ..readers import close_all_office
from .artifacts import ArtifactStore
from .llm_stage import LlmRunner
from .profiler import profile_document
from .record_normalizer import normalize_records
from .schema_inducer import induce_schema

logger = logging.getLogger(__name__)


class FactPipeline:
    """fact 기반 비교 엔진. 현재 F0(raw/compact)+F1(profile/schema)+F2(records)까지 동작한다."""

    def __init__(
        self,
        config: AppConfig,
        *,
        extractor: Optional[Callable[[str], Any]] = None,
        compactor: Optional[Callable[[Any], dict]] = None,
        chat: Any = None,
    ) -> None:
        self.config = config
        self.fact: FactConfig = getattr(config, "fact", FactConfig())
        # 테스트는 COM/네트워크를 피하려고 가짜 추출기/압축기/chat 을 주입한다.
        self._extract = extractor or extract_raw
        self._compact = compactor or compact_raw
        self._chat = chat  # None 이면 첫 사용 시 지연 생성

    def _chat_client(self) -> Any:
        if self._chat is None:
            from ..llm.factory import build_clients

            self._chat, _ = build_clients(self.config)  # chat 만 사용
        return self._chat

    def run(
        self,
        reference: str,
        targets: list[str],
        progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> list[dict]:
        """모든 문서를 raw→compact→profile→schema→records(Excel) 로 처리해 artifacts 에 저장.

        F3 이후 단계가 없어 :class:`NotImplementedError` 를 던진다. ``finally`` 에서
        열린 COM 문서를 정리한다.
        """
        docs = [reference, *targets]
        summaries: list[dict] = []
        try:
            for i, path in enumerate(docs, start=1):
                summaries.append(self._process_one(path))
                if progress:
                    progress(i, len(docs), path)
            # --- F3 이후 단계(Fact Extractor~Comparator)는 아직 없음 ---
            self._not_yet_implemented()
            return summaries
        finally:
            close_all_office()

    def _process_one(self, path: str) -> dict:
        """문서 1개: F0(raw/compact 저장) → F1(profile, Excel 은 schema)."""
        store = ArtifactStore(
            self.fact.artifacts_dir,
            os.path.basename(path),
            enabled=self.fact.save_artifacts,
            cache=self.fact.cache,
        )
        # F0: raw → physical_raw, compact → compact_raw
        raw_obj = self._extract(path)
        store.save("physical_raw", raw_obj.to_dict())
        compact = self._compact(raw_obj)
        store.save("compact_raw", compact)

        # F1: Document Profiler (+ Excel 은 Schema Inducer)
        runner = LlmRunner(
            self._chat_client(), max_calls=self.fact.max_llm_calls_per_doc
        )
        profile = profile_document(compact, runner, store)
        stages = ["physical_raw", "compact_raw", "document_profile"]
        if compact.get("doc_type") == "excel":
            tp, cs = induce_schema(compact, profile, runner, store)
            stages += ["table_profile", "column_schema"]
            normalize_records(
                compact, tp, cs, runner,
                batch_rows=self.fact.record_batch_rows, store=store,
            )
            stages += ["records"]

        logger.info("[Fact] %s: %s (LLM %d회)", os.path.basename(path), stages, runner.calls)
        return {
            "path": path,
            "doc_type": compact.get("doc_type"),
            "artifacts_dir": str(store.dir),
            "stages": stages,
            "llm_calls": runner.calls,
        }

    @staticmethod
    def _not_yet_implemented() -> None:
        raise NotImplementedError(
            "FactPipeline: Fact Extractor~Comparator 는 Phase F3~F6 에서 구현됩니다. "
            "현재(F0~F2)는 raw/compact/profile/schema/records artifacts 저장까지 동작합니다."
        )
