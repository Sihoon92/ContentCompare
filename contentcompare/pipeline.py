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
from .llm import build_clients
from .models import ComparisonResult, DocItem
from .readers import get_reader
from .similarity import VectorIndex, chunk_items

logger = logging.getLogger(__name__)

ProgressFn = Callable[[int, int, ComparisonResult], None]


class ComparePipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.llm, self.embedder = build_clients(config)
        self.comparator = Comparator(self.llm)

    # ------------------------------------------------------------------ #
    def run(
        self,
        reference_path: str,
        target_paths: list[str],
        *,
        progress: Optional[ProgressFn] = None,
    ) -> list[ComparisonResult]:
        # 1) 문서 읽기
        reference_items = self._read(reference_path)
        logger.info("기준 항목 %d개 추출: %s", len(reference_items), reference_path)

        target_items: list[DocItem] = []
        for path in target_paths:
            items = self._read(path)
            logger.info("대상 항목 %d개 추출: %s", len(items), path)
            target_items.extend(items)

        # 2) 임베딩 인덱스 구축 (청킹 후)
        chunks = chunk_items(target_items, self.config.similarity.chunk_chars)
        index = VectorIndex(self.embedder)
        index.add(chunks)
        logger.info("임베딩 인덱스 구축 완료: 벡터 %d개", len(index))

        # 3~4) 기준 항목 순차 비교
        results: list[ComparisonResult] = []
        total = len(reference_items)
        for i, ref in enumerate(reference_items, start=1):
            if ref.is_empty():
                continue
            candidates = index.search(
                ref.text,
                top_k=self.config.similarity.top_k,
                min_score=self.config.similarity.min_score,
            )
            result = self.comparator.compare(ref, candidates)
            results.append(result)
            if progress:
                progress(i, total, result)
        return results

    # ------------------------------------------------------------------ #
    def _read(self, path: str) -> list[DocItem]:
        reader = get_reader(path, self.config)
        return reader.read(path)
