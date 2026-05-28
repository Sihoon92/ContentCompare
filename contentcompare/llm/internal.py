"""사내(in-house) HTTP LLM 백엔드.

OpenAI 호환 엔드포인트(`/chat/completions`, `/embeddings`)를 가정한다.
사내망 직결을 위해 호출 직전 :func:`contentcompare.config.no_proxy` 로
HTTP(S)_PROXY 를 비운다(설정 ``internal.unset_proxy``). (기획 1번)
"""

from __future__ import annotations

import contextlib
import os

from ..config import LLMConfig, no_proxy


class InternalBackend:
    """사내 OpenAI 호환 백엔드 (LLMClient & EmbeddingClient 동시 구현)."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.base_url = config.internal.base_url.rstrip("/")

    # --- 프록시 정책 ------------------------------------------------------- #
    def _proxy_ctx(self):
        if self.config.internal.unset_proxy:
            return no_proxy()
        return contextlib.nullcontext()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        api_key = os.environ.get(self.config.internal.api_key_env, "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    # --- LLMClient -------------------------------------------------------- #
    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> str:
        import requests

        with self._proxy_ctx():
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json={
                    "model": self.config.chat_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": temperature,
                },
                timeout=self.config.timeout,
                verify=self.config.internal.verify_ssl,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    # --- EmbeddingClient -------------------------------------------------- #
    def embed(self, texts: list[str]) -> list[list[float]]:
        import requests

        with self._proxy_ctx():
            resp = requests.post(
                f"{self.base_url}/embeddings",
                headers=self._headers(),
                json={"model": self.config.embed_model, "input": texts},
                timeout=self.config.timeout,
                verify=self.config.internal.verify_ssl,
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            # input 순서 유지를 위해 index 로 정렬.
            data.sort(key=lambda d: d.get("index", 0))
            return [d["embedding"] for d in data]
