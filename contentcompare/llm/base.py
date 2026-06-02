"""LLM/임베딩 백엔드 추상 인터페이스.

모든 백엔드(Ollama, 사내 HTTP 등)는 아래 두 프로토콜을 구현한다. 파이프라인은
구체 백엔드를 모르고 인터페이스에만 의존하므로 설정 한 줄로 교체 가능하다.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """텍스트 생성(chat/completion) 백엔드."""

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
