"""LLM/임베딩 백엔드 추상 인터페이스.

모든 백엔드(Ollama, 사내 HTTP 등)는 아래 두 프로토콜을 구현한다. 파이프라인은
구체 백엔드를 모르고 인터페이스에만 의존하므로 설정 한 줄로 교체 가능하다.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """텍스트 생성(chat/completion) 백엔드.

    **선택적 확장 규약(덕 타이핑) — 프로토콜 서명에는 넣지 않는다.**

    아래 넷은 *구현해도 되고 안 해도 되는* 확장이다. 서명(``complete -> str``)을 바꾸면
    ``comparison/``·``readers/`` 와 테스트의 가짜 chat 37개까지 파급되므로,
    ``handles_rate_limit`` 을 시작으로 이 저장소는 **속성으로 신호하고 호출부가
    ``getattr`` 로 물어보는** 방식을 세 번째 반복해서 쓰고 있다.

    ``handles_rate_limit: bool`` (클래스 속성)
        429 를 백엔드가 HTTP 레벨(:mod:`.http`)에서 이미 처리한다. :mod:`.factory` 가 읽어
        사후 재시도 래퍼를 붙일지 정한다.

    ``last_usage: Usage`` (인스턴스 속성)
        직전 ``complete()`` 의 토큰 사용량. :class:`.tracing.TracedChat` 이 호출 직후 읽는다.

    ``supports_structured_output: bool`` (속성 또는 프로퍼티)
        ``complete()`` 가 아래 ``schema`` 키워드를 이해한다.
        :meth:`~contentcompare.fact.llm_stage.LlmRunner.complete_json` 이 **호출마다** 읽고
        참일 때만 ``schema=`` 를 넘긴다. 선언하지 않은 객체(테스트용 가짜 chat 37개)에는
        추가 인자가 **절대 가지 않는다** — 서명 하나를 지키기 위한 장치의 전부가 이것이다.

    ``complete(..., schema: dict | None = None)``
        ``supports_structured_output`` 이 참인 구현만 이 키워드를 받는다. 값은 **JSON
        Schema dict** 이지 OpenAI 의 ``response_format`` 봉투가 아니다 — 봉투 모양은
        백엔드마다 다르고(OpenAI 는 ``response_format``, Ollama 는 ``format`` 에 스키마를
        날것으로) 그 차이를 아는 것은 백엔드의 일이다. 호출부는 "이 모양의 JSON 을
        원한다"만 안다.

    ⚠️ **플래그와 키워드는 한 커밋에서 같이 움직여야 한다.** 래퍼가 키워드를 아직 통과시키지
    못하는데 안쪽 백엔드가 플래그를 참으로 선언하면, 위임(``__getattr__``)이 플래그만 위로
    올려 주고 호출은 ``TypeError`` 로 죽는다. ``factory._needs_rate_limit_wrapper`` 가
    "설정에는 있는데 호출 경로에는 없는" 결함으로 **두 번** 깨진 것과 같은 형태의 사고다.
    """

    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> str:
        """system/user 프롬프트로 한 번의 생성을 수행하고 텍스트를 반환한다."""
        ...


@runtime_checkable
class EmbeddingClient(Protocol):
    """임베딩 백엔드."""

    def embed(self, texts: list[str], *, kind: str = "passage") -> list[list[float]]:
        """텍스트 리스트를 임베딩 벡터 리스트로 변환한다(입력 순서 유지).

        ``kind`` 는 입력 종류로 ``passage``(본문) | ``query``(검색어). e5 계열처럼
        종류별 접두어가 필요한 모델을 위해 백엔드가 접두어를 달리 붙일 수 있다.
        접두어가 설정돼 있지 않으면 무시되어 기존 동작과 같다."""
        ...
