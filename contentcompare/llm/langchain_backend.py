"""LangChain 기반 백엔드 (OpenAI 호환).

``base_url`` + ``api_key`` + ``model`` 세 값만으로 동작한다. langchain-openai 의
``ChatOpenAI`` / ``OpenAIEmbeddings`` 를 사용하며, 설정은 기존 ``internal`` 섹션을
그대로 재사용한다(프록시 우회/SSL 검증 포함). ``backend: langchain`` 으로 선택한다.

langchain 은 무거운 선택적 의존성이므로 import 를 실제 호출 시점으로 지연한다:
    pip install -e .[langchain]
"""

from __future__ import annotations

import contextlib
import os
from typing import Any, Optional

from ..config import LLMConfig, no_proxy


class LangChainBackend:
    """LangChain(OpenAI 호환) chat + embedding 백엔드.

    테스트/주입을 위해 chat/embeddings 객체를 직접 넘길 수 있다.
    """

    def __init__(
        self,
        config: LLMConfig,
        *,
        chat: Optional[Any] = None,
        embeddings: Optional[Any] = None,
    ) -> None:
        self.config = config
        self._chat = chat
        self._emb = embeddings

    # --- 공통 ------------------------------------------------------------- #
    def _proxy_ctx(self):
        if self.config.internal.unset_proxy:
            return no_proxy()
        return contextlib.nullcontext()

    def _api_key(self) -> str:
        return self.config.internal.api_key or os.environ.get(
            self.config.internal.api_key_env, ""
        )

    def _http_client(self):
        """verify_ssl=False 면 검증을 끈 httpx 클라이언트를 만든다(사내 사설 인증서)."""
        if self.config.internal.verify_ssl:
            return None
        try:
            import httpx  # noqa: WPS433 - 지연 import

            return httpx.Client(verify=False)
        except Exception:  # pragma: no cover - httpx 는 langchain 의존성
            return None

    @staticmethod
    def _missing(exc: Exception) -> RuntimeError:
        return RuntimeError(
            "langchain 백엔드에는 langchain-openai 가 필요합니다: "
            "pip install -e .[langchain]"
        )

    def _ensure_chat(self):
        if self._chat is None:
            try:
                from langchain_openai import ChatOpenAI  # noqa: WPS433
            except ImportError as exc:  # pragma: no cover - 환경 의존
                raise self._missing(exc) from exc
            kwargs: dict[str, Any] = dict(
                model=self.config.chat_model,
                base_url=self.config.internal.base_url,
                api_key=self._api_key() or "sk-none",
                temperature=0.0,
                timeout=self.config.timeout,
                max_retries=self.config.max_retries,
            )
            http_client = self._http_client()
            if http_client is not None:
                kwargs["http_client"] = http_client
            self._chat = ChatOpenAI(**kwargs)
        return self._chat

    def _ensure_emb(self):
        if self._emb is None:
            try:
                from langchain_openai import OpenAIEmbeddings  # noqa: WPS433
            except ImportError as exc:  # pragma: no cover - 환경 의존
                raise self._missing(exc) from exc
            kwargs: dict[str, Any] = dict(
                model=self.config.embed_model,
                base_url=self.config.internal.base_url,
                api_key=self._api_key() or "sk-none",
                timeout=self.config.timeout,
            )
            http_client = self._http_client()
            if http_client is not None:
                kwargs["http_client"] = http_client
            self._emb = OpenAIEmbeddings(**kwargs)
        return self._emb

    # --- LLMClient -------------------------------------------------------- #
    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> str:
        chat = self._ensure_chat()
        # langchain chat 모델은 (role, content) 튜플 리스트를 받는다(메시지 클래스 import 불필요).
        messages = [("system", system), ("human", user)]
        with self._proxy_ctx():
            resp = chat.bind(temperature=temperature).invoke(messages)
        content = getattr(resp, "content", resp)
        return content if isinstance(content, str) else str(content)

    # --- EmbeddingClient -------------------------------------------------- #
    def embed(self, texts: list[str], *, kind: str = "passage") -> list[list[float]]:
        emb = self._ensure_emb()
        prefix = self.config.embed_prefix_for(kind)
        if prefix:
            texts = [prefix + t for t in texts]
        with self._proxy_ctx():
            return emb.embed_documents(list(texts))
