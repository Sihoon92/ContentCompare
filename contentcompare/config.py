"""설정 로딩 및 프록시 정책 처리.

YAML 파일을 읽어 :class:`AppConfig` 로 만들고, 환경변수로 일부 값을 오버라이드한다.
사내(internal) LLM 연결 시 ``HTTP_PROXY``/``HTTPS_PROXY`` 를 비우는 정책도 여기서 담당한다(기획 1번).
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - 선택적 의존성
    yaml = None


# --------------------------------------------------------------------------- #
# 설정 데이터클래스
# --------------------------------------------------------------------------- #
@dataclass
class OllamaConfig:
    host: str = "http://localhost:11434"


@dataclass
class InternalConfig:
    base_url: str = "https://llm.intra.corp/v1"
    api_key: str = ""
    """API 키를 직접 지정(간단). 보안상 비워두고 api_key_env 를 쓰는 것을 권장."""
    api_key_env: str = "INTERNAL_LLM_API_KEY"
    """API 키가 담긴 환경변수 이름. api_key 가 비어있을 때 사용."""
    unset_proxy: bool = True
    """True 면 사내 호출 시 HTTP(S)_PROXY 를 빈 값으로 만든다."""
    verify_ssl: bool = False
    log_proxy: bool = False
    """True 면 호출 직전 적용되는 프록시 환경변수를 로그로 남긴다(우회 실검증용)."""


@dataclass
class LLMConfig:
    backend: str = "ollama"  # "ollama" | "internal" | "langchain"
    embed_backend: str = ""
    """임베딩 백엔드를 chat 과 다르게 쓸 때 지정.

    비우면 backend 와 동일. 사내 chat 엔드포인트가 임베딩을 제공하지 않으면
    ``fastembed``(로컬) 또는 ``ollama`` 를 지정해 분리한다.
    """
    chat_model: str = "qwen2.5:14b"
    embed_model: str = "bge-m3"
    embed_cache_dir: str = ""
    """fastembed 모델 캐시 폴더. 오프라인(사내망)이면 미리 받은 폴더를 지정.

    비우면 fastembed 기본 캐시(~/.cache/fastembed)를 쓰며 최초 1회 다운로드한다.
    """
    embed_model_path: str = ""
    """직접 받은 ONNX 임베딩 모델 폴더 경로(backend=onnx 일 때).

    폴더에 model.onnx + tokenizer.json 이 있어야 한다. 다운로드 없이 로컬 사용.
    """
    embed_prefix: str = ""
    """임베딩 입력 앞에 붙일 접두어. e5 계열은 'query: ' 를 권장."""
    timeout: float = 120.0
    max_retries: int = 3
    """일시 오류(연결/타임아웃/5xx/429) 재시도 횟수."""
    backoff_base: float = 2.0
    """재시도 지수 백오프 기준(2 → 2s,4s,8s,…)."""
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    internal: InternalConfig = field(default_factory=InternalConfig)


@dataclass
class ExcelConfig:
    granularity: str = "hybrid"
    """비교 분해 단위: ``row`` | ``field`` | ``hybrid``.

    - row    : 행 전체를 하나의 비교 단위(DocItem)로.
    - field  : 셀 하나를 독립 비교 단위로(키 문맥 포함).
    - hybrid : 행=검색 단위(RecordItem), 셀=판정 단위(FieldClaim). 기본값.
    """

    header_row: int = 1
    """헤더가 시작하는 행(1-based, 절대 위치)."""

    header_rows: int = 1
    """헤더가 차지하는 행 수(다단 헤더 지원). ``header_row`` 부터 이 수만큼."""

    key_columns: list = field(default_factory=list)
    """행을 식별하는 키 컬럼들. 헤더명(str) 또는 1-based 인덱스(int). 비면 자동 추정."""

    compare_columns: Optional[list] = None
    """비교 대상 컬럼들. None 이면 키/스킵 제외 전체. 헤더명 또는 1-based 인덱스."""

    skip_columns: list = field(default_factory=list)
    """비교에서 완전히 제외할 컬럼들. 헤더명 또는 1-based 인덱스."""

    value_as_displayed: bool = True
    """True 면 화면 표시 문자열(서식 적용)을 비교에 사용, False 면 원시값."""

    max_rows: Optional[int] = None
    """비교할 최대 row 수(None=전체)."""


@dataclass
class SimilarityConfig:
    recall_k: int = 30
    """1차 검색(임베딩·BM25)에서 각각 추려낼 후보 수."""

    top_k: int = 10
    """MMR 후 LLM 에 투입할 최종 후보 수."""

    fusion: str = "rrf"
    """후보 융합 방식: ``rrf``(임베딩+BM25) | ``cosine``(임베딩 단독)."""

    rrf_k: int = 60
    """RRF 상수(클수록 상위 순위 가중이 완만)."""

    mmr_lambda: float = 0.5
    """MMR 관련도/다양성 가중(1=관련도만, 0=다양성만)."""

    per_doc_cap: int = 4
    """한 대상 문서가 최종 후보를 독식하지 않도록 문서별 상한."""

    rerank: bool = False
    """교차 인코더 재랭킹 사용 여부(Phase 2.5, 기본 off)."""

    cache_dir: str = ".cache/embeddings"
    """임베딩 디스크 캐시 경로(빈 문자열이면 캐시 비활성)."""

    chunk_chars: int = 800
    """긴 항목 청킹 길이."""

    min_score: float = 0.0
    """``fusion=cosine`` 일 때 코사인 임계값(rrf 에서는 사실상 미사용)."""


@dataclass
class ReportConfig:
    format: str = "markdown"


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    excel: ExcelConfig = field(default_factory=ExcelConfig)
    similarity: SimilarityConfig = field(default_factory=SimilarityConfig)
    report: ReportConfig = field(default_factory=ReportConfig)

    # ----------------------------------------------------------------- #
    # 로딩
    # ----------------------------------------------------------------- #
    @classmethod
    def load(cls, path: Optional[str] = None) -> "AppConfig":
        """YAML 파일에서 설정을 읽는다. path 가 없으면 기본값을 사용."""
        data: dict[str, Any] = {}
        if path:
            if yaml is None:
                raise RuntimeError("PyYAML 이 필요합니다: pip install pyyaml")
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        llm_raw = dict(data.get("llm", {}))
        ollama = OllamaConfig(**llm_raw.pop("ollama", {}) or {})
        internal = InternalConfig(**llm_raw.pop("internal", {}) or {})
        llm = LLMConfig(ollama=ollama, internal=internal, **llm_raw)
        return cls(
            llm=llm,
            excel=ExcelConfig(**data.get("excel", {}) or {}),
            similarity=SimilarityConfig(**data.get("similarity", {}) or {}),
            report=ReportConfig(**data.get("report", {}) or {}),
        )


# --------------------------------------------------------------------------- #
# 프록시 정책
# --------------------------------------------------------------------------- #
_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


def disable_proxy() -> None:
    """프로세스 전역에서 HTTP(S)_PROXY 를 빈 값으로 만든다(복원하지 않음).

    사내망 직결을 위해 프로그램 시작 시 한 번 호출하면, 이후 실행 내내
    프록시를 거치지 않는다(기획 1번). 멱등적이라 여러 번 불러도 안전하다.
    """
    for k in _PROXY_VARS:
        os.environ[k] = ""


@contextlib.contextmanager
def no_proxy() -> Iterator[None]:
    """블록 동안 HTTP(S)_PROXY 를 빈 값으로 만들고, 빠져나오면 복원한다.

    :func:`disable_proxy` 가 전역으로 비우는 것과 달리, 특정 호출 구간에만
    적용하고 복원하는 안전망 컨텍스트다.
    """
    saved = {k: os.environ.get(k) for k in _PROXY_VARS}
    try:
        for k in _PROXY_VARS:
            os.environ[k] = ""
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
