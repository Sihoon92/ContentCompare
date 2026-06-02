"""Ollama 백엔드.

로컬에서 동작하는 Ollama 서버(`/api/chat`, `/api/embeddings`)를 호출한다.
재시도/타임아웃/에러 처리는 :mod:`contentcompare.llm.http` 가 담당한다.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from ..config import LLMConfig
from .http import RetryPolicy, extract, post_json


class OllamaBackend:
    """Ollama chat + embedding 백엔드 (LLMClient & EmbeddingClient 동시 구현)."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        poster: Optional[Callable[..., Any]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.host = config.ollama.host.rstrip("/")
        self._poster = poster
        self._sleep = sleep

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
            timeout=self.config.timeout,
            retry=self._retry(),
            poster=self._poster,
            sleep=self._sleep,
        )

    # --- LLMClient -------------------------------------------------------- #
    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> str:
        url = f"{self.host}/api/chat"
        data = self._post(
            url,
            {
                "model": self.config.chat_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "options": {"temperature": temperature},
            },
        )
        return extract(data, "message", "content", url=url)

    # --- EmbeddingClient -------------------------------------------------- #
    def embed(self, texts: list[str]) -> list[list[float]]:
        url = f"{self.host}/api/embeddings"
        vectors: list[list[float]] = []
        for text in texts:
            # TODO: Ollama 가 배치 임베딩을 지원하면 한 번에 보내도록 개선.
            data = self._post(url, {"model": self.config.embed_model, "prompt": text})
            vectors.append(extract(data, "embedding", url=url))
        return vectors
