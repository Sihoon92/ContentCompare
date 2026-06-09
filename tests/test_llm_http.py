"""Phase 5: HTTP 재시도/타임아웃/에러 + 백엔드 파싱 테스트(네트워크 불필요)."""

from __future__ import annotations

import pytest

from contentcompare.config import LLMConfig
from contentcompare.llm.http import (
    LLMRequestError,
    RateLimitError,
    RetryPolicy,
    TransientError,
    extract,
    looks_like_rate_limit,
    post_json,
    retry_on_rate_limit,
)
from contentcompare.llm.internal import InternalBackend
from contentcompare.llm.ollama import OllamaBackend


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        if isinstance(self._json, Exception):
            raise self._json
        return self._json


def scripted_poster(events):
    """호출마다 events 의 다음 항목을 반환/예외 발생시키는 poster + 호출카운터."""
    state = {"i": 0}

    def poster(url, **kwargs):
        ev = events[min(state["i"], len(events) - 1)]
        state["i"] += 1
        if isinstance(ev, Exception):
            raise ev
        return ev

    poster.calls = state
    return poster


_NOSLEEP = lambda _d: None  # noqa: E731


# --------------------------------------------------------------------------- #
# post_json
# --------------------------------------------------------------------------- #
def test_post_json_success():
    poster = scripted_poster([FakeResponse(200, {"ok": 1})])
    out = post_json("http://x", {}, poster=poster, sleep=_NOSLEEP)
    assert out == {"ok": 1}


def test_post_json_retries_transient_then_succeeds():
    poster = scripted_poster([TransientError("conn"), FakeResponse(200, {"ok": 1})])
    sleeps = []
    out = post_json(
        "http://x", {}, poster=poster, sleep=sleeps.append,
        retry=RetryPolicy(max_retries=3, backoff_base=2.0),
    )
    assert out == {"ok": 1}
    assert poster.calls["i"] == 2
    assert sleeps == [2.0]  # 1회 재시도 → 2s 대기


def test_post_json_gives_up_after_max_retries():
    poster = scripted_poster([TransientError("conn")])
    with pytest.raises(LLMRequestError, match="재시도"):
        post_json("http://x", {}, poster=poster, sleep=_NOSLEEP,
                  retry=RetryPolicy(max_retries=2))
    assert poster.calls["i"] == 3  # 최초 1 + 재시도 2


def test_post_json_4xx_not_retried():
    poster = scripted_poster([FakeResponse(404, text="not found")])
    with pytest.raises(LLMRequestError, match="HTTP 404"):
        post_json("http://x", {}, poster=poster, sleep=_NOSLEEP)
    assert poster.calls["i"] == 1  # 재시도 없음


def test_post_json_5xx_retried():
    poster = scripted_poster([FakeResponse(503, text="busy")])
    with pytest.raises(LLMRequestError):
        post_json("http://x", {}, poster=poster, sleep=_NOSLEEP,
                  retry=RetryPolicy(max_retries=2))
    assert poster.calls["i"] == 3


def test_post_json_429_waits_rate_limit_then_succeeds():
    # 429 는 일반 백오프(2s) 가 아니라 rate_limit_wait(여기선 60s) 만큼 대기.
    poster = scripted_poster([FakeResponse(429, text="rate limited"),
                              FakeResponse(200, {"ok": 1})])
    sleeps = []
    out = post_json(
        "http://x", {}, poster=poster, sleep=sleeps.append,
        retry=RetryPolicy(rate_limit_wait=60.0, rate_limit_max_retries=3),
    )
    assert out == {"ok": 1}
    assert sleeps == [60.0]  # 분당 한도 회복 대기


def test_post_json_429_honors_retry_after_header():
    poster = scripted_poster([FakeResponse(429, headers={"Retry-After": "12"}),
                              FakeResponse(200, {"ok": 1})])
    sleeps = []
    post_json("http://x", {}, poster=poster, sleep=sleeps.append,
              retry=RetryPolicy(rate_limit_wait=60.0))
    assert sleeps == [12.0]  # 서버가 알려준 대기 우선


def test_post_json_429_separate_budget_from_transient():
    # 429 전용 예산(rate_limit_max_retries)을 소진하면 실패.
    poster = scripted_poster([FakeResponse(429, text="rl")])
    with pytest.raises(LLMRequestError, match="429"):
        post_json("http://x", {}, poster=poster, sleep=_NOSLEEP,
                  retry=RetryPolicy(rate_limit_max_retries=2))
    assert poster.calls["i"] == 3  # 최초 1 + 재시도 2


def test_rate_limit_error_is_transient_subclass():
    assert issubclass(RateLimitError, TransientError)


# --- 429 가 아닌 한도 신호도 1분 대기 경로로 ------------------------------- #
def test_non_429_status_with_rate_limit_body_waits():
    # 503 이지만 본문이 한도 → 일반 백오프(2s)가 아니라 rate_limit_wait(60s) 대기.
    poster = scripted_poster([FakeResponse(503, text="Rate limit exceeded, try later"),
                              FakeResponse(200, {"ok": 1})])
    sleeps = []
    out = post_json("http://x", {}, poster=poster, sleep=sleeps.append,
                    retry=RetryPolicy(rate_limit_wait=60.0))
    assert out == {"ok": 1}
    assert sleeps == [60.0]


def test_4xx_with_rate_limit_body_is_retried_not_fatal():
    # 400/403 등으로 한도를 알리는 게이트웨이 → 치명 오류가 아니라 한도 재시도.
    poster = scripted_poster([FakeResponse(400, text="요청 한도를 초과했습니다"),
                              FakeResponse(200, {"ok": 1})])
    sleeps = []
    out = post_json("http://x", {}, poster=poster, sleep=sleeps.append,
                    retry=RetryPolicy(rate_limit_wait=60.0))
    assert out == {"ok": 1}
    assert sleeps == [60.0]


def test_200_with_rate_limit_error_body_waits():
    # HTTP 200 인데 본문에 한도 오류를 담은 게이트웨이.
    poster = scripted_poster([
        FakeResponse(200, {"error": {"message": "Too Many Requests"}}),
        FakeResponse(200, {"ok": 1}),
    ])
    sleeps = []
    out = post_json("http://x", {}, poster=poster, sleep=sleeps.append,
                    retry=RetryPolicy(rate_limit_wait=60.0))
    assert out == {"ok": 1}
    assert sleeps == [60.0]


def test_looks_like_rate_limit():
    assert looks_like_rate_limit("Rate limit reached")
    assert looks_like_rate_limit("요청 한도 초과")
    assert looks_like_rate_limit("HTTP 429 too many requests")
    assert not looks_like_rate_limit("invalid api key")


# --- retry_on_rate_limit 래퍼(비-HTTP 클라이언트용) ----------------------- #
def test_retry_on_rate_limit_waits_then_succeeds():
    calls = {"n": 0}
    sleeps = []

    def fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Rate limit exceeded")  # 메시지로 한도 판별
        return "ok"

    out = retry_on_rate_limit(fn, retry=RetryPolicy(rate_limit_wait=60.0), sleep=sleeps.append)
    assert out == "ok" and sleeps == [60.0]


def test_retry_on_rate_limit_passes_through_other_errors():
    def fn():
        raise ValueError("boom")  # 한도 아님 → 그대로 전파

    with pytest.raises(ValueError, match="boom"):
        retry_on_rate_limit(fn, sleep=_NOSLEEP)


def test_retry_on_rate_limit_gives_up_after_budget():
    def fn():
        raise RuntimeError("429 too many requests")

    with pytest.raises(LLMRequestError, match="요청 한도"):
        retry_on_rate_limit(fn, retry=RetryPolicy(rate_limit_max_retries=2), sleep=_NOSLEEP)


def test_post_json_bad_body_raises():
    poster = scripted_poster([FakeResponse(200, json_data=ValueError("bad"))])
    with pytest.raises(LLMRequestError, match="JSON 파싱"):
        post_json("http://x", {}, poster=poster, sleep=_NOSLEEP)


def test_proxy_ctx_entered_each_attempt():
    entered = {"n": 0}

    class Ctx:
        def __enter__(self): entered["n"] += 1
        def __exit__(self, *a): return False

    poster = scripted_poster([TransientError("c"), FakeResponse(200, {"ok": 1})])
    post_json("http://x", {}, poster=poster, sleep=_NOSLEEP,
              proxy_ctx=Ctx, retry=RetryPolicy(max_retries=2))
    assert entered["n"] == 2  # 매 시도마다 컨텍스트 진입


# --------------------------------------------------------------------------- #
# extract
# --------------------------------------------------------------------------- #
def test_extract_nested_ok():
    assert extract({"a": {"b": [10, 20]}}, "a", "b", 1) == 20


def test_extract_missing_raises():
    with pytest.raises(LLMRequestError, match="형식이 예상과 다릅"):
        extract({"a": {}}, "a", "b", "c")


# --------------------------------------------------------------------------- #
# 백엔드 파싱
# --------------------------------------------------------------------------- #
def test_ollama_complete_parses_content():
    poster = scripted_poster([FakeResponse(200, {"message": {"content": "안녕"}})])
    be = OllamaBackend(LLMConfig(), poster=poster, sleep=_NOSLEEP)
    assert be.complete("sys", "usr") == "안녕"


def test_ollama_embed_loops_texts():
    poster = scripted_poster([
        FakeResponse(200, {"embedding": [0.1, 0.2]}),
        FakeResponse(200, {"embedding": [0.3, 0.4]}),
    ])
    be = OllamaBackend(LLMConfig(), poster=poster, sleep=_NOSLEEP)
    assert be.embed(["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]


def test_internal_complete_parses_choices():
    poster = scripted_poster([
        FakeResponse(200, {"choices": [{"message": {"content": "응답"}}]}),
    ])
    be = InternalBackend(LLMConfig(), poster=poster, sleep=_NOSLEEP)
    assert be.complete("sys", "usr") == "응답"


def test_internal_embed_sorts_by_index():
    poster = scripted_poster([
        FakeResponse(200, {"data": [
            {"index": 1, "embedding": [9.0]},
            {"index": 0, "embedding": [1.0]},
        ]}),
    ])
    be = InternalBackend(LLMConfig(), poster=poster, sleep=_NOSLEEP)
    assert be.embed(["a", "b"]) == [[1.0], [9.0]]  # index 순 정렬


def test_internal_retries_with_injected_sleep():
    poster = scripted_poster([
        TransientError("conn"),
        FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]}),
    ])
    cfg = LLMConfig(max_retries=2)
    cfg.internal.unset_proxy = False
    be = InternalBackend(cfg, poster=poster, sleep=_NOSLEEP)
    assert be.complete("s", "u") == "ok"
    assert poster.calls["i"] == 2
