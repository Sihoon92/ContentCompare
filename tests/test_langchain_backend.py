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
    # 타임라인은 끈다 — 켜면 chat 이 TracedChat 으로 감싸여 라우팅 검증이 가려진다.
    cfg = AppConfig()
    cfg.llm.backend = "langchain"
    cfg.logging.timeline = False
    chat, emb = build_clients(cfg)
    assert isinstance(chat, LangChainBackend)
    assert chat is emb


# --------------------------------------------------------------------------- #
# 토큰 사용량 — langchain 은 dict 가 아니라 **메시지 객체 속성**으로 준다
# --------------------------------------------------------------------------- #
class UsageMsg(FakeMsg):
    def __init__(self, content, usage_metadata=None, response_metadata=None):
        super().__init__(content)
        if usage_metadata is not None:
            self.usage_metadata = usage_metadata
        if response_metadata is not None:
            self.response_metadata = response_metadata


class UsageChat(FakeChat):
    def __init__(self, message):
        super().__init__()
        self.message = message

    def invoke(self, messages):
        self.last_messages = messages
        return self.message


def test_complete_records_usage_metadata():
    from contentcompare.llm.usage import Usage

    msg = UsageMsg("OK", usage_metadata={"input_tokens": 120, "output_tokens": 34,
                                         "total_tokens": 154})
    be = LangChainBackend(_cfg(), chat=UsageChat(msg))
    assert be.complete("s", "u") == "OK"
    assert be.last_usage == Usage(input_tokens=120, output_tokens=34)


def test_complete_falls_back_to_response_metadata():
    """구버전 langchain 은 ``response_metadata.token_usage`` 에만 담는다."""
    from contentcompare.llm.usage import Usage

    msg = UsageMsg("OK", response_metadata={
        "model_name": "gpt-4o",
        "token_usage": {"prompt_tokens": 7, "completion_tokens": 3},
    })
    be = LangChainBackend(_cfg(), chat=UsageChat(msg))
    be.complete("s", "u")
    assert be.last_usage == Usage(input_tokens=7, output_tokens=3)


def test_complete_without_usage_is_unknown():
    """사용량을 안 주는 게이트웨이여도 조용히 미상으로 남는다(추정하지 않는다)."""
    from contentcompare.llm.usage import UNKNOWN

    be = LangChainBackend(_cfg(), chat=FakeChat(reply="OK"))
    be.complete("s", "u")
    assert be.last_usage == UNKNOWN
