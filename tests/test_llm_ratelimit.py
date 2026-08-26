"""사내 LLM 요청 한도(분당 N회) 대응 — 사전 스로틀 + 사후 대기.

사내 엔드포인트는 분당 60회 제한이 있고, fact 엔진은 실행 한 번에 수백 회를 부른다.
한도에 걸리는 것이 예외가 아니라 **상시 조건**이므로 두 층으로 막는다:

1. **사전 스로틀** — 애초에 한도를 넘지 않게 호출 페이스를 조절
2. **사후 대기** — 그래도 걸리면 한도가 회복될 만큼 기다렸다 재시도

테스트는 기존 규약대로 ``clock``/``sleep`` 을 주입해 **실제로 기다리지 않는다**
(``llm/http.py`` 의 sleep 주입과 같은 방식). 네트워크·플랫폼 독립.
"""

import pytest

from contentcompare.config import AppConfig


# --------------------------------------------------------------------------- #
# 가짜 시계 — 주입한 sleep 이 시간을 실제로 흐르게 한다
# --------------------------------------------------------------------------- #
class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeChat:
    """``complete`` 호출을 세고, 정해둔 예외를 앞에서부터 던진다."""

    def __init__(self, errors: list = None, reply: str = "OK") -> None:
        self.errors = list(errors or [])
        self.reply = reply
        self.calls = 0

    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.reply

    def embed(self, texts: list, *, kind: str = "passage") -> list:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return [[0.1, 0.2] for _ in texts]


class FakeRateLimitError(Exception):
    """openai.RateLimitError 를 흉내낸다 — 이름과 status_code 로 감지되어야 한다."""

    def __init__(self, message: str = "rate limit exceeded", status_code: int = 429,
                 retry_after: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        if retry_after:
            self.response = type("R", (), {"headers": {"Retry-After": retry_after}})()


# --------------------------------------------------------------------------- #
# 1) 사전 스로틀 — RateLimiter
# --------------------------------------------------------------------------- #
def test_calls_under_the_limit_never_wait():
    """한도에 못 미치면 스로틀은 존재하지 않는 것처럼 동작해야 한다."""
    from contentcompare.llm.ratelimit import RateLimiter

    clock = FakeClock()
    limiter = RateLimiter(max_per_minute=5, clock=clock.time, sleep=clock.sleep)
    for _ in range(5):
        limiter.acquire()
    assert clock.slept == []


def test_hitting_the_limit_waits_only_until_the_window_opens():
    """6번째 호출은 **첫 호출이 창을 벗어날 만큼만** 기다려야 한다.

    통째로 60초를 자면 한도를 지키긴 해도 처리량을 불필요하게 버린다.
    """
    from contentcompare.llm.ratelimit import RateLimiter

    clock = FakeClock()
    limiter = RateLimiter(max_per_minute=5, clock=clock.time, sleep=clock.sleep)
    for _ in range(5):
        limiter.acquire()
        clock.advance(10)          # 0,10,20,30,40 초에 5회
    limiter.acquire()              # 지금 50초 → 첫 호출(0초)이 60초를 지나야 한다
    assert clock.slept == [pytest.approx(10.0)]


def test_window_slides_so_old_calls_stop_counting():
    """창이 지난 호출은 잊어야 한다 — 안 그러면 영원히 막힌다."""
    from contentcompare.llm.ratelimit import RateLimiter

    clock = FakeClock()
    limiter = RateLimiter(max_per_minute=2, clock=clock.time, sleep=clock.sleep)
    limiter.acquire()
    limiter.acquire()
    clock.advance(61)              # 둘 다 창 밖으로
    limiter.acquire()
    assert clock.slept == []


def test_zero_means_no_throttle():
    """0/음수는 '끔' — 설정을 안 건드린 사용자에게 변화가 없어야 한다."""
    from contentcompare.llm.ratelimit import RateLimiter

    clock = FakeClock()
    limiter = RateLimiter(max_per_minute=0, clock=clock.time, sleep=clock.sleep)
    for _ in range(100):
        limiter.acquire()
    assert clock.slept == []


# --------------------------------------------------------------------------- #
# 2) 한도 예외 감지 — is_rate_limit
# --------------------------------------------------------------------------- #
def test_detects_rate_limit_by_status_code():
    from contentcompare.llm.ratelimit import is_rate_limit

    assert is_rate_limit(FakeRateLimitError(message="whatever", status_code=429))


def test_detects_rate_limit_by_class_name():
    """openai SDK 는 예외 클래스명으로만 구분되는 경우가 있다."""
    from contentcompare.llm.ratelimit import is_rate_limit

    exc = type("RateLimitError", (Exception,), {})("무언가 잘못됨")
    assert is_rate_limit(exc)


def test_detects_rate_limit_by_message_marker():
    """사내 게이트웨이가 상태코드를 안 주고 본문만 줄 수 있다."""
    from contentcompare.llm.ratelimit import is_rate_limit

    assert is_rate_limit(RuntimeError("Too Many Requests: quota exceeded"))
    assert is_rate_limit(RuntimeError("요청 한도를 초과했습니다"))


def test_ordinary_errors_are_not_rate_limits():
    """과하게 잡으면 진짜 오류를 1분씩 재시도하며 숨긴다."""
    from contentcompare.llm.ratelimit import is_rate_limit

    assert not is_rate_limit(RuntimeError("connection refused"))
    assert not is_rate_limit(ValueError("JSON 파싱 실패"))


def test_status_codes_and_markers_are_configurable():
    """서버가 429 가 아닌 코드를 쓰는 환경을 설정으로 흡수한다.

    클래스명에 RateLimit 이 없는 중립 예외를 써야 상태코드 판정만 검증된다.
    """
    from contentcompare.llm.ratelimit import is_rate_limit

    exc = type("ApiError", (Exception,), {})("busy")
    exc.status_code = 503
    assert not is_rate_limit(exc)
    assert is_rate_limit(exc, status_codes=(503,))
    assert is_rate_limit(RuntimeError("호출량 초과"), markers=("호출량 초과",))


# --------------------------------------------------------------------------- #
# 3) 사후 대기 — RateLimitedChat
# --------------------------------------------------------------------------- #
def _wrap(inner, *, clock, limit=0, wait=60.0, retries=5, handled=False):
    from contentcompare.llm.ratelimit import RateLimiter, RateLimitedChat

    return RateLimitedChat(
        inner,
        limiter=RateLimiter(max_per_minute=limit, clock=clock.time, sleep=clock.sleep),
        wait=wait, max_retries=retries,
        inner_handles_rate_limit=handled,
        sleep=clock.sleep,
    )


def test_rate_limit_error_waits_then_succeeds():
    clock = FakeClock()
    chat = FakeChat(errors=[FakeRateLimitError()])
    traced = _wrap(chat, clock=clock)

    assert traced.complete("s", "u") == "OK"
    assert clock.slept == [60.0]
    assert chat.calls == 2


def test_retry_after_header_wins_over_configured_wait():
    """서버가 언제 풀리는지 알려주면 그 말을 믿는 편이 빠르고 정확하다."""
    clock = FakeClock()
    chat = FakeChat(errors=[FakeRateLimitError(retry_after="12")])
    traced = _wrap(chat, clock=clock, wait=60.0)

    traced.complete("s", "u")
    assert clock.slept == [12.0]


def test_budget_exhaustion_reraises_the_original_error():
    """무한 대기는 안 된다 — 예산이 끝나면 원인을 그대로 올려보낸다."""
    clock = FakeClock()
    chat = FakeChat(errors=[FakeRateLimitError() for _ in range(5)])
    traced = _wrap(chat, clock=clock, retries=2)

    with pytest.raises(FakeRateLimitError):
        traced.complete("s", "u")
    assert chat.calls == 3          # 최초 1 + 재시도 2
    assert clock.slept == [60.0, 60.0]


def test_non_rate_limit_errors_are_raised_immediately():
    clock = FakeClock()
    chat = FakeChat(errors=[RuntimeError("연결 실패")])
    traced = _wrap(chat, clock=clock)

    with pytest.raises(RuntimeError, match="연결 실패"):
        traced.complete("s", "u")
    assert chat.calls == 1
    assert clock.slept == []


def test_backend_that_handles_rate_limits_is_not_retried_again():
    """internal/ollama 는 HTTP 레벨에서 이미 429 를 처리한다.

    그 예외 메시지에 '429' 가 들어 있어, 래퍼가 또 재시도하면 5회×60초가 **두 겹**으로
    쌓인다(최악 10분). 스로틀은 적용하되 사후 재시도는 안쪽에 맡긴다.
    """
    clock = FakeClock()
    chat = FakeChat(errors=[RuntimeError("요청 한도(429)로 5회 재시도 후에도 실패")])
    traced = _wrap(chat, clock=clock, handled=True)

    with pytest.raises(RuntimeError):
        traced.complete("s", "u")
    assert chat.calls == 1
    assert clock.slept == []


def test_wrapper_does_not_alter_arguments_or_result():
    """래퍼가 끼어들어도 판정 결과는 바이트 단위로 같아야 한다."""
    clock = FakeClock()

    seen = {}

    class Recorder(FakeChat):
        def complete(self, system, user, *, temperature=0.0):
            seen.update(system=system, user=user, temperature=temperature)
            return "원본 응답"

    traced = _wrap(Recorder(), clock=clock)
    assert traced.complete("시스템", "사용자", temperature=0.7) == "원본 응답"
    assert seen == {"system": "시스템", "user": "사용자", "temperature": 0.7}


def test_other_attributes_are_delegated():
    """백엔드에 따라 chat 과 embed 가 같은 객체다 — 위임하지 않으면 임베딩이 깨진다."""
    clock = FakeClock()
    chat = FakeChat()
    chat.some_attr = "값"
    traced = _wrap(chat, clock=clock)
    assert traced.some_attr == "값"


# --------------------------------------------------------------------------- #
# 4) 임베딩도 같은 한도를 먹는다
# --------------------------------------------------------------------------- #
def test_embed_is_throttled_by_the_same_limiter():
    """chat 과 embed 는 같은 API 키를 쓰므로 한도를 **공유**한다.

    따로 세면 합계가 한도의 두 배가 되어 스로틀이 무의미해진다.
    """
    from contentcompare.llm.ratelimit import (
        RateLimitedChat,
        RateLimitedEmbedder,
        RateLimiter,
    )

    clock = FakeClock()
    limiter = RateLimiter(max_per_minute=2, clock=clock.time, sleep=clock.sleep)
    backend = FakeChat()
    chat = RateLimitedChat(backend, limiter=limiter, wait=60.0, max_retries=5,
                           inner_handles_rate_limit=False, sleep=clock.sleep)
    emb = RateLimitedEmbedder(backend, limiter=limiter, wait=60.0, max_retries=5,
                              inner_handles_rate_limit=False, sleep=clock.sleep)

    chat.complete("s", "u")
    emb.embed(["a"])
    emb.embed(["b"])               # 3번째 → 한도 초과로 대기해야 한다
    assert clock.slept and clock.slept[0] > 0


def test_embedder_retries_rate_limit_errors_too():
    from contentcompare.llm.ratelimit import RateLimitedEmbedder, RateLimiter

    clock = FakeClock()
    backend = FakeChat(errors=[FakeRateLimitError()])
    emb = RateLimitedEmbedder(
        backend, limiter=RateLimiter(0, clock=clock.time, sleep=clock.sleep),
        wait=60.0, max_retries=5, inner_handles_rate_limit=False, sleep=clock.sleep)

    assert emb.embed(["a"]) == [[0.1, 0.2]]
    assert clock.slept == [60.0]


# --------------------------------------------------------------------------- #
# 5) factory 배선
# --------------------------------------------------------------------------- #
def _cfg(**llm_kw) -> AppConfig:
    base = {"backend": "ollama", "embed_backend": "fastembed"}
    base.update(llm_kw)
    return AppConfig.from_dict({"llm": base})


def test_config_reads_the_new_keys():
    cfg = _cfg(max_calls_per_minute=55, rate_limit_status_codes=[429, 503],
               rate_limit_markers=["호출량 초과"])
    assert cfg.llm.max_calls_per_minute == 55
    assert cfg.llm.rate_limit_status_codes == [429, 503]
    assert cfg.llm.rate_limit_markers == ["호출량 초과"]


def test_default_is_off():
    """켜지 않은 사람에게 동작 변화가 0 이어야 한다."""
    assert _cfg().llm.max_calls_per_minute == 0


def test_build_clients_returns_untouched_objects_when_off(monkeypatch):
    """스로틀이 꺼져 있으면 **동일 객체** — Langfuse 배선이 지키는 규약과 같다.

    타임라인도 함께 끈다. 그쪽이 켜져 있으면 chat 이 ``TracedChat`` 으로 감싸여
    이 테스트가 보려는 "스로틀 래퍼가 붙었는가"를 가린다.
    """
    from contentcompare.llm import factory

    sentinel_chat, sentinel_embed = object(), object()
    monkeypatch.setattr(factory, "_make",
                        lambda backend, llm: sentinel_chat if backend == "ollama"
                        else sentinel_embed)

    config = _cfg()
    config.logging.timeline = False
    chat, emb = factory.build_clients(config)
    assert chat is sentinel_chat and emb is sentinel_embed


def test_build_clients_wraps_both_when_enabled(monkeypatch):
    from contentcompare.llm import factory
    from contentcompare.llm.ratelimit import RateLimitedChat, RateLimitedEmbedder

    monkeypatch.setattr(factory, "_make", lambda backend, llm: FakeChat())
    chat, emb = factory.build_clients(
        _cfg(backend="langchain", embed_backend="langchain",
             max_calls_per_minute=55))

    assert isinstance(chat, RateLimitedChat)
    assert isinstance(emb, RateLimitedEmbedder)
    assert chat._limiter is emb._limiter          # 한도를 공유해야 한다


def test_local_embedding_backend_is_not_throttled(monkeypatch):
    """fastembed/onnx 는 로컬이라 사내 한도를 안 먹는다 — 예산을 낭비하면 안 된다."""
    from contentcompare.llm import factory
    from contentcompare.llm.ratelimit import RateLimitedEmbedder

    monkeypatch.setattr(factory, "_make", lambda backend, llm: FakeChat())
    _, emb = factory.build_clients(
        _cfg(backend="langchain", embed_backend="fastembed",
             max_calls_per_minute=55))

    assert not isinstance(emb, RateLimitedEmbedder)


def test_backends_with_builtin_handling_are_flagged():
    """http.py 를 타는 백엔드는 스스로 429 를 처리한다는 사실이 코드에 드러나야 한다."""
    from contentcompare.llm.internal import InternalBackend
    from contentcompare.llm.ollama import OllamaBackend

    assert InternalBackend.handles_rate_limit is True
    assert OllamaBackend.handles_rate_limit is True


def test_langchain_backend_is_not_flagged():
    """langchain 은 사후 대기가 없다 — 래퍼가 담당해야 한다."""
    from contentcompare.llm.langchain_backend import LangChainBackend

    assert getattr(LangChainBackend, "handles_rate_limit", False) is False
