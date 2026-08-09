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

_VALID = "ollama | internal | langchain | fastembed/onnx(embed 전용)"


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
    if backend in ("onnx", "local"):
        from .local_onnx import LocalOnnxEmbedding

        return LocalOnnxEmbedding(llm)
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

    # LLM 입출력 추적(Langfuse). **꺼져 있으면 import 조차 하지 않는다** — 코어
    # 의존성 최소 정책을 지키고, 설정을 건드리지 않은 사용자에게 변화가 0 이도록
    # 오늘과 **동일 객체**를 그대로 돌려준다.
    #
    # chat 만 감싼다(embed_obj 는 그대로). 같은 객체인 백엔드에서도 임베딩 호출은
    # TracedChat.__getattr__ 위임으로 원래 구현에 그대로 닿는다.
    if llm.langfuse.is_active() or llm.trace_local:
        from .tracing import wrap_chat

        chat_obj = wrap_chat(chat_obj, config)

    # 요청 한도 대응은 **가장 바깥**에 둔다. 추적보다 바깥이라 대기 시간이
    # TracedChat 의 duration_ms 에 섞이지 않는다(지연 통계 오염 방지).
    # 꺼져 있으면 감싸지 않아 오늘과 **동일 객체**를 돌려준다.
    if llm.max_calls_per_minute > 0:
        chat_obj, embed_obj = _wrap_rate_limited(chat_obj, embed_obj, llm, embed_backend)
    return chat_obj, embed_obj


def _wrap_rate_limited(chat_obj, embed_obj, llm: LLMConfig, embed_backend: str):
    """분당 호출 한도 스로틀 + 사후 재시도를 입힌다.

    chat 과 embed 는 **같은 API 키를 쓰므로 서버가 합쳐서 센다** — limiter 하나를
    공유해야 한도가 실제로 지켜진다. 단 로컬 임베딩(fastembed/onnx)은 사내 한도를
    먹지 않으므로 예산을 낭비하지 않도록 제외한다.
    """
    from .ratelimit import (
        LOCAL_EMBED_BACKENDS,
        RateLimitedChat,
        RateLimitedEmbedder,
        RateLimiter,
    )

    limiter = RateLimiter(llm.max_calls_per_minute)
    shared = dict(
        limiter=limiter,
        wait=llm.rate_limit_wait,
        max_retries=llm.rate_limit_max_retries,
        status_codes=llm.rate_limit_status_codes or None,
        markers=llm.rate_limit_markers or None,
    )
    chat_obj = RateLimitedChat(
        chat_obj,
        inner_handles_rate_limit=getattr(chat_obj, "handles_rate_limit", False),
        **shared,
    )
    if embed_backend not in LOCAL_EMBED_BACKENDS:
        embed_obj = RateLimitedEmbedder(
            embed_obj,
            inner_handles_rate_limit=getattr(embed_obj, "handles_rate_limit", False),
            **shared,
        )
    return chat_obj, embed_obj
