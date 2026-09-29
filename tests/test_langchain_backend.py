"""LangChain 백엔드 테스트 — chat/embeddings 객체 주입으로 langchain 미설치 환경에서도 검증."""

from __future__ import annotations

import pytest

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
    cfg.llm.rate_limit_wait = 0  # 한도 래퍼도 끈다 — 켜면 RateLimitedChat 이 바깥에 붙어 이 검증을 가린다
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


# --------------------------------------------------------------------------- #
# 구조화 출력 (llm.structured_output)
# --------------------------------------------------------------------------- #
_SCHEMA = {
    "title": "record", "type": "object", "properties": {"a": {"type": "string"}},
    "required": ["a"], "additionalProperties": False,
}


class _SchemaRejected(Exception):
    """게이트웨이가 스키마를 거절할 때의 예외 모양(상태코드 + 본문 마커)."""

    status_code = 400


class RejectingChat(FakeChat):
    """``response_format`` 이 실려 오면 거절한다 — 스키마를 안 받는 게이트웨이 대역."""

    def __init__(self, reply="OK"):
        super().__init__(reply)
        self.attempts = []

    def invoke(self, messages):
        self.attempts.append(self.bound)
        if "response_format" in (self.bound or {}):
            raise _SchemaRejected("Invalid value for 'response_format': not supported")
        return super().invoke(messages)


def test_schema_none_sends_exactly_todays_request():
    """구조화 출력의 대전제 — 스키마를 안 주면 **바이트 단위로 같은 요청**이다."""
    chat = FakeChat(reply="OK")
    be = LangChainBackend(_cfg(), chat=chat, embeddings=FakeEmb())
    be.complete("시스템", "유저", temperature=0.3)
    assert chat.bound == {"temperature": 0.3}   # response_format 키 자체가 없다


def test_schema_is_wrapped_in_the_openai_envelope():
    chat = FakeChat(reply="OK")
    be = LangChainBackend(_cfg(), chat=chat, embeddings=FakeEmb())
    be.complete("시스템", "유저", schema=_SCHEMA)
    fmt = chat.bound["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "record"     # 스키마 title 과 짝
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] is _SCHEMA


def test_usage_is_still_read_when_a_schema_is_given():
    """⚠️ ``with_structured_output()`` 으로 회귀하면 **여기서 죽는다.**

    그쪽은 AIMessage 가 아닌 pydantic 객체를 돌려줘 ``from_response`` 가 토큰을 못 읽고,
    이 저장소에서 미상은 "서버가 안 줬다"는 뜻이라 우리가 잃어버린 것이 서버 탓으로
    기록된다 — ``tok_per_sec`` 이 사라져 배치 크기 판단 근거가 없어진다.
    """
    class UsageMsg(FakeMsg):
        usage_metadata = {"input_tokens": 11, "output_tokens": 22}

    class UsageChat(FakeChat):
        def invoke(self, messages):
            return UsageMsg(self.reply)

    be = LangChainBackend(_cfg(), chat=UsageChat(), embeddings=FakeEmb())
    be.complete("시스템", "유저", schema=_SCHEMA)
    assert be.last_usage.input_tokens == 11
    assert be.last_usage.output_tokens == 22


def test_mode_off_ignores_the_schema_entirely():
    cfg = _cfg()
    cfg.structured_output = "off"
    chat = FakeChat(reply="OK")
    be = LangChainBackend(cfg, chat=chat, embeddings=FakeEmb())
    assert be.supports_structured_output is False
    be.complete("시스템", "유저", schema=_SCHEMA)
    assert chat.bound == {"temperature": 0.0}


def test_mode_json_object_ignores_the_schema_body():
    cfg = _cfg()
    cfg.structured_output = "json_object"
    chat = FakeChat(reply="OK")
    LangChainBackend(cfg, chat=chat, embeddings=FakeEmb()).complete(
        "시스템", "유저", schema=_SCHEMA)
    assert chat.bound["response_format"] == {"type": "json_object"}


def test_a_rejected_schema_downgrades_once_and_still_returns_content():
    """실행을 죽이지 않는다 — 40분짜리 파이프라인을 한복판에서 끊을 근거가 없다."""
    chat = RejectingChat(reply="OK")
    be = LangChainBackend(_cfg(), chat=chat, embeddings=FakeEmb())
    assert be.complete("시스템", "유저", schema=_SCHEMA) == "OK"
    assert len(chat.attempts) == 2                      # 스키마 있음 → 없음
    assert "response_format" in chat.attempts[0]
    assert "response_format" not in chat.attempts[1]
    assert be.supports_structured_output is False       # 이 실행 동안 꺼진다


def test_after_a_downgrade_later_calls_do_not_retry_the_schema():
    """강등이 래치되지 않으면 남은 수백 회가 전부 같은 400 을 맞는다."""
    chat = RejectingChat(reply="OK")
    be = LangChainBackend(_cfg(), chat=chat, embeddings=FakeEmb())
    be.complete("시스템", "유저", schema=_SCHEMA)
    be.complete("시스템", "유저", schema=_SCHEMA)
    assert len(chat.attempts) == 3                      # 2 + 1 (두 번째는 시도조차 안 함)


def test_unrelated_failures_are_not_retried():
    """넓게 잡으면 **모든 실패의 비용이 두 배**가 된다 — 실패한 호출을 한 번 더 하니까."""
    class Unauthorized(FakeChat):
        status = 401

        def invoke(self, messages):
            self.attempts = getattr(self, "attempts", [])
            self.attempts.append(self.bound)
            exc = Exception("invalid api key")
            exc.status_code = 401
            raise exc

    chat = Unauthorized()
    be = LangChainBackend(_cfg(), chat=chat, embeddings=FakeEmb())
    with pytest.raises(Exception, match="invalid api key"):
        be.complete("시스템", "유저", schema=_SCHEMA)
    assert len(chat.attempts) == 1
    assert be.supports_structured_output is True        # 강등하지 않는다


def test_a_typo_in_the_mode_dies_at_construction_not_mid_pipeline():
    cfg = _cfg()
    cfg.structured_output = "json-schema"
    with pytest.raises(ValueError, match="json-schema"):
        LangChainBackend(cfg, chat=FakeChat(), embeddings=FakeEmb())
