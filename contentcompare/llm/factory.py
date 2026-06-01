"""설정 기반 LLM/임베딩 백엔드 선택기.

``config.llm.backend`` 값(``ollama`` | ``internal``)에 따라 적절한 백엔드를
생성한다. 두 백엔드 모두 LLMClient 와 EmbeddingClient 를 동시에 구현하므로
같은 객체를 (chat, embed) 양쪽으로 반환한다.
"""

from __future__ import annotations

from ..config import AppConfig
from .base import EmbeddingClient, LLMClient
from .internal import InternalBackend
from .ollama import OllamaBackend


def build_clients(config: AppConfig) -> tuple[LLMClient, EmbeddingClient]:
    """(chat_client, embedding_client) 튜플을 반환한다."""
    backend = config.llm.backend.lower()
    if backend == "ollama":
        obj = OllamaBackend(config.llm)
    elif backend == "internal":
        obj = InternalBackend(config.llm)
    elif backend == "langchain":
        from .langchain_backend import LangChainBackend

        obj = LangChainBackend(config.llm)
    else:
        raise ValueError(
            f"알 수 없는 LLM backend: {config.llm.backend!r} (ollama|internal|langchain)"
        )
    return obj, obj
