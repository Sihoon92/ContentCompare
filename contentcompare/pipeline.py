"""전체 비교 파이프라인 오케스트레이션.

기준 문서 1개 + 대상 문서 N개를 받아:
1) 리더로 DocItem 추출
2) 대상 항목들을 청킹 후 임베딩 인덱스 구축
3) 기준 항목마다 top-k 유사 후보 검색
4) 후보들을 LLM 에 투입해 비교 판정
5) ComparisonResult 리스트 반환
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from .comparison import Comparator
from .config import AppConfig
from .knowledge import load_knowledge
from .llm import build_clients
from .models import ComparisonResult, DocItem, RecordItem, RecordResult
from .readers import close_all_office, get_reader
from .similarity import CachedEmbedder, HybridIndex, chunk_items

logger = logging.getLogger(__name__)

CompareResult = ComparisonResult | RecordResult
ProgressFn = Callable[[int, int, CompareResult], None]


class ComparePipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.llm, self.embedder = build_clients(config)
        # 사람이 작성한 도메인 지식을 비교 프롬프트에 항상 주입(요청 5번).
        knowledge = ""
        kcfg = config.knowledge
        if kcfg.enabled:
            knowledge = load_knowledge(kcfg.dir, max_chars=kcfg.max_chars)
            if knowledge:
                logger.info("도메인 지식 주입 활성화: %s (%d자)", kcfg.dir, len(knowledge))
        self.comparator = Comparator(self.llm, knowledge=knowledge)

    # ------------------------------------------------------------------ #
    def run(
        self,
        reference_path: str,
        target_paths: list[str],
        *,
        progress: Optional[ProgressFn] = None,
    ) -> list[CompareResult]:
        try:
            return self._run(reference_path, target_paths, progress=progress)
        finally:
            # 오류/정상 종료 어느 경우든, 열린 채 남은 Office 문서를 완전히 종료한다.
            # (각 리더가 자체 finally 로 닫지만, 예기치 못한 경로를 대비한 안전망.)
            close_all_office()

    def _run(
        self,
        reference_path: str,
        target_paths: list[str],
        *,
        progress: Optional[ProgressFn] = None,
    ) -> list[CompareResult]:
        # 1) 문서 읽기
        reference_items = self._read(reference_path)
        logger.info("기준 항목 %d개 추출: %s", len(reference_items), reference_path)

        target_items: list[DocItem] = []
        for path in target_paths:
            items = self._read(path)
            logger.info("대상 항목 %d개 추출: %s", len(items), path)
            target_items.extend(items)

        # 2) 하이브리드 인덱스 구축 (청킹 후, 임베딩 캐시 적용)
        sim = self.config.similarity
        chunks = chunk_items(target_items, sim.chunk_chars)
        embedder = CachedEmbedder(
            self.embedder, sim.cache_dir, model_name=self.config.llm.embed_model
        )
        index = HybridIndex(
            embedder,
            fusion=sim.fusion,
            rrf_k=sim.rrf_k,
            mmr_lambda=sim.mmr_lambda,
            per_doc_cap=sim.per_doc_cap,
            min_score=sim.min_score,
        )
        index.add(chunks)
        logger.info("하이브리드 인덱스 구축 완료: 벡터 %d개", len(index))

        # 3~4) 기준 항목 순차 비교
        results: list[CompareResult] = []
        total = len(reference_items)
        for i, ref in enumerate(reference_items, start=1):
            if ref.is_empty():
                continue
            candidates = index.search(
                ref.text,
                recall_k=sim.recall_k,
                top_k=sim.top_k,
            )
            # 엑셀 hybrid/field: 필드를 가진 RecordItem 이면 필드별 판정.
            if isinstance(ref, RecordItem) and ref.fields:
                result: CompareResult = self.comparator.compare_record(ref, candidates)
            else:
                result = self.comparator.compare(ref, candidates)
            results.append(result)
            if progress:
                progress(i, total, result)
        return results

    # ------------------------------------------------------------------ #
    def _read(self, path: str) -> list[DocItem]:
        reader = get_reader(path, self.config, llm=self.llm)
        return reader.read(path)
