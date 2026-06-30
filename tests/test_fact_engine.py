"""엔진 선택 테스트 — CLI 기본 rag, make_pipeline 팩토리 분기.

ComparePipeline/FactPipeline 인스턴스 생성만 확인하며 실제 실행(COM/네트워크)은 하지 않는다.
"""

from __future__ import annotations

import pytest

from contentcompare.cli import build_parser
from contentcompare.config import AppConfig
from contentcompare.fact.engine import make_pipeline
from contentcompare.fact.pipeline import FactPipeline
from contentcompare.pipeline import ComparePipeline


def test_default_engine_is_rag():
    args = build_parser().parse_args(["--reference", "a.xlsx", "--targets", "b.docx"])
    assert args.engine == "rag"


def test_engine_flag_parsed():
    args = build_parser().parse_args(
        ["--engine", "fact", "--reference", "a.xlsx", "--targets", "b.docx"]
    )
    assert args.engine == "fact"


def test_make_pipeline_rag():
    assert isinstance(make_pipeline(AppConfig(), "rag"), ComparePipeline)


def test_make_pipeline_default_is_rag():
    assert isinstance(make_pipeline(AppConfig()), ComparePipeline)


def test_make_pipeline_fact():
    assert isinstance(make_pipeline(AppConfig(), "fact"), FactPipeline)


def test_make_pipeline_unknown_raises():
    with pytest.raises(ValueError):
        make_pipeline(AppConfig(), "bogus")
