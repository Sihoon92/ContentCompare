"""설정 기반 LLM/임베딩 백엔드 선택기.

``config.llm.backend`` 로 chat 백엔드를, ``config.llm.embed_backend`` 로 임베딩
백엔드를 고른다. embed_backend 가 비어 있으면 chat 과 동일 백엔드를 쓴다.

사내 chat 엔드포인트가 임베딩을 제공하지 않을 때, chat=internal/langchain +
embed=fastembed(로컬) 처럼 분리할 수 있다.
"""

from __future__ import annotations

from ..config import AppConfig, LLMConfig, disable_proxy
from ..logging_setup import log_print
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

    # LLM 입출력 추적(Langfuse/로컬)과 **실행 타임라인**은 목적이 다르지만 감싸는
    # 지점이 같다 — 셋 중 하나라도 켜져 있으면 :class:`TracedChat` 로 감싼다.
    # 셋 다 꺼져 있으면 import 조차 하지 않고 오늘과 **동일 객체**를 돌려준다.
    #
    # ⚠️ 타임라인은 기본 on 이라 실무에서는 사실상 항상 감싸진다. 예전에는
    # "추적을 안 켜면 래핑도 없다"가 단언이었으므로 CLAUDE.md 도 함께 고쳤다 —
    # 그러지 않으면 다음 사람이 "래핑 안 한다는데 왜 래핑되지"로 시간을 잃는다.
    #
    # chat 만 감싼다(embed_obj 는 그대로). 같은 객체인 백엔드에서도 임베딩 호출은
    # TracedChat.__getattr__ 위임으로 원래 구현에 그대로 닿는다.
    if llm.langfuse.is_active() or llm.trace_local or config.logging.timeline:
        from .tracing import wrap_chat

        chat_obj = wrap_chat(chat_obj, config)

    # 요청 한도·타임아웃 대응은 **가장 바깥**에 둔다. 추적보다 바깥이라 대기 시간이
    # TracedChat 의 duration_ms 에 섞이지 않는다(지연 통계 오염 방지).
    # 둘 다 꺼져 있으면 감싸지 않아 오늘과 **동일 객체**를 돌려준다.
    #
    # ⚠️ 조건에 timeout_wait 가 **빠져 있었다** — 60초 대기 코드는 있는데
    # max_calls_per_minute 가 0(기본)이면 래퍼 자체가 안 붙어 호출 경로에 아예
    # 없었다. 실측에서 "1분 대기가 구현 안 된 것 같다"로 관찰된 것이 이 누락이다.
    if llm.max_calls_per_minute > 0 or llm.timeout_wait > 0:
        _warn_retry_multiplication(llm, backend)
        chat_obj, embed_obj = _wrap_rate_limited(chat_obj, embed_obj, llm, embed_backend)
    return chat_obj, embed_obj


def _warn_retry_multiplication(llm: LLMConfig, backend: str) -> None:
    """타임아웃 대기와 SDK 자체 재시도가 **곱해지는** 조합을 경고한다.

    두 층은 서로를 모른다. ``langchain`` 은 openai SDK 가, ``internal``/``ollama`` 는
    :mod:`contentcompare.llm.http` 가 이미 타임아웃을 재시도하는데, 그 위에 60초 대기를
    얹으면 총 소요가 곱으로 커진다 — 실측 기본값(timeout 120s · max_retries 3 ·
    timeout_max_retries 2)에서 **한 호출이 최악 26분**이다. 사람이 그것을 행(hang)으로
    오해하고 실행을 끊으면 대기 기능이 오히려 결과를 잃게 만든다.

    막지 않고 알리기만 하는 것은, 이 조합이 정당한 환경(게이트웨이가 한도를 붙들기로
    알리고 SDK 재시도가 그 벽을 못 넘는 경우)이 실제로 있기 때문이다.
    """
    if llm.timeout_wait <= 0 or llm.max_retries <= 0:
        return
    inner = (llm.max_retries + 1)
    worst = inner * llm.timeout * (llm.timeout_max_retries + 1) \
        + llm.timeout_max_retries * llm.timeout_wait
    log_print(
        f"⚠️ 재시도가 두 겹입니다 — 한 호출 최악 {worst / 60:.0f}분"
        f" ({backend} 자체 재시도 {inner}회 × timeout {llm.timeout:.0f}s"
        f" × 대기 재시도 {llm.timeout_max_retries + 1}회"
        f" + 대기 {llm.timeout_wait:.0f}s × {llm.timeout_max_retries}).\n"
        f"   timeout_wait 를 켰다면 llm.max_retries 를 0~1 로 낮추세요.",
    )


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
        timeout_wait=llm.timeout_wait,
        timeout_max_retries=llm.timeout_max_retries,
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
