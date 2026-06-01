"""LangChain 백엔드 테스트 — chat/embeddings 객체 주입으로 langchain 미설치 환경에서도 검증."""

from __future__ import annotations

from contentcompare.config import AppConfig, LLMConfig
from contentcompare.llm.factory import build_clients
from contentcompare.llm.langchain_backend import LangChainBackend


class FakeMsg:
    def __init__(self, content):
        self.content = content


class FakeChat:
    """langchain ChatModel 흉내: bind() 후 invoke() 가 content 객체를 반환."""

    def __init__(self, reply="응답"):
        self.reply = reply
        self.bound = None
        self.last_messages = None

    def bind(self, **kwargs):
        self.bound = kwargs
        return self

    def invoke(self, messages):
        self.last_messages = messages
        return FakeMsg(self.reply)


class FakeEmb:
    def embed_documents(self, texts):
        return [[float(len(t)), 1.0] for t in texts]


def _cfg():
    cfg = LLMConfig(backend="langchain", chat_model="gemma", embed_model="bge")
    cfg.internal.base_url = "https://api/v1"
    cfg.internal.api_key = "sk-test"
    return cfg


def test_complete_uses_role_tuples_and_returns_content():
    chat = FakeChat(reply="OK")
    be = LangChainBackend(_cfg(), chat=chat, embeddings=FakeEmb())
    out = be.complete("시스템", "유저", temperature=0.3)
    assert out == "OK"
    # (role, content) 튜플로 전달되는지
    assert chat.last_messages == [("system", "시스템"), ("human", "유저")]
    assert chat.bound == {"temperature": 0.3}


def test_embed_returns_vectors():
    be = LangChainBackend(_cfg(), chat=FakeChat(), embeddings=FakeEmb())
    vecs = be.embed(["ab", "cde"])
    assert vecs == [[2.0, 1.0], [3.0, 1.0]]


def test_api_key_falls_back_to_env(monkeypatch):
    cfg = _cfg()
    cfg.internal.api_key = ""               # 직접 키 없음
    cfg.internal.api_key_env = "MY_KEY"
    monkeypatch.setenv("MY_KEY", "from-env")
    be = LangChainBackend(cfg)
    assert be._api_key() == "from-env"


def test_factory_routes_to_langchain_lazily():
    # 생성은 지연 import 라 langchain 미설치여도 객체가 만들어져야 한다.
    cfg = AppConfig()
    cfg.llm.backend = "langchain"
    chat, emb = build_clients(cfg)
    assert isinstance(chat, LangChainBackend)
    assert chat is emb
