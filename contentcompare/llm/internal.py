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

logger = logging.getLogger("contentcompare.llm")

_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


class InternalBackend:
    """사내 OpenAI 호환 백엔드 (LLMClient & EmbeddingClient 동시 구현)."""

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
            max_retries=self.config.max_retries, backoff_base=self.config.backoff_base
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
        return extract(data, "choices", 0, "message", "content", url=url)

    # --- EmbeddingClient -------------------------------------------------- #
    def embed(self, texts: list[str]) -> list[list[float]]:
        url = f"{self.base_url}/embeddings"
        data = self._post(url, {"model": self.config.embed_model, "input": texts})
        items = extract(data, "data", url=url)
        # input 순서 유지를 위해 index 로 정렬.
        items = sorted(items, key=lambda d: d.get("index", 0))
        return [extract(d, "embedding", url=url) for d in items]
