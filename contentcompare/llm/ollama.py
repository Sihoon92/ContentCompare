"""Ollama 백엔드.

로컬에서 동작하는 Ollama 서버(`/api/chat`, `/api/embeddings`)를 호출한다.
의존성: ``requests`` (지연 import).
"""

from __future__ import annotations

from ..config import LLMConfig


class OllamaBackend:
    """Ollama chat + embedding 백엔드 (LLMClient & EmbeddingClient 동시 구현)."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.host = config.ollama.host.rstrip("/")

    # --- LLMClient -------------------------------------------------------- #
    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> str:
        import requests

        resp = requests.post(
            f"{self.host}/api/chat",
            json={
                "model": self.config.chat_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "options": {"temperature": temperature},
            },
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]

    # --- EmbeddingClient -------------------------------------------------- #
    def embed(self, texts: list[str]) -> list[list[float]]:
        import requests

        vectors: list[list[float]] = []
        for text in texts:
            # TODO: Ollama 가 배치 임베딩을 지원하면 한 번에 보내도록 개선.
            resp = requests.post(
                f"{self.host}/api/embeddings",
                json={"model": self.config.embed_model, "prompt": text},
                timeout=self.config.timeout,
            )
            resp.raise_for_status()
            vectors.append(resp.json()["embedding"])
        return vectors
