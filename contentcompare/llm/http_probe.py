"""httpx 요청/응답 훅 — **SDK 내부 재시도**를 타임라인에 드러낸다.

이 파일이 있는 이유는 실측 하나다. ``langchain`` 백엔드는 openai SDK 가
``max_retries`` 만큼 자체 재시도를 하는데, 그 4회(120초 × 4 = 8분)가
:class:`~contentcompare.llm.tracing.TracedChat` 에는 **``duration_ms`` 하나로 뭉쳐**
보였다. 화면에는 아무것도 안 나오고 8분 뒤 ``APITimeoutError`` 만 떨어졌다 —
"멈춘 것인가 재시도 중인가"를 구분할 방법이 없었다.

**왜 훅인가.** SDK 재시도는 **새 HTTP 요청**이다. 그래서 전송 계층에 훅을 달면 SDK 를
고치지 않고도 회차가 정확히 잡힌다. 대안이던 "openai/httpx 로거 열기"는 소켓·커넥션풀
로그까지 함께 쏟아져(그래서 ``NOISY_LOGGERS`` 에 막아 둔 것이다) 타임라인 파일에도
안 실린다.

⚠️ **응답 훅은 타임아웃에 불리지 않는다.** httpx 의 ``response`` 훅은 응답이 실제로
왔을 때만 실행되고, ``ReadTimeout`` 은 예외로 빠져나가 훅을 건너뛴다. 그래서 재시도의
증거는 "응답 없이 **다음 요청이 왔다**"는 사실이다 — :meth:`HttpProbe.on_request` 가
직전 요청의 미완결을 보고 재시도를 기록하는 구조가 여기서 나온다.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from .. import timeline
from .tracing import current_stage

logger = logging.getLogger("contentcompare.llm.http_probe")

#: 이 상태코드 이상은 실패로 본다(타임라인 라벨 전용 — 재시도 결정은 SDK/`http.py` 가 한다).
_ERROR_FROM = 400


class HttpProbe:
    """httpx 클라이언트 하나의 요청 흐름을 지켜본다.

    상태를 갖는 것은 **회차를 세기 위해서**다. 요청 하나만 봐서는 그것이 최초인지
    재시도인지 알 수 없고, 그 구분이 이 모듈의 존재 이유다.

    스레드 안전을 노리지 않는다 — fact 파이프라인은 LLM 호출을 순차로 하고, 어긋나도
    잃는 것은 회차 숫자의 정확도뿐이다(기록이 실행을 방해하지 않는다는 원칙이 우선).
    """

    def __init__(
        self,
        *,
        max_retries: int = 0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_retries = max_retries
        self._clock = clock
        self._pending: Optional[float] = None
        self._attempt = 0

    # ------------------------------------------------------------------ #
    def on_request(self, request: Any) -> None:
        """요청 직전. 직전 요청이 응답 없이 끝났다면 그것이 곧 재시도의 증거다."""
        try:
            now = self._clock()
            if self._pending is not None:
                self._attempt += 1
                timeline.emit(
                    timeline.RETRY, current_stage(depth=1),
                    status="timeout",
                    duration_ms=int((now - self._pending) * 1000),
                    attempt=self._attempt,
                    max=self.max_retries or None,
                    error="응답 없음(전송 실패·타임아웃)",
                )
            else:
                self._attempt = 0
            self._pending = now
        except Exception as exc:  # noqa: BLE001 — 훅이 요청을 막으면 안 된다
            logger.debug("요청 훅 무시: %s", exc)

    def on_response(self, response: Any) -> None:
        """응답 도착. 여기까지 왔다는 것 자체가 '전송은 됐다'는 뜻이다."""
        try:
            started, self._pending = self._pending, None
            code = int(getattr(response, "status_code", 0) or 0)
            took = int((self._clock() - started) * 1000) if started is not None else 0
            timeline.emit(
                timeline.HTTP, current_stage(depth=1),
                status=_status_for(code), duration_ms=took,
                status_code=code or None,
                attempt=(self._attempt + 1) if self._attempt else None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("응답 훅 무시: %s", exc)


def _status_for(code: int) -> str:
    if code == 429:
        return "rate_limit"
    if code >= _ERROR_FROM:
        return "error"
    return "ok"


def hooks(max_retries: int = 0) -> dict[str, list]:
    """httpx ``event_hooks`` 인자로 바로 넘길 수 있는 형태."""
    probe = HttpProbe(max_retries=max_retries)
    return {"request": [probe.on_request], "response": [probe.on_response]}
