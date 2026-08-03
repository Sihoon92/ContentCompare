"""Fact 비교 파이프라인 — 신규 엔진(현행 ComparePipeline 과 별개).

설계는 ``docs/FACT_PIPELINE_PLAN.md`` 참고. 현재 구현 범위(F0~F3):

    Raw Extractor → Raw Compactor → artifacts 저장        (F0)
    Document Profiler → Schema Inducer(Excel)             (F1, LLM)
    Record Normalizer(Excel)                              (F2, LLM)
    Fact Extractor(Excel 코드 / Word·PPT LLM)            (F3)
    Validator → Repair → Comparator                      (F4~F6, 미구현)

F4 이후 단계는 :meth:`FactPipeline.run` 에서 :class:`FactStagesIncomplete`
(=:class:`NotImplementedError`)로 명시적으로 막는다(상위 호출부가 잡아 안내).
문서 단위 처리는 **서로 격리**되어 하나가 실패해도 나머지가 계속된다(F3.5) —
라이브 검증은 "실패를 관찰하는 작업"이므로 격리와 계측이 전제다.

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
from .fact_extractor import extract_facts
from .llm_stage import LlmRunner
from .profiler import profile_document
from .record_normalizer import normalize_records
from .schema_inducer import induce_schema

logger = logging.getLogger(__name__)


class FactStagesIncomplete(NotImplementedError):
    """F4 이후 단계 미구현 경계. 여기까지의 문서별 ``summaries`` 를 실어 나른다.

    :class:`NotImplementedError` 하위라 기존 호출부(``cli.py``)의 예외 처리를
    그대로 유지하면서, 상위에서 문서별 성공/실패를 표로 보여줄 수 있게 한다.
    """

    def __init__(self, message: str, summaries: Optional[list[dict]] = None) -> None:
        super().__init__(message)
        self.summaries = summaries or []


class FactPipeline:
    """fact 기반 비교 엔진. 현재 F0(raw/compact)+F1(profile/schema)+F2(records)+F3(facts)까지 동작한다."""

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
        """모든 문서를 raw→compact→profile→schema/records(Excel)→facts 로 처리해 저장.

        문서 하나가 실패해도(COM 오류·LLM 예산 초과·JSON 파싱 실패) 나머지는 계속
        처리하고, 그 문서의 summary 에 ``status="error"`` 와 사유를 남긴다.
        F4 이후 단계가 없어 마지막에 :class:`FactStagesIncomplete` 를 던진다
        (``summaries`` 첨부). ``finally`` 에서 열린 COM 문서를 정리한다.
        """
        docs = [reference, *targets]
        summaries: list[dict] = []
        try:
            for i, path in enumerate(docs, start=1):
                summaries.append(self._process_one_safe(path))
                if progress:
                    progress(i, len(docs), path)
            # --- F4 이후 단계(Validator~Comparator)는 아직 없음 ---
            self._not_yet_implemented(summaries)
            return summaries
        finally:
            close_all_office()

    def _process_one_safe(self, path: str) -> dict:
        """문서 1개를 처리하되 예외를 격리해 summary 로 변환한다.

        실문서 라이브에서는 COM 추출 예외(``pywintypes.com_error``/``OSError``)가
        LLM 오류만큼 흔하므로 넓게 잡는다. traceback 은 로그 파일에 남긴다.
        """
        name = os.path.basename(path)
        # 실패해도 "어디까지 갔는지"를 보고해야 하므로 진행 상태를 밖에서 들고 있는다.
        stages: list[str] = []
        stats: dict[str, Any] = {}
        try:
            summary = self._process_one(path, stages, stats)
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

    def _process_one(self, path: str, stages: list[str], stats: dict) -> dict:
        """문서 1개: F0(raw/compact) → F1(profile, Excel 은 schema) → F2(Excel records) → F3(facts).

        ``stages``/``stats`` 는 호출자가 준 out-param 이다 — 중간에 실패해도 그때까지의
        진행 단계와 계측값을 잃지 않기 위해서다.
        """
        store = ArtifactStore(
            self.fact.artifacts_dir,
            os.path.basename(path),
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
        try:
            profile = profile_document(compact, runner, store)
            stages.append("document_profile")
            if compact.get("doc_type") == "excel":
                tp, cs = induce_schema(compact, profile, runner, store)
                stages += ["table_profile", "column_schema"]
                records = normalize_records(
                    compact, tp, cs, runner,
                    batch_rows=self.fact.record_batch_rows, store=store,
                    stats=record_stats,
                )
                stages.append("records")
                # F3: records → facts (코드 결정적, 무 LLM)
                extract_facts(compact, records=records, store=store, stats=fact_stats)
            else:
                # F3: Word/PPT 는 블록/도형 → facts 직행 (LLM)
                extract_facts(
                    compact, profile=profile, runner=runner,
                    store=store, batch_blocks=self.fact.fact_batch_blocks,
                    stats=fact_stats,
                )
            stages.append("facts")
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
            "stats": stats,
        }

    @staticmethod
    def _not_yet_implemented(summaries: list[dict]) -> None:
        raise FactStagesIncomplete(
            "FactPipeline: Validator~Comparator 는 Phase F4~F6 에서 구현됩니다. "
            "현재(F0~F3)는 raw/compact/profile/schema/records/facts artifacts 저장까지 동작합니다.",
            summaries,
        )
