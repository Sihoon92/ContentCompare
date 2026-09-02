"""LLM 백엔드 공용 HTTP 호출 (재시도 + 타임아웃 + 명확한 에러).

Ollama/사내 백엔드가 공유한다. 일시적 오류(연결 실패, 타임아웃, 5xx, 429)는
지수 백오프로 재시도하고, 그 외 4xx/파싱 오류는 컨텍스트를 담은
:class:`LLMRequestError` 로 즉시 올린다.

테스트를 위해 실제 요청 함수(``poster``)와 대기 함수(``sleep``)를 주입할 수 있다.
"""

from __future__ import annotations

import logging
import time
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger("contentcompare.llm")


class LLMRequestError(RuntimeError):
    """LLM/임베딩 HTTP 호출 실패(재시도 소진 또는 비재시도 오류)."""


class TransientError(Exception):
    """재시도 대상 일시 오류(내부용 + 테스트 주입용)."""


class RateLimitError(TransientError):
    """요청 한도 초과(HTTP 429). 다른 일시오류보다 더 오래 기다렸다 재시도한다.

    ``retry_after`` 가 있으면(서버가 ``Retry-After`` 헤더로 알려준 대기 초) 그 값을,
    없으면 :class:`RetryPolicy.rate_limit_wait` 를 대기 시간으로 쓴다.
    """

    def __init__(self, message: str, *, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class RetryPolicy:
    max_retries: int = 3
    backoff_base: float = 2.0   # 2s, 4s, 8s, ...
    backoff_cap: float = 16.0
    # --- 요청 한도(429) 전용 정책 ---------------------------------------- #
    rate_limit_wait: float = 60.0
    """429(요청 한도) 응답 시 기본 대기 시간(초). 사내 LLM 분당 한도 회복용."""
    rate_limit_max_retries: int = 5
    """429 전용 재시도 횟수(일반 일시오류와 별도 예산)."""


def _transient_types() -> tuple:
    """재시도로 간주할 예외 타입들(requests 미설치 시 TransientError 만)."""
    try:
        import requests  # noqa: WPS433 - 지연 import

        return (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.ChunkedEncodingError,
            TransientError,
        )
    except Exception:  # pragma: no cover - requests 는 정식 의존성
        return (TransientError,)


def _default_poster(url, **kwargs):  # pragma: no cover - 네트워크 의존
    import requests  # noqa: WPS433

    return requests.post(url, **kwargs)


def post_json(
    url: str,
    payload: dict,
    *,
    headers: Optional[dict] = None,
    timeout: float = 120.0,
    verify: bool = True,
    retry: Optional[RetryPolicy] = None,
    proxy_ctx: Optional[Callable[[], Any]] = None,
    poster: Optional[Callable[..., Any]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """JSON POST 후 파싱한 dict 를 반환. 실패 시 :class:`LLMRequestError`.

    Args:
        proxy_ctx: 호출당 컨텍스트 매니저를 만드는 무인자 콜러블(예: ``no_proxy``).
        poster:    요청 함수(기본 ``requests.post``). 테스트 시 주입.
        sleep:     백오프 대기 함수(테스트 시 no-op 주입).
    """
    retry = retry or RetryPolicy()
    poster = poster or _default_poster
    transient = _transient_types()
    make_ctx = proxy_ctx or nullcontext
    attempt = 0          # 일반 일시오류(연결/타임아웃/5xx) 재시도 카운트
    rl_attempt = 0       # 429(요청 한도) 전용 재시도 카운트

    logger.debug("POST %s payload=%s", url, _snippet_obj(payload, 1000))
    while True:
        try:
            with make_ctx():
                resp = poster(
                    url, json=payload, headers=headers, timeout=timeout, verify=verify
                )
            status = getattr(resp, "status_code", 200)
            if status == 429:
                raise RateLimitError(
                    f"HTTP 429 요청 한도 초과 — {_snippet(resp)}",
                    retry_after=_retry_after_seconds(resp),
                )
            if status >= 500:
                raise TransientError(f"HTTP {status} — {_snippet(resp)}")
            if status >= 400:
                raise LLMRequestError(
                    f"{url} 호출 실패: HTTP {status} — {_snippet(resp)}"
                )
            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001 - 본문 파싱 실패
                raise LLMRequestError(
                    f"{url} 응답 JSON 파싱 실패: {_snippet(resp)}"
                ) from exc
            logger.debug("← %s ok: %s", url, _snippet_obj(data, 1000))
            return data
        except RateLimitError as exc:
            # 429 는 분당 한도일 가능성이 높아 1분가량 길게 기다렸다가 재시도한다.
            rl_attempt += 1
            if rl_attempt > retry.rate_limit_max_retries:
                raise LLMRequestError(
                    f"{url} 호출이 요청 한도(429)로 {retry.rate_limit_max_retries}회 "
                    f"재시도 후에도 실패: {exc}"
                ) from exc
            delay = exc.retry_after if exc.retry_after else retry.rate_limit_wait
            logger.warning(
                "%s 요청 한도(429) — %.0fs 대기 후 재시도 %d/%d",
                url, delay, rl_attempt, retry.rate_limit_max_retries,
            )
            _emit_retry("rate_limit", delay, rl_attempt,
                        retry.rate_limit_max_retries, exc)
            sleep(delay)
        except transient as exc:
            attempt += 1
            if attempt > retry.max_retries:
                raise LLMRequestError(
                    f"{url} 호출이 {retry.max_retries}회 재시도 후에도 실패: {exc}"
                ) from exc
            delay = min(retry.backoff_base ** attempt, retry.backoff_cap)
            logger.warning(
                "%s 일시 오류(%s) — %.0fs 후 재시도 %d/%d",
                url, exc, delay, attempt, retry.max_retries,
            )
            _emit_retry("error", delay, attempt, retry.max_retries, exc)
            sleep(delay)


def _emit_retry(status: str, delay: float, attempt: int, limit: int,
                exc: BaseException) -> None:
    """재시도를 타임라인에 남긴다 — ``internal``/``ollama``(requests) 경로용.

    ``langchain`` 은 SDK 가 자체 재시도를 하므로 :mod:`.http_probe` 가 같은 일을
    전송 계층에서 한다. **두 경로가 같은 축에 놓여야** 백엔드를 바꿔도 타임라인을
    읽는 법이 달라지지 않는다.
    """
    from .. import timeline
    from .tracing import current_stage

    timeline.emit(
        timeline.RETRY, current_stage(depth=1), status=status,
        attempt=attempt, max=limit, wait_seconds=round(float(delay), 1),
        error=f"{type(exc).__name__}: {exc}"[:200],
    )


def extract(data: dict, *path, url: str = "") -> Any:
    """중첩 키/인덱스 경로를 안전하게 추출. 실패 시 명확한 에러."""
    cur: Any = data
    try:
        for key in path:
            cur = cur[key]
        return cur
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMRequestError(
            f"{url or '응답'} 형식이 예상과 다릅니다(경로 {list(path)}): {_snippet_obj(data)}"
        ) from exc


def _retry_after_seconds(resp: Any) -> Optional[float]:
    """``Retry-After`` 헤더(초 단위 정수)를 읽어 float 로 반환. 없거나 파싱 실패 시 None.

    HTTP-date 형식은 사내 LLM 에서 드물어 다루지 않는다(그 경우 기본 대기로 폴백).
    """
    headers = getattr(resp, "headers", None) or {}
    try:
        value = headers.get("Retry-After") or headers.get("retry-after")
    except AttributeError:
        return None
    if value is None:
        return None
    try:
        return max(0.0, float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _snippet(resp: Any, n: int = 200) -> str:
    text = getattr(resp, "text", "") or ""
    return text[:n].replace("\n", " ")


def _snippet_obj(obj: Any, n: int = 200) -> str:
    return str(obj)[:n].replace("\n", " ")
