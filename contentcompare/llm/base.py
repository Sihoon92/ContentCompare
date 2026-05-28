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

    def embed(self, texts: list[str]) -> list[list[float]]:
        """텍스트 리스트를 임베딩 벡터 리스트로 변환한다(입력 순서 유지)."""
        ...
