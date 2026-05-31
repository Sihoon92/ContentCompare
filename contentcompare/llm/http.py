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


@dataclass
class RetryPolicy:
    max_retries: int = 3
    backoff_base: float = 2.0   # 2s, 4s, 8s, ...
    backoff_cap: float = 16.0


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
    attempt = 0

    while True:
        try:
            with make_ctx():
                resp = poster(
                    url, json=payload, headers=headers, timeout=timeout, verify=verify
                )
            status = getattr(resp, "status_code", 200)
            if status >= 500 or status == 429:
                raise TransientError(f"HTTP {status}")
            if status >= 400:
                raise LLMRequestError(
                    f"{url} 호출 실패: HTTP {status} — {_snippet(resp)}"
                )
            try:
                return resp.json()
            except Exception as exc:  # noqa: BLE001 - 본문 파싱 실패
                raise LLMRequestError(
                    f"{url} 응답 JSON 파싱 실패: {_snippet(resp)}"
                ) from exc
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
            sleep(delay)


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


def _snippet(resp: Any, n: int = 200) -> str:
    text = getattr(resp, "text", "") or ""
    return text[:n].replace("\n", " ")


def _snippet_obj(obj: Any, n: int = 200) -> str:
    return str(obj)[:n].replace("\n", " ")
