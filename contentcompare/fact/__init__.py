"""Fact 기반 비교 엔진(신규) — 현행 RAG(``pipeline.py``)와 별개·공존.

설계: ``docs/FACT_PIPELINE_PLAN.md`` / 상세: ``docs/FACT_F0_DESIGN.md``.

핵심 발상: 파일을 LLM 에 바로 주지 않고, 코드가 만든 raw json 을 LLM 이 구조화해
모든 문서를 공통 fact schema 로 정규화한 뒤 fact↔fact 로 정합성을 검증한다. 현재는
Phase F0(기반 정비)까지 구현되어 있다.
"""

from __future__ import annotations

from .artifacts import ArtifactStore
from .engine import make_pipeline
from .llm_stage import LlmBudgetExceeded, LlmRunner
from .pipeline import FactPipeline
from .profiler import profile_document
from .schema_inducer import induce_schema
from .schema_models import ColumnSchema, DocumentProfile, TableProfile

__all__ = [
    "ArtifactStore",
    "FactPipeline",
    "make_pipeline",
    "LlmRunner",
    "LlmBudgetExceeded",
    "profile_document",
    "induce_schema",
    "DocumentProfile",
    "TableProfile",
    "ColumnSchema",
]
