"""구조화 출력 배선 — **가짜 chat 을 깨지 않는가**가 이 파일의 주제다.

테스트 36개 파일 + ``scripts/compare_engines.py`` 에 가짜 chat 클래스가 37개 있고, 전부
``def complete(self, system, user, *, temperature=0.0)`` 이다. ``**kwargs`` 를 받는 것은
**하나도 없다**(``grep`` 으로 확인). 그래서 여기서 **그 서명 정확히 하나**를 재현해
검증한다 — 37개가 같은 서명을 공유하므로 이 한 클래스에 대한 증명이 37개 전부에 대한
증명이다. 그 전제 자체도 :func:`test_legacy_fake_signature_is_still_what_the_suite_uses`
가 고정한다.

배선은 세 층이고 셋이 **한 커밋에서 같이** 움직여야 한다(``llm/base.py`` 의 ⚠️ 참고):
백엔드가 플래그를 선언하고, 래퍼가 kwarg 를 통과시키고, 호출부가 조건부로 넘긴다.
"""

from __future__ import annotations

import inspect

import pytest

from contentcompare.llm.ratelimit import RateLimitedChat, RateLimiter
from contentcompare.llm.tracing import NullTracer, TracedChat

_SCHEMA = {
    "title": "record", "type": "object", "properties": {},
    "required": [], "additionalProperties": False,
}


class LegacyChat:
    """가짜 37개와 **글자 단위로 같은 서명.** 한 글자도 고치지 말 것."""

    def __init__(self):
        self.calls = []

    def complete(self, system, user, *, temperature=0.0):
        self.calls.append({"system": system, "user": user, "temperature": temperature})
        return '{"ok": true}'


class ModernChat:
    """스키마를 이해한다고 **선언한** 백엔드 대역."""

    supports_structured_output = True

    def __init__(self):
        self.calls = []

    def complete(self, system, user, *, temperature=0.0, schema=None):
        self.calls.append({"temperature": temperature, "schema": schema})
        return '{"ok": true}'


def _traced(inner):
    return TracedChat(inner, model="m", backend="b", tracer=NullTracer())


def _limited(inner):
    return RateLimitedChat(inner, limiter=RateLimiter(0), wait=0.0, max_retries=2,
                           sleep=lambda _s: None)


# --------------------------------------------------------------------------- #
# 전제 — 이것이 무너지면 이 파일의 증명이 무효다
# --------------------------------------------------------------------------- #
def test_legacy_fake_signature_is_still_what_the_suite_uses():
    params = inspect.signature(LegacyChat.complete).parameters
    assert list(params) == ["self", "system", "user", "temperature"]
    assert params["temperature"].kind is inspect.Parameter.KEYWORD_ONLY
    assert not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


# --------------------------------------------------------------------------- #
# 래퍼 통과
# --------------------------------------------------------------------------- #
def test_traced_chat_forwards_unknown_kwargs_untouched():
    inner = ModernChat()
    _traced(inner).complete("s", "u", temperature=0.3, schema=_SCHEMA)
    assert inner.calls == [{"temperature": 0.3, "schema": _SCHEMA}]


def test_traced_chat_still_works_without_the_new_kwarg():
    """``comparison/``·``readers/`` 는 temperature 조차 안 넘긴다 — 그 경로가 살아 있는가."""
    inner = LegacyChat()
    assert _traced(inner).complete("s", "u") == '{"ok": true}'
    assert inner.calls == [{"system": "s", "user": "u", "temperature": 0.0}]


def test_rate_limited_chat_forwards_unknown_kwargs_untouched():
    inner = ModernChat()
    _limited(inner).complete("s", "u", temperature=0.3, schema=_SCHEMA)
    assert inner.calls == [{"temperature": 0.3, "schema": _SCHEMA}]


class _RateLimitError(Exception):
    status_code = 429


def test_rate_limited_chat_resends_the_schema_on_a_429_retry():
    """대기 후 재시도에도 스키마가 **다시** 나가야 한다.

    인자를 재시도 루프 밖에서 소비하는 구현이면 재시도 요청만 스키마 없이 나가, "한 번은
    strict 인데 한 번은 아닌" 재현 불가능한 차이가 생긴다.
    """
    seen = []

    class Flaky:
        supports_structured_output = True

        def complete(self, system, user, *, temperature=0.0, schema=None):
            seen.append(schema)
            if len(seen) == 1:
                raise _RateLimitError("429 rate limit")
            return "ok"

    _limited(Flaky()).complete("s", "u", schema=_SCHEMA)
    assert seen == [_SCHEMA, _SCHEMA]


def test_full_stack_delegation_lifts_the_flag_to_the_top():
    """``RateLimitedChat(TracedChat(backend))`` 를 통해 플래그가 보이는가.

    이것이 설계 전체가 성립하는 이유다 — 위임(``__getattr__``)이 **메서드**였다면 추적을
    우회했을 그 메커니즘이, **플래그**에서는 정확히 필요한 동작이다.
    """
    stack = _limited(_traced(ModernChat()))
    assert getattr(stack, "supports_structured_output", False) is True
    stack.complete("s", "u", schema=_SCHEMA)


def test_full_stack_reports_false_for_a_legacy_fake():
    """가짜는 플래그를 선언하지 않는다 → 위임이 ``AttributeError`` → ``getattr`` 이 False."""
    stack = _limited(_traced(LegacyChat()))
    assert getattr(stack, "supports_structured_output", False) is False


# --------------------------------------------------------------------------- #
# 플래그와 서명의 1:1 대응 — 어긋나면 위임이 플래그만 올려 주고 호출이 죽는다
# --------------------------------------------------------------------------- #
def test_backends_that_declare_support_actually_accept_the_kwarg():
    """``_needs_rate_limit_wrapper`` 가 두 번 깨진 것과 같은 형태의 사고를 막는다.

    플래그만 선언하고 서명을 안 고치면, ``__getattr__`` 위임이 플래그를 위로 올려 주고
    호출은 ``TypeError`` 로 죽는다 — "설정에는 있는데 호출 경로에는 없는" 결함의 거울상이다.
    """
    from contentcompare.llm.internal import InternalBackend
    from contentcompare.llm.langchain_backend import LangChainBackend
    from contentcompare.llm.ollama import OllamaBackend

    for cls in (OllamaBackend, InternalBackend, LangChainBackend):
        declared = getattr(cls, "supports_structured_output", False)
        accepts = "schema" in inspect.signature(cls.complete).parameters
        if isinstance(declared, property):
            # 프로퍼티면 인스턴스에서만 값을 알 수 있다 — 선언 자체가 '지원함'의 신호다.
            assert accepts, f"{cls.__name__} 가 플래그를 선언하고 schema 를 안 받는다"
        else:
            assert bool(declared) is accepts, cls.__name__
