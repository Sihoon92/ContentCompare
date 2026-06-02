"""LLMConfig.embed_prefix_for(kind) 접두어 해석 규칙 테스트."""

from __future__ import annotations

from contentcompare.config import LLMConfig


def test_prefix_for_uses_kind_specific_when_set():
    cfg = LLMConfig()
    cfg.embed_query_prefix = "query: "
    cfg.embed_passage_prefix = "passage: "
    assert cfg.embed_prefix_for("query") == "query: "
    assert cfg.embed_prefix_for("passage") == "passage: "


def test_prefix_for_falls_back_to_common():
    cfg = LLMConfig()
    cfg.embed_prefix = "common: "  # 전용값 미설정
    assert cfg.embed_prefix_for("query") == "common: "
    assert cfg.embed_prefix_for("passage") == "common: "


def test_prefix_for_empty_by_default():
    cfg = LLMConfig()
    assert cfg.embed_prefix_for("query") == ""
    assert cfg.embed_prefix_for("passage") == ""


def test_kind_specific_overrides_common():
    cfg = LLMConfig()
    cfg.embed_prefix = "common: "
    cfg.embed_query_prefix = "query: "  # query 만 전용값
    assert cfg.embed_prefix_for("query") == "query: "
    assert cfg.embed_prefix_for("passage") == "common: "  # passage 는 공통 폴백
