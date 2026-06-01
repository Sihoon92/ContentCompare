"""설정 기반 LLM/임베딩 백엔드 선택기.

``config.llm.backend`` 로 chat 백엔드를, ``config.llm.embed_backend`` 로 임베딩
백엔드를 고른다. embed_backend 가 비어 있으면 chat 과 동일 백엔드를 쓴다.

사내 chat 엔드포인트가 임베딩을 제공하지 않을 때, chat=internal/langchain +
embed=fastembed(로컬) 처럼 분리할 수 있다.
"""

from __future__ import annotations

from ..config import AppConfig, LLMConfig, disable_proxy
from .base import EmbeddingClient, LLMClient
from .internal import InternalBackend
from .ollama import OllamaBackend

_VALID = "ollama | internal | langchain | fastembed(embed 전용)"


def _make(backend: str, llm: LLMConfig):
    """백엔드 이름 → 백엔드 객체(chat/embed 동시 구현, fastembed 는 embed 전용)."""
    backend = backend.lower()
    if backend == "ollama":
        return OllamaBackend(llm)
    if backend == "internal":
        return InternalBackend(llm)
    if backend == "langchain":
        from .langchain_backend import LangChainBackend

        return LangChainBackend(llm)
    if backend == "fastembed":
        from .fastembed_backend import FastEmbedBackend

        return FastEmbedBackend(llm)
    raise ValueError(f"알 수 없는 LLM backend: {backend!r} ({_VALID})")


def build_clients(config: AppConfig) -> tuple[LLMClient, EmbeddingClient]:
    """(chat_client, embedding_client) 튜플을 반환한다."""
    llm = config.llm
    backend = llm.backend.lower()
    embed_backend = (llm.embed_backend or backend).lower()

    # 사내망 직결 설정이면 프로세스 전역에서 프록시를 영구히 비운다(복원 없음).
    if "internal" in (backend, embed_backend) or "langchain" in (backend, embed_backend):
        if llm.internal.unset_proxy:
            disable_proxy()

    chat_obj = _make(backend, llm)
    # 임베딩 백엔드가 같으면 같은 객체를, 다르면 별도로 생성.
    embed_obj = chat_obj if embed_backend == backend else _make(embed_backend, llm)
    return chat_obj, embed_obj
