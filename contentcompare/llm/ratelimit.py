"""사내 LLM 요청 한도(분당 N회) 대응 — 사전 스로틀 + 사후 대기.

사내 엔드포인트는 분당 호출 한도가 흔하고(실측 60회/분), fact 엔진은 실행 한 번에
수백 회를 부른다. 한도에 걸리는 것이 예외가 아니라 **상시 조건**이므로 두 층으로 막는다:

1. :class:`RateLimiter` — **사전**. 애초에 한도를 넘지 않게 호출 페이스를 조절.
2. :class:`RateLimitedChat` / :class:`RateLimitedEmbedder` — **사후**. 그래도 걸리면
   한도가 회복될 만큼 기다렸다 재시도.

**왜 백엔드가 아니라 래퍼인가.** :func:`~contentcompare.llm.factory.build_clients` 가
클라이언트를 만드는 유일한 지점이라, 여기서 감싸면 백엔드 코드를 건드리지 않고 전
경로가 덮인다(:class:`~contentcompare.llm.tracing.TracedChat` 과 같은 전략). 게다가
**추적보다 바깥**에 두므로 대기 시간이 ``duration_ms`` 에 섞이지 않아 Langfuse 의
지연 통계가 오염되지 않는다.

**이중 재시도 금지.** ``internal``/``ollama`` 는 이미 HTTP 레벨에서 429 를 처리하고
(:mod:`contentcompare.llm.http`) 예산 소진 시 메시지에 "429" 를 담아 올린다. 래퍼가
그것을 다시 재시도하면 5회×60초가 두 겹으로 쌓여 최악 10분을 버린다. 그래서 백엔드가
``handles_rate_limit = True`` 를 선언하면 **사후 재시도만** 건너뛴다(스로틀은 적용).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any, Callable, Iterable, Optional

from .. import timeline
from ..logging_setup import log_print
from .http import _retry_after_seconds
from .tracing import current_stage

logger = logging.getLogger("contentcompare.llm.ratelimit")

DEFAULT_STATUS_CODES: tuple[int, ...] = (429,)
DEFAULT_MARKERS: tuple[str, ...] = (
    "rate limit", "too many requests", "quota", "요청 한도",
)

LOCAL_EMBED_BACKENDS = ("fastembed", "onnx", "local")
"""로컬에서 도는 임베딩 백엔드 — 사내 한도를 먹지 않으므로 스로틀 대상이 아니다."""


# --------------------------------------------------------------------------- #
# 사전 스로틀
# --------------------------------------------------------------------------- #
class RateLimiter:
    """슬라이딩 윈도우로 "최근 60초에 N회"를 지킨다.

    고정 창(매 분 리셋)이 아니라 슬라이딩 창인 이유: 고정 창은 경계에서 두 배가 몰릴
    수 있다(59초에 60회 + 61초에 60회). 서버가 어느 방식으로 세는지 모르므로 더
    보수적인 쪽을 택한다.

    한도에 닿아도 **창이 열릴 만큼만** 기다린다 — 통째로 60초를 자면 한도는 지키되
    처리량을 불필요하게 버린다.

    ``clock``/``sleep`` 을 주입할 수 있어 테스트가 실제로 기다리지 않는다
    (:mod:`contentcompare.llm.http` 의 sleep 주입과 같은 규약).
    """

    def __init__(
        self,
        max_per_minute: int,
        *,
        window: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._max = int(max_per_minute or 0)
        self._window = window
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()

    @property
    def enabled(self) -> bool:
        return self._max > 0

    def acquire(self) -> None:
        """호출 직전에 부른다. 한도에 닿았으면 창이 열릴 때까지 잔다."""
        if not self.enabled:
            return
        self._prune(self._clock())
        if len(self._calls) >= self._max:
            delay = self._window - (self._clock() - self._calls[0])
            if delay > 0:
                logger.info(
                    "요청 한도 스로틀 — 분당 %d회 유지를 위해 %.1fs 대기",
                    self._max, delay,
                )
                # 타임라인에도 남긴다 — 이 정지는 정상 동작인데, 기록이 없으면
                # "왜 갑자기 느려졌나"를 설명할 수 없어 행(hang)으로 오해된다.
                timeline.emit(
                    timeline.WAIT, current_stage(depth=1), status="rate_limit",
                    seconds=round(delay, 1), reason="요청 한도 스로틀",
                    limit=self._max,
                )
                self._sleep(delay)
            self._prune(self._clock())
        self._calls.append(self._clock())

    def _prune(self, now: float) -> None:
        while self._calls and now - self._calls[0] >= self._window:
            self._calls.popleft()


# --------------------------------------------------------------------------- #
# 한도 예외 감지
# --------------------------------------------------------------------------- #
def is_rate_limit(
    exc: BaseException,
    *,
    status_codes: Optional[Iterable[int]] = None,
    markers: Optional[Iterable[str]] = None,
) -> bool:
    """이 예외가 "요청 한도 초과"인가.

    ``openai`` 를 import 하지 않고 **덕 타이핑**으로 판단한다(코어 의존성 최소 정책 —
    백엔드가 langchain 이든 requests 든 같은 코드로 다뤄야 한다). 근거 세 가지:

    1. 상태코드 — ``exc.status_code`` 또는 ``exc.response.status_code``
    2. 클래스명에 ``RateLimit`` 포함 — openai SDK 의 ``RateLimitError``
    3. 메시지에 마커 포함 — 상태코드 없이 본문으로만 알리는 게이트웨이용

    과하게 넓히면 진짜 오류를 1분씩 재시도하며 숨기므로, 기본 마커는 좁게 둔다.
    """
    codes = tuple(status_codes) if status_codes is not None else DEFAULT_STATUS_CODES
    words = tuple(markers) if markers is not None else DEFAULT_MARKERS

    status = _status_of(exc)
    if status is not None and status in codes:
        return True
    if "ratelimit" in type(exc).__name__.replace("_", "").lower():
        return True
    text = str(exc).lower()
    return any(str(w).lower() in text for w in words)


#: 타임아웃 메시지 마커. 클래스명 판정이 주(主)이고 이쪽은 보조다.
_TIMEOUT_MARKERS = ("timed out", "timeout", "read timed out", "시간 초과")


def is_timeout(exc: BaseException) -> bool:
    """이 예외가 "응답을 기다리다 끊겼다"인가.

    ``is_rate_limit`` 과 **갈라 두는 이유**가 있다. ``APITimeoutError`` 는 상태코드도
    ``response`` 도 없고 메시지가 ``"Request timed out."`` 뿐이라 한도 감지의 세 근거
    (상태코드·클래스명·마커) 어디에도 안 걸린다. 그렇다고 한도 마커에 ``timeout`` 을
    끼워 넣으면 **원인이 다른 둘이 한 예산·한 대기로 뭉쳐** "왜 60초를 기다렸나"를
    설명할 수 없게 된다 — 한도는 기다리면 풀리고, 생성 지연은 기다려도 안 풀린다.

    판정은 **클래스명이 주(主)** 다. ``APITimeoutError``(openai) ·
    ``ReadTimeout``(httpx) · ``Timeout``(requests) · ``TimeoutError``(내장)가 전부
    이름에 ``timeout`` 을 담고 있어, 백엔드를 바꿔도 같은 코드로 잡힌다
    (:mod:`contentcompare.llm.http` 를 쓰든 SDK 를 쓰든).

    ⚠️ :func:`contentcompare.timeline.classify_error` 에도 비슷한 판정이 있지만 그쪽은
    **기록에 붙일 이름**을 고르는 것이고 이쪽은 **60초를 기다릴지**를 정한다. 둘이
    갈려도 동작에는 영향이 없다.
    """
    if "timeout" in type(exc).__name__.replace("_", "").lower():
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _TIMEOUT_MARKERS)


def retry_after_of(exc: BaseException) -> Optional[float]:
    """예외에 딸린 응답의 ``Retry-After`` 초. 없으면 ``None``.

    헤더 해석 규칙은 :mod:`contentcompare.llm.http` 와 **같아야** 하므로 그 함수를
    그대로 재사용한다 — 두 벌로 갈라지면 한쪽만 고치는 사고가 난다.
    """
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    return _retry_after_seconds(resp)


def _status_of(exc: BaseException) -> Optional[int]:
    for holder in (exc, getattr(exc, "response", None)):
        if holder is None:
            continue
        for attr in ("status_code", "status"):
            value = getattr(holder, attr, None)
            if isinstance(value, int):
                return value
    return None


# --------------------------------------------------------------------------- #
# 사후 대기 래퍼
# --------------------------------------------------------------------------- #
class _RateLimitedBase:
    """스로틀 + 한도 재시도를 입히되 **인자와 반환값은 절대 변형하지 않는다.**

    이 래퍼가 끼어들어도 판정 결과는 바이트 단위로 같아야 한다 —
    :class:`~contentcompare.llm.tracing.TracedChat` 과 같은 계약이다.
    """

    def __init__(
        self,
        inner: Any,
        *,
        limiter: RateLimiter,
        wait: float = 60.0,
        max_retries: int = 5,
        inner_handles_rate_limit: bool = False,
        status_codes: Optional[Iterable[int]] = None,
        markers: Optional[Iterable[str]] = None,
        timeout_wait: float = 0.0,
        timeout_max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._inner = inner
        self._limiter = limiter
        self._wait = wait
        self._max_retries = max_retries
        self._handled = inner_handles_rate_limit
        self._status_codes = status_codes
        self._markers = markers
        self._timeout_wait = float(timeout_wait or 0.0)
        self._timeout_max_retries = int(timeout_max_retries)
        self._sleep = sleep
        self._diagnosed = False
        self._timeout_diagnosed = False

    def _call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        # 두 예산을 **따로** 센다 — 원인이 다르면 회복 조건도 다르고, 섞어 세면
        # "왜 갑자기 포기했나"를 설명할 수 없다.
        rate_attempt = 0
        timeout_attempt = 0
        while True:
            self._limiter.acquire()
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — 해당 없으면 그대로 올려보낸다
                if not self._handled and is_rate_limit(
                    exc, status_codes=self._status_codes, markers=self._markers
                ):
                    rate_attempt += 1
                    if rate_attempt > self._max_retries:
                        raise
                    delay = retry_after_of(exc) or self._wait
                    self._announce(exc, delay, rate_attempt)
                    self._sleep(delay)
                    continue
                # ``_handled`` 는 **429 만** 뜻한다. http.py 의 일시오류 백오프는
                # 2~8초라 분당 한도 회복에 못 미치므로, 타임아웃 대기까지 건너뛰면
                # 이 기능이 internal·ollama 에서 통째로 죽는다.
                if self._timeout_wait > 0 and is_timeout(exc):
                    timeout_attempt += 1
                    if timeout_attempt > self._timeout_max_retries:
                        raise
                    self._announce_timeout(exc, self._timeout_wait, timeout_attempt)
                    self._sleep(self._timeout_wait)
                    continue
                # 감지에 실패한 예외도 남긴다 — 서버가 한도를 다른 형태로 알릴 때
                # 마커를 사후에 좁히려면 실물이 필요하다.
                logger.debug("한도·타임아웃 아님으로 판단한 예외: %s: %s",
                             type(exc).__name__, exc)
                raise

    def _announce(self, exc: BaseException, delay: float, attempt: int) -> None:
        """60초 정지가 행(hang)으로 오해되지 않게 **화면에도** 알린다.

        첫 회에는 예외 타입·상태코드·본문 앞부분까지 남긴다 — 사내 서버가 한도 초과를
        실제로 어떤 형태로 돌려주는지 확인하는 유일한 수단이다.
        """
        timeline.emit(
            timeline.WAIT, current_stage(depth=1), status="rate_limit",
            seconds=round(delay, 1), reason="요청 한도 재시도",
            attempt=attempt, max=self._max_retries,
            error=f"{type(exc).__name__}: {exc}"[:200],
        )
        if not self._diagnosed:
            self._diagnosed = True
            log_print(
                f"⏳ 요청 한도 감지 — {delay:.0f}초 대기 후 재시도 "
                f"({attempt}/{self._max_retries})\n"
                f"   실제 응답: {type(exc).__name__} "
                f"status={_status_of(exc)} {str(exc)[:200]}",
            )
            return
        log_print(f"⏳ 요청 한도 — {delay:.0f}초 대기 후 재시도 "
                  f"({attempt}/{self._max_retries})")

    def _announce_timeout(self, exc: BaseException, delay: float, attempt: int) -> None:
        """타임아웃 대기를 알린다. 첫 회에는 예외 실물까지 남긴다.

        **첫 회 진단이 이 기능의 핵심 산출물이다.** 타임아웃의 원인이 한도인지(기다리면
        풀린다) 생성 지연인지(기다려도 안 풀린다)는 예외만 봐서는 모르고, 재시도가
        실제로 성공하는지로 갈린다 — 그 판단 근거를 여기서 남긴다.
        """
        timeline.emit(
            timeline.WAIT, current_stage(depth=1), status="timeout",
            seconds=round(delay, 1), reason="타임아웃 후 대기",
            attempt=attempt, max=self._timeout_max_retries,
            error=f"{type(exc).__name__}: {exc}"[:200],
        )
        if not self._timeout_diagnosed:
            self._timeout_diagnosed = True
            log_print(
                f"⏳ 타임아웃 — {delay:.0f}초 대기 후 재시도 "
                f"({attempt}/{self._timeout_max_retries})\n"
                f"   실제 예외: {type(exc).__name__} {str(exc)[:200]}\n"
                f"   ※ 재시도가 계속 실패하면 원인은 요청 한도가 아니라 **생성 지연**이다 —"
                f" 배치 크기(fact.record_batch_rows / fact_batch_blocks)를 줄일 것.",
            )
            return
        log_print(f"⏳ 타임아웃 — {delay:.0f}초 대기 후 재시도 "
                  f"({attempt}/{self._timeout_max_retries})")

    # 감싼 객체의 나머지 속성은 그대로 위임한다 — 백엔드에 따라 chat 과 embed 가
    # 같은 객체이므로(InternalBackend), 위임하지 않으면 반대쪽이 깨진다.
    def __getattr__(self, item: str) -> Any:
        if item.startswith("_"):
            raise AttributeError(item)
        return getattr(self._inner, item)


class RateLimitedChat(_RateLimitedBase):
    """chat 클라이언트(:class:`~contentcompare.llm.base.LLMClient` 프로토콜)."""

    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> str:
        return self._call(self._inner.complete, system, user, temperature=temperature)


class RateLimitedEmbedder(_RateLimitedBase):
    """임베딩 클라이언트. **chat 과 같은 limiter 를 공유해야** 한도가 지켜진다.

    같은 API 키를 쓰므로 서버는 둘을 합쳐서 센다 — 따로 세면 합계가 한도의 두 배가
    되어 스로틀이 무의미해진다.
    """

    def embed(self, texts: list[str], *, kind: str = "passage") -> list[list[float]]:
        return self._call(self._inner.embed, texts, kind=kind)
