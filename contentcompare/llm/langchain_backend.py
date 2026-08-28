"""LangChain 기반 백엔드 (OpenAI 호환).

``base_url`` + ``api_key`` + ``model`` 세 값만으로 동작한다. langchain-openai 의
``ChatOpenAI`` / ``OpenAIEmbeddings`` 를 사용하며, 설정은 기존 ``internal`` 섹션을
그대로 재사용한다(프록시 우회/SSL 검증 포함). ``backend: langchain`` 으로 선택한다.

langchain 은 무거운 선택적 의존성이므로 import 를 실제 호출 시점으로 지연한다:
    pip install -e .[langchain]
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any, Optional

from .. import timeline
from ..config import LLMConfig, no_proxy
from ..logging_setup import log_print
from .structured import looks_like_schema_rejection, normalize_mode, response_format
from .tracing import current_stage
from .usage import UNKNOWN, Usage, from_response

logger = logging.getLogger("contentcompare.llm.langchain_backend")


class LangChainBackend:
    """LangChain(OpenAI 호환) chat + embedding 백엔드.

    테스트/주입을 위해 chat/embeddings 객체를 직접 넘길 수 있다.
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
        chat: Optional[Any] = None,
        embeddings: Optional[Any] = None,
    ) -> None:
        self.config = config
        self._chat = chat
        self._emb = embeddings
        # 오타(``json-schema``)는 **여기서** 죽는다. 백엔드 생성은 실행 시작 시점이라
        # 40분짜리 파이프라인 한복판이 아니라 첫 1초 안에 알 수 있다.
        self._mode = normalize_mode(getattr(config, "structured_output", "auto"))
        self._structured_off = False

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
        """httpx 클라이언트를 **항상** 만들어 관측 훅을 단다.

        두 가지를 한다: ``verify_ssl=False`` 면 검증을 끄고(사내 사설 인증서),
        어느 쪽이든 :mod:`.http_probe` 훅을 붙여 **SDK 내부 재시도**를 타임라인에
        드러낸다.

        예전에는 ``verify_ssl=True`` 면 ``None`` 을 돌려줘 SDK 가 자기 클라이언트를
        만들게 뒀는데, 그러면 재시도가 우리 눈에 안 보인다 — 실측에서 120초 × 4회가
        ``duration_ms`` 하나로 뭉쳤다. **verify 값은 그대로**이므로 통신 동작은
        동일하고 관측만 얻는다.
        """
        try:
            import httpx  # noqa: WPS433 - 지연 import

            from .http_probe import hooks
        except Exception:  # pragma: no cover - httpx 는 langchain 의존성
            return None
        try:
            return httpx.Client(
                verify=self.config.internal.verify_ssl,
                event_hooks=hooks(self.config.max_retries),
            )
        except Exception as exc:  # pragma: no cover - 환경 의존
            # 클라이언트를 못 만들면 SDK 기본값으로 돌아간다 — 관측을 잃을 뿐
            # 호출은 계속돼야 한다.
            logger.warning("httpx 클라이언트 생성 실패 — SDK 기본값을 씁니다: %s", exc)
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
                # chat 에만 있고 여기 빠져 있던 비대칭. 임베딩도 같은 엔드포인트라
                # 일시 오류·한도에 똑같이 노출된다.
                max_retries=self.config.max_retries,
            )
            http_client = self._http_client()
            if http_client is not None:
                kwargs["http_client"] = http_client
            self._emb = OpenAIEmbeddings(**kwargs)
        return self._emb

    # --- 구조화 출력(덕 타이핑 규약, :mod:`.base` 참고) --------------------- #
    @property
    def supports_structured_output(self) -> bool:
        """이 백엔드가 ``complete(..., schema=...)`` 를 이해하는가.

        **클래스 상수가 아니라 프로퍼티인 이유**는 이 값이 실행 중에 내려갈 수 있기
        때문이다(:meth:`_disable_structured`). ``handles_rate_limit`` 은 백엔드의 불변
        성질이라 상수여도 됐지만, 이쪽은 **서버가 우리를 거절했는가**라는 관측값이다.

        래퍼(:class:`~contentcompare.llm.tracing.TracedChat` ·
        :class:`~contentcompare.llm.ratelimit.RateLimitedChat`)가 ``__getattr__`` 로
        위임하므로 호출부는 래퍼를 통해서도 살아 있는 이 값을 읽는다. **위임이 제약이
        아니라 장치로 쓰이는 자리다** — 메서드였다면 추적을 우회했을 그 위임이다.
        """
        return self._mode != "off" and not self._structured_off

    # --- LLMClient -------------------------------------------------------- #
    def complete(self, system: str, user: str, *, temperature: float = 0.0,
                 schema: Optional[dict] = None) -> str:
        """한 번 생성하고 텍스트를 돌려준다.

        ``schema``(JSON Schema dict)가 있으면 서버에 모양을 강제한다.
        **``with_structured_output()`` 을 쓰지 않는 이유**가 중요하다 — 그쪽은 pydantic
        객체를 돌려주므로

        1. ``-> str`` 계약이 깨져 래퍼 스택 전체가 파급되고,
        2. 반환이 ``AIMessage`` 가 아니게 되어 :func:`~contentcompare.llm.usage.from_response`
           가 토큰을 못 읽는다. 그런데 :mod:`.usage` 규약상 **미상은 "서버가 안 줬다"는
           뜻**이라, 우리가 잃어버린 것이 서버 탓으로 기록된다 — ``tok_per_sec`` 이 사라져
           배치 크기 판단 근거가 없어지고 원인을 영영 못 찾는다,
        3. :class:`~contentcompare.llm.tracing.TracedChat` 이 남기는 ``output`` 이 모델
           원문이 아니라 되직렬화 결과가 되어, "무엇을 받았는지"를 보려고 만든 추적
           (``trace_local``)이 무력해지며,
        4. 파싱이 SDK 안으로 들어가 :class:`~contentcompare.fact.llm_stage.LlmRunner` 의
           교정 재시도와 ``parse_failures`` 계측(F4b 설계 입력)을 우회한다.

        ``bind`` 는 문자열 계약을 그대로 두고 서버 강제만 얻는다 — 위 넷이 전부 살아 있다.
        """
        chat = self._ensure_chat()
        # langchain chat 모델은 (role, content) 튜플 리스트를 받는다(메시지 클래스 import 불필요).
        messages = [("system", system), ("human", user)]
        self.last_usage = UNKNOWN  # 이유는 :attr:`last_usage` 참고
        fmt = (response_format(schema, mode=self._mode)
               if self.supports_structured_output else None)
        try:
            return self._invoke(chat, messages, temperature, fmt)
        except Exception as exc:  # noqa: BLE001 — 스키마 거절만 흡수하고 나머지는 올려보낸다
            if fmt is None or not looks_like_schema_rejection(exc):
                # 스키마와 무관한 실패를 재시도하면 **모든 실패의 비용이 두 배**가 된다.
                raise
            self._disable_structured(exc)
            return self._invoke(chat, messages, temperature, None)

    def _invoke(self, chat, messages, temperature: float,
                fmt: Optional[dict]) -> str:
        """실제 호출 한 번. ``fmt`` 가 ``None`` 이면 **오늘과 완전히 같은 요청**이다.

        ``bind`` 에 ``response_format`` 키를 아예 넣지 않는 것이 중요하다 —
        ``response_format=None`` 을 넣으면 그것을 페이로드에 실어 보내는 SDK 버전이 있고,
        그러면 '끔'이 '끔이라고 명시'로 바뀌어 오늘과 다른 요청이 된다.
        """
        bound: dict[str, Any] = {"temperature": temperature}
        if fmt is not None:
            bound["response_format"] = fmt
        with self._proxy_ctx():
            resp = chat.bind(**bound).invoke(messages)
        self.last_usage = from_response(resp)
        content = getattr(resp, "content", resp)
        return content if isinstance(content, str) else str(content)

    def _disable_structured(self, exc: Exception) -> None:
        """서버가 스키마를 거절했다 — 이 실행에서는 끄고 **한 번만** 알린다.

        강등은 :meth:`~contentcompare.llm.tracing.TracedChat._safe_record` 와 같은
        전략이다: 매 호출마다 같은 경고를 내면 수백 건이 진짜 원인을 덮으므로, 한 번
        알리고 이후에는 조용히 폴백한다.

        **실행을 죽이지 않는 이유**는 대안이 더 나쁘기 때문이다 — 구조화 출력은 정확도
        보조 장치이지 기능 자체가 아니고, 40분짜리 파이프라인을 한복판에서 끊는 것이
        "오늘까지의 동작으로 마저 끝내는 것"보다 낫다고 볼 근거가 없다. 대신 흔적을
        **세 곳**(화면·타임라인·``run_stats`` 의 ``structured_calls``)에 남겨 사후에 반드시
        보이게 한다. 조용히 사라지는 강등은 만들지 않는다.
        """
        self._structured_off = True
        logger.warning("구조화 출력 거절: %s: %s", type(exc).__name__, exc)
        timeline.emit(
            timeline.NOTE, current_stage(depth=1), status="error",
            reason="구조화 출력 거절 — 이 실행에서는 끕니다",
            error=f"{type(exc).__name__}: {exc}"[:200],
        )
        log_print(
            "⚠️ 서버가 JSON Schema 를 거절했습니다 — 이 실행에서는 구조화 출력을 끄고 "
            "프롬프트만으로 진행합니다.\n"
            f"   실제 응답: {type(exc).__name__} {str(exc)[:200]}\n"
            "   계속 나오면 llm.structured_output 을 json_object 또는 off 로 두세요.",
        )

    # --- EmbeddingClient -------------------------------------------------- #
    def embed(self, texts: list[str], *, kind: str = "passage") -> list[list[float]]:
        emb = self._ensure_emb()
        prefix = self.config.embed_prefix_for(kind)
        if prefix:
            texts = [prefix + t for t in texts]
        with self._proxy_ctx():
            return emb.embed_documents(list(texts))
