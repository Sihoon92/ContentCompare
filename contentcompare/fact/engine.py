"""엔진 선택 팩토리 — 엔진 이름으로 비교 파이프라인을 만든다.

현행 RAG(``ComparePipeline``)와 신규 fact(``FactPipeline``)를 한 곳에서 고른다.
CLI ``--engine`` 분기 로직을 순수 함수로 분리해 테스트 가능하게 한다(결정 #1).
RAG 파이프라인은 **호출만** 하며, 코드를 변경하지 않는다.
"""

from __future__ import annotations

from ..config import AppConfig

VALID_ENGINES = ("rag", "fact")


def make_pipeline(config: AppConfig, engine: str = "rag"):
    """engine 이름 → 파이프라인 인스턴스.

    - ``rag``(기본): 현행 :class:`~contentcompare.pipeline.ComparePipeline`.
    - ``fact``: 신규 :class:`~contentcompare.fact.pipeline.FactPipeline`.
    """
    if engine == "rag":
        from ..pipeline import ComparePipeline  # 현행(무수정)

        return ComparePipeline(config)
    if engine == "fact":
        from .pipeline import FactPipeline

        return FactPipeline(config)
    raise ValueError(f"알 수 없는 engine: {engine!r} (사용 가능: {', '.join(VALID_ENGINES)})")
