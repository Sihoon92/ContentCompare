"""FastEmbed 기반 로컬 임베딩 백엔드.

사내 chat 엔드포인트가 임베딩을 제공하지 않을 때, **로컬에서** 임베딩을 만든다.
fastembed 는 ONNX 런타임 기반의 가벼운 오픈소스 임베딩 라이브러리로, 서버 없이
모델을 받아 바로 임베딩한다(오프라인/사내망 친화적).

    pip install -e .[fastembed]

한국어가 섞이면 다국어 모델을 쓰세요(config.embed_model). 지원 모델은 fastembed
버전마다 다르므로, 실패하면 에러 메시지에 출력되는 '지원 목록'에서 고르세요. 예:
    - intfloat/multilingual-e5-large   (다국어, 널리 지원됨 — 권장)
    - intfloat/multilingual-e5-small   (다국어, 가벼움)
    - BAAI/bge-m3                       (다국어, 최신 fastembed 필요: pip install -U fastembed)
    - BAAI/bge-small-en-v1.5            (영어 위주, 가벼움)
"""

from __future__ import annotations

from typing import Any, Optional

from ..config import LLMConfig

_DEFAULT_MODEL = "intfloat/multilingual-e5-large"


def _supported_names(text_embedding_cls) -> list[str]:
    """설치된 fastembed 가 지원하는 모델명 목록(키 이름 차이 흡수)."""
    names: list[str] = []
    try:
        for m in text_embedding_cls.list_supported_models():
            name = m.get("model") or m.get("model_name") if isinstance(m, dict) else None
            if name:
                names.append(name)
    except Exception:  # pragma: no cover - 버전 차이
        pass
    return names


class FastEmbedBackend:
    """fastembed 로 임베딩만 담당(EmbeddingClient). chat 은 지원하지 않는다."""

    def __init__(self, config: LLMConfig, *, model: Optional[Any] = None) -> None:
        self.config = config
        self._model = model  # 테스트 시 주입 가능

    def _ensure_model(self):
        if self._model is None:
            try:
                from fastembed import TextEmbedding  # noqa: WPS433 - 지연 import
            except ImportError as exc:  # pragma: no cover - 환경 의존
                raise RuntimeError(
                    "fastembed 가 필요합니다: pip install -e .[fastembed]"
                ) from exc
            name = self.config.embed_model or _DEFAULT_MODEL
            kwargs: dict[str, Any] = {}
            if self.config.embed_cache_dir:
                # 오프라인: 미리 받아둔 모델 폴더를 사용(다운로드 시도 안 함).
                kwargs["cache_dir"] = self.config.embed_cache_dir
            try:
                self._model = TextEmbedding(model_name=name, **kwargs)
            except (ValueError, KeyError) as exc:  # 미지원 모델명
                supported = _supported_names(TextEmbedding)
                hint = ", ".join(supported) if supported else "(목록 조회 실패)"
                raise RuntimeError(
                    f"fastembed 가 임베딩 모델 '{name}' 을(를) 지원하지 않습니다.\n"
                    f"config 의 embed_model 을 지원 모델 중 하나로 바꾸세요"
                    f"(다국어 권장: intfloat/multilingual-e5-large).\n"
                    f"또는 최신 버전 설치: pip install -U fastembed\n"
                    f"지원 목록: {hint}"
                ) from exc
        return self._model

    # --- EmbeddingClient -------------------------------------------------- #
    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure_model()
        out: list[list[float]] = []
        for vec in model.embed(list(texts)):
            # fastembed 는 numpy 배열을 내놓는다 → 리스트로 변환.
            tolist = getattr(vec, "tolist", None)
            out.append(tolist() if callable(tolist) else list(vec))
        return out
