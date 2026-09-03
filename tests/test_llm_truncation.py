"""출력 길이 한도(절단) 감지 — 네 백엔드가 같은 사건을 네 이름으로 부른다.

실측 배경: F2 records 배치에서 ``completion_tokens=32368`` 로 실행이 죽었는데, 화면에는
"length limit" 만 보이고 원인·조치가 드러나지 않았다. 그 문구는 우리 것이 아니라 openai SDK
``LengthFinishReasonError`` 이고, 우리 쪽 어떤 핸들러도 그것을 인식하지 못해
``classify_error`` 가 ``"error"`` 로 뭉갰다.

⚠️ **이 파일의 절반은 역방향 고정이다.** 절단 예외가 한도/타임아웃으로 **오인되지 않는지**를
지키는 것이 감지 자체보다 중요하다 — 오인되면 기다려도 안 풀리는 것을 5회×60초 기다린다.
"""

import pytest

from contentcompare.config import LLMConfig
from contentcompare.llm.ratelimit import is_rate_limit, is_timeout
from contentcompare.llm.truncation import (
    LENGTH,
    LengthLimitError,
    evidence_of,
    finish_reason_of,
    fold,
    from_exception,
    from_truncated,
    is_length_limit,
)


# --------------------------------------------------------------------------- #
# openai SDK 흉내 — .completion 에 잘린 본문과 usage 를 들고 온다
# --------------------------------------------------------------------------- #
def _completion(content: str, *, reason: str = LENGTH,
                prompt: int = 8210, completion: int = 32368):
    usage = type("CompletionUsage", (), {
        "prompt_tokens": prompt, "completion_tokens": completion})()
    message = type("Message", (), {"content": content})()
    choice = type("Choice", (), {"finish_reason": reason, "message": message})()
    return type("ChatCompletion", (), {"choices": [choice], "usage": usage})()


class FakeLengthFinishReasonError(Exception):
    """``openai.LengthFinishReasonError`` 흉내.

    실물은 ``OpenAIError`` 하위라 **status_code 도 response 도 없다** — 그래서
    ``looks_like_schema_rejection``(400/422 요구)·``is_rate_limit``·``is_timeout`` 을
    전부 통과해 원인 불명이 됐다. 그 조건을 그대로 재현한다.
    """

    def __init__(self, content: str = "{\"records\": [", **kw) -> None:
        super().__init__(
            "Could not parse response content as the length limit was reached"
            " - CompletionUsage(completion_tokens=32368, prompt_tokens=8210,"
            " total_tokens=40578)")
        self.completion = _completion(content, **kw)


# --------------------------------------------------------------------------- #
# 1) 감지
# --------------------------------------------------------------------------- #
def test_detects_by_completion_finish_reason():
    """가장 강한 근거는 구조다 — 메시지 문구가 바뀌어도 살아남는다."""
    exc = FakeLengthFinishReasonError()
    exc.args = ("문구가 통째로 바뀌어도",)
    assert is_length_limit(exc)


def test_detects_by_class_name():
    exc = type("LengthFinishReasonError", (Exception,), {})("무언가 잘못됨")
    assert is_length_limit(exc)


def test_detects_by_message_marker():
    exc = RuntimeError("Could not parse response content as the length limit was reached")
    assert is_length_limit(exc)


def test_our_own_exception_is_detected():
    assert is_length_limit(LengthLimitError("잘렸습니다"))


@pytest.mark.parametrize("message", [
    "Content-Length header missing",
    "maximum context length is 8192 tokens",   # 입력 초과 — 여기서는 절단이 아니다
    "field 'length' is required",
])
def test_bare_length_word_is_not_enough(message):
    """벌거벗은 ``length`` 부분일치 금지.

    넓히면 무관한 실패에도 배치를 쪼개 **모든 실패의 비용이 두 배**가 된다
    (``looks_like_schema_rejection`` 을 좁게 둔 것과 같은 근거).
    """
    assert not is_length_limit(RuntimeError(message))


def test_normal_finish_reason_is_not_length():
    """정상 종료한 응답을 든 예외는 절단이 아니다.

    :class:`FakeLengthFinishReasonError` 를 쓰지 않는 이유는 그 이름·메시지가 근거 ②③에
    걸리기 때문이다 — 여기서 시험하려는 것은 **구조 근거(①)의 음성**이다.
    """
    exc = RuntimeError("서버가 뭔가 다른 이유로 실패했다")
    exc.completion = _completion("정상 본문", reason="stop")
    assert not is_length_limit(exc)


# --------------------------------------------------------------------------- #
# 2) 역방향 — 한도/타임아웃으로 **오인되지 않는다**
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("make", [
    lambda: FakeLengthFinishReasonError(),
    lambda: from_exception(FakeLengthFinishReasonError(), backend="langchain"),
    lambda: from_truncated({"done_reason": "length"}, backend="ollama", output="잘림"),
])
def test_length_is_never_mistaken_for_rate_limit_or_timeout(make):
    """**이 파일에서 가장 중요한 테스트.**

    길이 한도는 기다려도 안 풀린다(``temperature=0`` → 재전송이 같은 지점에서 잘림).
    한도나 타임아웃으로 오인되면 ``RateLimitedChat`` 이 5회×60초를 순수하게 버린다.

    실제로 개발 중 한 번 걸렸다 — 조치 문구에 ``llm.timeout`` 을 넣었더니 메시지 마커
    ``"timeout"`` 에 걸려 ``is_timeout`` 이 참이 됐다. 그래서 조치 문구는 이 판정을 안 타는
    ``timeline._HINTS`` 로 옮겼다.
    """
    exc = make()
    assert is_length_limit(exc)
    assert not is_rate_limit(exc)
    assert not is_timeout(exc)


def test_rate_limit_is_not_mistaken_for_length():
    """반대 방향도 막는다 — 429 를 절단으로 보면 쓸데없이 배치를 쪼갠다."""
    exc = type("RateLimitError", (Exception,), {})("rate limit exceeded (60/60)")
    assert not is_length_limit(exc)


# --------------------------------------------------------------------------- #
# 3) 증거 추출 — 지금까지 raise 와 함께 버려지던 것
# --------------------------------------------------------------------------- #
def test_evidence_carries_truncated_body_and_tokens():
    exc = FakeLengthFinishReasonError(content="A" * 9000)
    output, usage = evidence_of(exc)

    assert usage.input_tokens == 8210 and usage.output_tokens == 32368
    assert output.startswith("A") and output.endswith("A")
    assert "생략" in output          # 접혔다


def test_missing_evidence_stays_unknown():
    """못 찾은 것은 지어내지 않는다(``usage.py`` 원칙 2)."""
    output, usage = evidence_of(RuntimeError("the length limit was reached"))
    assert output == ""
    assert not usage.known


def test_fold_keeps_the_tail():
    """진단에서 값진 것은 **꼬리**다 — 어디서 끊겼는지, 무엇이 반복되는지가 거기 있다."""
    text = "머리" * 3000 + "END"
    folded = fold(text, head=10, tail=10)
    assert folded.endswith("END")
    assert folded.startswith("머리")
    assert len(folded) < len(text)


def test_fold_leaves_short_text_alone():
    assert fold("짧다") == "짧다"
    assert fold("") == ""


def test_output_chars_is_the_length_before_folding():
    """접힌 길이를 남기면 "출력이 짧았다"로 뜻이 뒤집힌다."""
    err = from_exception(FakeLengthFinishReasonError(content="A" * 9000),
                         backend="langchain")
    assert err.output_chars == 9000
    assert len(err.output) < 9000


# --------------------------------------------------------------------------- #
# 4) finish_reason_of — 예외 없이 200 으로 잘려 오는 경로 셋
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("response, expected", [
    ({"done_reason": "length"}, "length"),                          # ollama
    ({"choices": [{"finish_reason": "length"}]}, "length"),         # internal
    ({"done_reason": "stop"}, "stop"),
    ({}, ""),
    ("문자열", ""),
    (None, ""),
])
def test_finish_reason_of_mappings(response, expected):
    assert finish_reason_of(response) == expected


def test_finish_reason_of_langchain_message():
    msg = type("AIMessage", (), {"response_metadata": {"finish_reason": "length"}})()
    assert finish_reason_of(msg) == LENGTH


# --------------------------------------------------------------------------- #
# 5) 백엔드 배선 — 넷이 같은 예외를 올린다
# --------------------------------------------------------------------------- #
def test_ollama_partial_generation_now_raises():
    """예전에는 **빈 응답일 때만** 봤다 — 부분 생성이 조용히 통과해 파싱 실패로만 보였다."""
    from tests.test_llm_http import FakeResponse, scripted_poster, _NOSLEEP
    from contentcompare.llm.ollama import OllamaBackend

    poster = scripted_poster([FakeResponse(200, {
        "message": {"content": '{"records": [{"record_id"'},
        "done_reason": "length", "prompt_eval_count": 100, "eval_count": 4096,
    })])
    be = OllamaBackend(LLMConfig(), poster=poster, sleep=_NOSLEEP)
    with pytest.raises(LengthLimitError) as caught:
        be.complete("sys", "usr")
    assert caught.value.output.startswith('{"records"')
    assert caught.value.usage.output_tokens == 4096


def test_internal_truncation_raises_instead_of_returning_partial_json():
    from tests.test_llm_http import FakeResponse, scripted_poster, _NOSLEEP
    from contentcompare.llm.internal import InternalBackend

    poster = scripted_poster([FakeResponse(200, {
        "choices": [{"finish_reason": "length",
                     "message": {"content": '{"records": ['}}],
        "usage": {"prompt_tokens": 500, "completion_tokens": 2048},
    })])
    be = InternalBackend(LLMConfig(), poster=poster, sleep=_NOSLEEP)
    with pytest.raises(LengthLimitError) as caught:
        be.complete("sys", "usr")
    assert caught.value.output == '{"records": ['
    assert caught.value.usage.output_tokens == 2048


def test_internal_normal_response_is_unchanged():
    """절단이 아니면 오늘과 완전히 같다."""
    from tests.test_llm_http import FakeResponse, scripted_poster, _NOSLEEP
    from contentcompare.llm.internal import InternalBackend

    poster = scripted_poster([FakeResponse(200, {
        "choices": [{"finish_reason": "stop", "message": {"content": "정상"}}],
    })])
    be = InternalBackend(LLMConfig(), poster=poster, sleep=_NOSLEEP)
    assert be.complete("sys", "usr") == "정상"


# --------------------------------------------------------------------------- #
# 6) 타임라인 — 라벨과 조치가 timeout 과 갈린다
# --------------------------------------------------------------------------- #
def test_classify_error_labels_length_separately():
    from contentcompare.timeline import ERROR_STATUSES, classify_error

    assert classify_error(FakeLengthFinishReasonError()) == "length"
    assert classify_error(LengthLimitError("잘렸습니다")) == "length"
    assert "length" in ERROR_STATUSES     # 조회·UI 가 실패로 세야 한다


def test_length_hint_does_not_tell_you_to_wait_longer():
    """timeout 힌트와 반드시 갈라야 한다 — 그쪽은 "더 기다려라"인데 여기선 무의미하다."""
    from contentcompare.timeline import TimelineEvent, diagnose

    event = TimelineEvent(ts=0.0, kind="llm_end", name="F2 records", status="length")
    (hint,) = diagnose([event])
    assert "record_batch_rows" in hint
    assert "풀리지 않습니다" in hint


# --------------------------------------------------------------------------- #
# 7) max_tokens — **한 테스트가 세 백엔드를 동시에 붙잡는다**
#
# `_needs_rate_limit_wrapper` 가 "설정에는 있는데 호출 경로에는 없는" 결함으로 두 번
# 깨진 이유는 조건이 인라인이라 **같이 고칠 자리가 안 보였기** 때문이다. 여기서는 이
# 테스트가 그 자리다 — 백엔드를 하나 더 만들거나 한 곳만 배선하면 여기서 깨진다.
# --------------------------------------------------------------------------- #
def _capture_payload():
    """``poster`` 가 받은 payload 를 그대로 잡아 둔다(ollama·internal 공용)."""
    from tests.test_llm_http import FakeResponse

    seen: dict = {}

    def poster(url, **kwargs):
        seen.update(kwargs.get("json") or {})
        return FakeResponse(200, {
            "message": {"content": "{}"},                       # ollama
            "choices": [{"message": {"content": "{}"}}],        # internal
        })

    return poster, seen


class _BindSpy:
    """``chat.bind(**kwargs).invoke(...)`` 의 kwargs 를 잡는 langchain chat 흉내."""

    def __init__(self) -> None:
        self.bound: dict = {}

    def bind(self, **kwargs):
        self.bound = kwargs
        return self

    def invoke(self, messages):
        return type("AIMessage", (), {"content": "{}", "response_metadata": {}})()


def _langchain_bound(cfg) -> dict:
    from contentcompare.llm.langchain_backend import LangChainBackend

    spy = _BindSpy()
    backend = LangChainBackend(cfg, chat=spy)
    backend.complete("s", "u")
    return spy.bound


@pytest.mark.parametrize("max_tokens", [0, 2048])
def test_max_tokens_reaches_every_backend(max_tokens):
    """세 백엔드가 **같은 설정 한 줄**을 각자의 이름으로 서버에 보낸다.

    ``0`` 일 때 키가 **아예 없어야** 하는 것도 같이 고정한다 — 이 손잡이의 기본값 계약이
    "오늘과 바이트 단위로 같은 요청"이기 때문이다(``response_format=None`` 을 안 싣는
    규칙과 같은 근거).
    """
    from tests.test_llm_http import _NOSLEEP
    from contentcompare.llm.internal import InternalBackend
    from contentcompare.llm.ollama import OllamaBackend

    cfg = LLMConfig(max_tokens=max_tokens)

    poster, seen = _capture_payload()
    OllamaBackend(cfg, poster=poster, sleep=_NOSLEEP).complete("s", "u")
    assert seen["options"].get("num_predict") == (max_tokens or None)

    poster, seen = _capture_payload()
    InternalBackend(cfg, poster=poster, sleep=_NOSLEEP).complete("s", "u")
    assert seen.get("max_tokens") == (max_tokens or None)

    assert _langchain_bound(cfg).get("max_tokens") == (max_tokens or None)
