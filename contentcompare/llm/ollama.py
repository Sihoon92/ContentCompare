"""Ollama 백엔드.

로컬에서 동작하는 Ollama 서버(`/api/chat`, `/api/embeddings`)를 호출한다.
재시도/타임아웃/에러 처리는 :mod:`contentcompare.llm.http` 가 담당한다.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from ..config import LLMConfig
from .http import RetryPolicy, extract, post_json
from .truncation import LENGTH, LengthLimitError, from_truncated
from .usage import UNKNOWN, Usage, from_response


class OllamaBackend:
    """Ollama chat + embedding 백엔드 (LLMClient & EmbeddingClient 동시 구현)."""

    handles_rate_limit = True
    """429 를 HTTP 레벨(:mod:`.http`)에서 직접 처리한다.

    자세한 이유는 :attr:`.internal.InternalBackend.handles_rate_limit` 참고.
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
        # 이전 호출 값이 이번 호출에 묻어나지 않게 **먼저** 비운다 — 실패해서
        # 응답이 없을 때 직전 성공의 토큰 수가 남으면 기록이 거짓말을 한다.
        self.last_usage = UNKNOWN
        url = f"{self.host}/api/chat"
        options: dict[str, Any] = {"temperature": temperature}
        if self.config.ollama.num_ctx:
            options["num_ctx"] = self.config.ollama.num_ctx
        payload: dict[str, Any] = {
            "model": self.config.chat_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": options,
        }
        if self.config.ollama.think is not None:
            payload["think"] = self.config.ollama.think
        data = self._post(url, payload)
        self.last_usage = from_response(data)
        content = extract(data, "message", "content", url=url)
        self._explain_length(data, url, content)
        return content

    @staticmethod
    def _explain_length(data: dict, url: str, content: str) -> None:
        """``done_reason="length"`` 를 원인이 보이는 예외로 바꾼다.

        Ollama 는 한도에 닿아도 오류를 주지 않는다 — 원인을 모르면 "LLM JSON 파싱
        실패: ''" 로만 보여 디버깅이 매우 어렵다.

        ⚠️ **content 유무와 무관하게 본다.** 예전에는 빈 응답일 때만 봤는데, 그러면
        *부분 생성*(컨텍스트는 충분한데 출력이 상한에 닿아 잘린 경우)이 조용히 통과해
        다른 백엔드 셋과 **같은 결함**이 됐다. 두 경우는 조치가 다르므로 문구를 가른다:

        - 빈 응답 → 입력·사고가 컨텍스트를 먹었다. ``num_ctx``/``think`` 가 답이다.
        - 부분 생성 → **출력** 상한이다. ``num_ctx`` 를 올려도 안 풀리고 배치를 줄여야 한다.

        둘 다 :class:`~contentcompare.llm.truncation.LengthLimitError` 로 올리므로
        ``fact`` 쪽 배치 축소 복구가 양쪽에 다 걸린다(입력이 줄면 둘 다 완화된다).
        ``LLMRequestError`` 의 하위형이라 기존 호출부·테스트는 그대로 동작한다.
        """
        if data.get("done_reason") != LENGTH:
            return
        if content:
            raise from_truncated(data, backend="ollama", output=content)
        used = data.get("prompt_eval_count", 0) + data.get("eval_count", 0)
        thinking = (data.get("message") or {}).get("thinking")
        raise LengthLimitError(
            f"{url} 응답이 비었습니다(done_reason=length, 사용 토큰 ≈{used})."
            + (" 모델이 컨텍스트를 사고(thinking)에 모두 사용했습니다." if thinking else "")
            + " config 의 llm.ollama.num_ctx 를 늘리거나(예: 16384)"
            " llm.ollama.think: false 로 사고를 끄세요.",
            usage=from_response(data), backend="ollama",
        )

    # --- EmbeddingClient -------------------------------------------------- #
    def embed(self, texts: list[str], *, kind: str = "passage") -> list[list[float]]:
        url = f"{self.host}/api/embeddings"
        prefix = self.config.embed_prefix_for(kind)
        vectors: list[list[float]] = []
        for text in texts:
            # TODO: Ollama 가 배치 임베딩을 지원하면 한 번에 보내도록 개선.
            data = self._post(
                url, {"model": self.config.embed_model, "prompt": prefix + text}
            )
            vectors.append(extract(data, "embedding", url=url))
        return vectors
