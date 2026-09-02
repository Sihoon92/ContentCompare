"""FastEmbed 임베딩 백엔드 + chat/embed 분리(혼합 구성) 테스트.

fastembed 미설치 환경에서도 모델 주입으로 검증한다.
"""

from __future__ import annotations

import pytest

from contentcompare.config import AppConfig, LLMConfig
from contentcompare.llm.factory import build_clients
from contentcompare.llm.fastembed_backend import FastEmbedBackend


class FakeModel:
    """fastembed TextEmbedding 흉내: embed(texts) → 벡터 제너레이터."""

    def __init__(self, dim=3):
        self.dim = dim

    def embed(self, texts):
        for t in texts:
            yield [float(len(t))] + [1.0] * (self.dim - 1)


class NpLike:
    """numpy 배열처럼 tolist() 를 갖는 벡터."""

    def __init__(self, data):
        self._data = data

    def tolist(self):
        return list(self._data)


def test_supported_names_handles_key_variants():
    from contentcompare.llm.fastembed_backend import _supported_names

    class Cls:
        @staticmethod
        def list_supported_models():
            return [{"model": "a"}, {"model_name": "b"}, {"other": "x"}]

    assert _supported_names(Cls) == ["a", "b"]


def test_fastembed_embed_returns_lists():
    be = FastEmbedBackend(LLMConfig(), model=FakeModel(dim=3))
    out = be.embed(["ab", "cde"])
    assert out == [[2.0, 1.0, 1.0], [3.0, 1.0, 1.0]]


def test_fastembed_converts_numpy_like():
    class NpModel:
        def embed(self, texts):
            return [NpLike([0.1, 0.2]) for _ in texts]

    be = FastEmbedBackend(LLMConfig(), model=NpModel())
    assert be.embed(["x"]) == [[0.1, 0.2]]


def test_factory_mixes_chat_and_fastembed():
    # chat=ollama, embed=fastembed → 서로 다른 객체.
    cfg = AppConfig()
    cfg.llm.backend = "ollama"
    cfg.llm.embed_backend = "fastembed"
    chat, embed = build_clients(cfg)
    assert chat is not embed
    assert isinstance(embed, FastEmbedBackend)


def test_factory_same_backend_returns_single_object():
    """같은 백엔드면 객체 하나 — 모델을 두 번 로드하지 않기 위한 불변식.

    관측 층(타임라인)을 끄고 본다. 켜져 있으면 chat 이 ``TracedChat`` 으로 감싸여
    ``is`` 가 깨지는데, 그것은 배선이 아니라 관측의 문제라 여기서 볼 대상이 아니다.
    """
    cfg = AppConfig()
    cfg.llm.backend = "ollama"          # embed_backend 비움 → 동일
    cfg.logging.timeline = False
    chat, embed = build_clients(cfg)
    assert chat is embed


def test_factory_langchain_chat_with_fastembed_embed():
    cfg = AppConfig()
    cfg.llm.backend = "langchain"
    cfg.llm.embed_backend = "fastembed"
    cfg.logging.timeline = False        # 배선만 본다(위 테스트와 같은 이유)
    cfg.llm.rate_limit_wait = 0  # 한도 래퍼도 끈다 — 켜면 RateLimitedChat 이 바깥에 붙어 이 검증을 가린다
    chat, embed = build_clients(cfg)
    from contentcompare.llm.langchain_backend import LangChainBackend

    assert isinstance(chat, LangChainBackend)
    assert isinstance(embed, FastEmbedBackend)


def test_factory_unknown_backend_raises():
    cfg = AppConfig()
    cfg.llm.backend = "nope"
    with pytest.raises(ValueError, match="알 수 없는"):
        build_clients(cfg)
