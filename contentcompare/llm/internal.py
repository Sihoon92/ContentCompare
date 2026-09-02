"""사내(in-house) HTTP LLM 백엔드.

OpenAI 호환 엔드포인트(`/chat/completions`, `/embeddings`)를 가정한다.
사내망 직결을 위해 호출 직전 :func:`contentcompare.config.no_proxy` 로
HTTP(S)_PROXY 를 비운다(설정 ``internal.unset_proxy``). (기획 1번)

재시도/타임아웃/에러 처리는 :mod:`contentcompare.llm.http` 가 담당한다.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from typing import Any, Callable, Optional

from ..config import LLMConfig, no_proxy
from .http import RetryPolicy, extract, post_json
from .usage import UNKNOWN, Usage, from_response

logger = logging.getLogger("contentcompare.llm")

_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


class InternalBackend:
    """사내 OpenAI 호환 백엔드 (LLMClient & EmbeddingClient 동시 구현)."""

    handles_rate_limit = True
    """429 를 HTTP 레벨(:mod:`.http`)에서 직접 처리한다 — Retry-After 까지 읽는다.

    :mod:`.ratelimit` 래퍼가 이 플래그를 보고 **사후 재시도를 건너뛴다**. 안 그러면
    예산 소진 메시지("요청 한도(429)로 …")를 래퍼가 다시 한도로 인식해 5회×60초가
    두 겹으로 쌓인다. 사전 스로틀은 그대로 적용된다.
    """

    last_usage: Usage = UNKNOWN
    """마지막 ``complete()`` 의 토큰 사용량. 서버가 안 주면 미상으로 남는다.

    **반환값 대신 속성인 이유**: ``LLMClient.complete`` 의 서명(``-> str``)을 바꾸면
    ``comparison/``·``readers/`` 까지 파급되는데 그쪽은 코드 무수정 원칙이다.
    :class:`~contentcompare.llm.tracing.TracedChat` 이 호출 직후 이 값을 읽어
    타임라인에 얹는다 — ``handles_rate_limit`` 과 같은 덕 타이핑 규약이다.
    """

    def __init__(
        self,
        config: LLMConfig,
        *,
        poster: Optional[Callable[..., Any]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.base_url = config.internal.base_url.rstrip("/")
        self._poster = poster
        self._sleep = sleep

    # --- 프록시 정책 ------------------------------------------------------- #
    def _proxy_ctx(self):
        if self.config.internal.log_proxy:
            active = {k: os.environ.get(k) for k in _PROXY_VARS if os.environ.get(k)}
            logger.info("사내 호출 전 프록시 env: %s (unset_proxy=%s)",
                        active or "(없음)", self.config.internal.unset_proxy)
        if self.config.internal.unset_proxy:
            return no_proxy()
        return contextlib.nullcontext()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        # 직접 지정한 api_key 우선, 없으면 환경변수에서 읽는다.
        api_key = self.config.internal.api_key or os.environ.get(
            self.config.internal.api_key_env, ""
        )
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _retry(self) -> RetryPolicy:
        return RetryPolicy(
            max_retries=self.config.max_retries,
            backoff_base=self.config.backoff_base,
            rate_limit_wait=self.config.rate_limit_wait,
            rate_limit_max_retries=self.config.rate_limit_max_retries,
        )

    def _post(self, url: str, payload: dict) -> dict:
        return post_json(
            url,
            payload,
            headers=self._headers(),
            timeout=self.config.timeout,
            verify=self.config.internal.verify_ssl,
            retry=self._retry(),
            proxy_ctx=self._proxy_ctx,
            poster=self._poster,
            sleep=self._sleep,
        )

    # --- LLMClient -------------------------------------------------------- #
    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> str:
        self.last_usage = UNKNOWN  # 이유는 :attr:`last_usage` 참고
        url = f"{self.base_url}/chat/completions"
        data = self._post(
            url,
            {
                "model": self.config.chat_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
            },
        )
        self.last_usage = from_response(data)
        return extract(data, "choices", 0, "message", "content", url=url)

    # --- EmbeddingClient -------------------------------------------------- #
    def embed(self, texts: list[str], *, kind: str = "passage") -> list[list[float]]:
        url = f"{self.base_url}/embeddings"
        prefix = self.config.embed_prefix_for(kind)
        if prefix:
            texts = [prefix + t for t in texts]
        data = self._post(url, {"model": self.config.embed_model, "input": texts})
        items = extract(data, "data", url=url)
        # input 순서 유지를 위해 index 로 정렬.
        items = sorted(items, key=lambda d: d.get("index", 0))
        return [extract(d, "embedding", url=url) for d in items]
