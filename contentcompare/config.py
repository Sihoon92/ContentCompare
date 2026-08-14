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

    num_ctx: int = 0
    """컨텍스트 창 크기(토큰). 0 이면 미지정(Ollama 기본 = 보통 4096).

    fact 파이프라인처럼 표 전체를 한 프롬프트에 넣는 단계는 기본 4096 을 쉽게 넘긴다.
    초과하면 Ollama 는 오류 대신 **빈 응답**(``done_reason="length"``)을 돌려주므로
    반드시 문서 크기에 맞춰 올려야 한다(16384 권장).
    """

    think: Optional[bool] = None
    """thinking 모델의 사고 과정 사용 여부. ``None`` 이면 미지정(모델 기본).

    ``False`` 로 두면 사고 토큰을 쓰지 않아 **응답이 크게 빨라지고**(실측 219s→26s)
    컨텍스트를 답변에만 쓴다. thinking 미지원 모델에 지정하면 Ollama 가 거부할 수
    있으므로 기본은 미지정이다.
    """


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
class LangfuseConfig:
    """LLM 입출력 추적(Langfuse). **세 값이 다 채워져야 켜진다.**

    이 프로젝트의 LLM 디버깅 수단은 로그 파일과 최종 산출물 JSON 뿐이었다. 로그는
    HTTP 페이로드를 1000자에서 자르고(``llm/http.py``), 산출물에는 프롬프트가 없다.
    그래서 "무엇을 넣어 무엇을 받았는지"를 볼 수가 없었다 — Langfuse 가 그 공백을 메운다.

    키 지정 규약은 :class:`InternalConfig` 와 같다: 직접값 우선, 비면 환경변수.
    **시크릿은 환경변수 사용을 권장**한다(``config.yaml`` 이 .gitignore 대상이더라도).
    """

    host: str = ""
    """Langfuse 서버 주소. 비우면 비활성. 사내 자체호스팅 가정."""
    public_key: str = ""
    secret_key: str = ""
    public_key_env: str = "LANGFUSE_PUBLIC_KEY"
    """public_key 가 비었을 때 읽을 환경변수 이름."""
    secret_key_env: str = "LANGFUSE_SECRET_KEY"
    """secret_key 가 비었을 때 읽을 환경변수 이름."""
    enabled: bool = True
    """키가 다 있어도 잠시 끄고 싶을 때 false."""
    trace_embeddings: bool = False
    """임베딩 호출도 추적할지. 기본 false — 한 번에 20건씩 묶여 오고 출력이 숫자
    벡터라 trace 가 지저분해지는 반면, 디버깅이 어려운 것은 chat 프롬프트다."""
    ssl_cert: str = ""
    """사내 CA 인증서 경로(**PEM 형식**). 사설 인증서를 쓰는 자체호스팅에 필요.

    Python 은 ``certifi`` 의 공인 CA 번들만 믿으므로, 사내 CA 로 서명된 Langfuse
    서버에 붙으면 ``CERTIFICATE_VERIFY_FAILED`` 가 난다. 이 값을 주면 그 인증서를
    신뢰 목록에 얹는다 — :attr:`verify_ssl` 을 끄는 것보다 안전하다.

    ⚠️ ``.cer`` 확장자는 DER(바이너리)인 경우가 많은데 requests/httpx 는 **PEM 만**
    읽는다. 변환: ``openssl x509 -inform DER -in x.cer -out x.pem``
    """
    verify_ssl: bool = True
    """false 면 인증서 검증을 끈다. **최후의 수단** — 인증서를 구할 수 없을 때만.

    :attr:`ssl_cert` 를 쓰는 편이 낫다. 검증을 끄면 중간자 공격을 막지 못한다.
    """
    flush_timeout: float = 5.0
    """종료 시 전송 대기 상한(초). 짧은 실행이 전송 전에 끝나는 것을 막는다."""
    debug: bool = False
    """true 면 SDK 자체 디버그 로그를 켠다."""

    def resolved(self) -> tuple[str, str, str]:
        """``(host, public_key, secret_key)`` — 직접값 우선, 없으면 환경변수."""
        pub = self.public_key or os.environ.get(self.public_key_env, "")
        sec = self.secret_key or os.environ.get(self.secret_key_env, "")
        return self.host.strip(), pub.strip(), sec.strip()

    def is_active(self) -> bool:
        """추적을 켤 것인가. 셋 중 하나라도 비면 끈다.

        반쯤 켜진 상태로 실행되면 "왜 trace 가 없지?" 를 추적하는 데 시간이 든다.
        전부 갖춰졌을 때만 켠다.
        """
        return self.enabled and all(self.resolved())


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
    """임베딩 입력 앞에 붙일 공통 접두어(query/passage 별도 미지정 시 폴백).

    e5 계열은 접두어가 필수다(없으면 성능 저하). 단일 값만 쓰려면 'query: ' 권장.
    """
    embed_query_prefix: str = ""
    """검색 쿼리(기준 항목)에 붙일 접두어. e5 계열 권장값: 'query: '.

    비우면 embed_prefix 로 폴백. 교차언어 검색(한↔영)에서는 query/passage 를
    구분해 주는 것이 정확도에 유리하다.
    """
    embed_passage_prefix: str = ""
    """본문(대상 문서 청크)에 붙일 접두어. e5 계열 권장값: 'passage: '.

    비우면 embed_prefix 로 폴백.
    """
    timeout: float = 120.0
    max_retries: int = 3
    """일시 오류(연결/타임아웃/5xx) 재시도 횟수."""
    backoff_base: float = 2.0
    """재시도 지수 백오프 기준(2 → 2s,4s,8s,…)."""
    rate_limit_wait: float = 60.0
    """요청 한도(HTTP 429) 시 대기 시간(초). 서버가 Retry-After 를 주면 그 값 우선.

    사내 LLM 은 분당 요청 한도가 흔하므로, 429 면 짧게 백오프하지 않고
    1분가량 기다렸다가 다시 시도한다.
    """
    rate_limit_max_retries: int = 5
    """요청 한도(429) 전용 재시도 횟수(일반 일시오류와 별도 예산)."""
    max_calls_per_minute: int = 0
    """**사전** 스로틀 — 분당 이 횟수를 넘지 않게 호출 페이스를 조절한다. 0=끔.

    :attr:`rate_limit_wait` 가 "걸린 뒤 기다리는" 사후 대응이라면 이쪽은 애초에 안
    걸리게 하는 사전 대응이다. 사후 대응만으로는 한도에 부딪힐 때마다 1분씩 멈추는
    일이 반복되는데, fact 엔진은 실행 한 번에 수백 회를 부르므로 그 손실이 크다.

    사내 한도가 분당 60회면 **55** 정도를 권한다(다른 프로세스·재시도 여유분).
    """
    rate_limit_status_codes: list = field(default_factory=lambda: [429])
    """한도 초과로 볼 HTTP 상태코드. 표준은 429 지만 사내 게이트웨이는 다를 수 있다."""
    rate_limit_markers: list = field(default_factory=lambda: [
        "rate limit", "too many requests", "quota", "요청 한도",
    ])
    """예외 메시지에서 한도 초과로 볼 문자열(소문자 비교).

    상태코드를 주지 않고 본문 메시지로만 알리는 게이트웨이가 있어 필요하다.
    과하게 넓히면 진짜 오류를 1분씩 재시도하며 숨기므로 신중히 늘릴 것.
    """
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    internal: InternalConfig = field(default_factory=InternalConfig)
    langfuse: LangfuseConfig = field(default_factory=LangfuseConfig)

    # --- 로컬 LLM 입출력 추적 (서버 불필요) ------------------------------- #
    # Langfuse 는 서버가 있어야 하지만, 파이프라인 현미경은 **파일**을 읽는다.
    # 같은 tracer 프로토콜의 파일 구현체를 켜는 스위치다.
    trace_local: bool = False
    """LLM 프롬프트/응답을 ``trace_dir`` 에 JSON 으로 남긴다.

    ⚠️ **기본 off** — 프롬프트에는 문서 원문이 통째로 들어가므로, 켜면 사내 문서가
    디스크에 평문으로 남는다. 오판을 추적하는 동안만 켤 것.
    """
    trace_dir: str = "artifacts/_traces"
    """로컬 추적 저장 루트: ``<trace_dir>/<실행>/<순번>-<단계>.json``."""

    trace_max_chars: int = 0
    """기록 1건의 프롬프트/응답 최대 길이(0=무제한). 절단하면 ``truncated: true``."""

    def embed_prefix_for(self, kind: str) -> str:
        """임베딩 입력 종류별 접두어를 고른다.

        kind 가 ``query``/``passage`` 면 각 전용 접두어를 우선 쓰고, 비어 있으면
        공통 ``embed_prefix`` 로 폴백한다. e5 계열에서 query/passage 를 구분해
        붙이기 위한 헬퍼(미설정 시 빈 문자열 → 기존 동작 그대로)."""
        if kind == "query" and self.embed_query_prefix:
            return self.embed_query_prefix
        if kind == "passage" and self.embed_passage_prefix:
            return self.embed_passage_prefix
        return self.embed_prefix or ""


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

    skip_banner_rows: bool = True
    """'대외비'처럼 전체 열이 같은 값으로 통합된 배너행을 헤더에서 자동 제외."""

    auto_header: bool = False
    """True 면 LLM 이 상위 행을 보고 header_row/header_rows 를 자동 추정(시트별)."""

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
    output_dir: str = "reports"
    """생성한 리포트(.md)를 자동 저장할 디렉터리(Streamlit '리포트 보기'가 읽는 곳)."""


@dataclass
class KnowledgeConfig:
    """사람이 작성한 도메인 지식(human-in-the-loop) 설정(요청 5번)."""

    enabled: bool = True
    """True 면 ``dir`` 의 모든 .md 를 비교 프롬프트에 참고 자료로 항상 주입한다."""

    dir: str = "knowledge"
    """도메인 지식 Markdown 파일들이 있는 디렉터리."""

    max_chars: int = 12000
    """프롬프트에 주입할 지식 텍스트의 최대 길이(초과분은 잘라낸다)."""


@dataclass
class FastPathConfig:
    """F5 Acceptance Gate — 코드 판정 ``match`` 를 믿어도 되는지 채점한다.

    기본값이 shadow(``enforce=False``)인 것은 게이트가 LLM 호출을 **늘리기**
    때문이다. 오늘도 코드 ``match`` 는 LLM 을 안 부르므로(``fact_comparator``
    ``finalize()``), 게이트의 실제 효과는 지금까지 조용히 확정되던 unsafe match 를
    LLM 으로 보내는 것이다. 얼마나 늘어날지는 ``unsafe_match_rate`` 를 실측하기
    전에는 알 수 없고, 모르는 채로 켜면 비용 회귀를 게이트 탓으로 돌리지 못한다.
    """

    enabled: bool = True
    """게이트 채점과 계측. False 면 게이트를 실행하지 않는다(도입 전과 동일)."""

    enforce: bool = False
    """True 면 게이트가 거부한 code ``match`` 를 LLM 판정으로 강등한다."""


@dataclass
class FactConfig:
    """신규 fact 파이프라인 설정(엔진=fact 일 때만 사용). 현행 RAG 와 무관.

    엔진 선택은 CLI ``--engine`` 으로 하며(결정 #1) config 에 두지 않는다.
    """

    artifacts_dir: str = "artifacts"
    """중간 산출물 저장 루트: ``artifacts/<문서>/<단계>.json`` (결정 #5)."""

    save_artifacts: bool = True
    """중간 산출물을 항상 저장(테스트에서 off 가능)."""

    cache: bool = True
    """단계별 산출물 캐싱(같은 입력이면 재계산/재호출 0 — 결정 #2)."""

    max_llm_calls_per_doc: int = 500
    """문서당 LLM 호출 예산(결정 #2). F1+ 단계에서 사용.

    이 값들은 **비용 목표가 아니라 폭주 방지선**이다. 실제 비용은 문서 크기가 정하고,
    분당 한도는 ``llm.max_calls_per_minute`` 이 따로 막는다. 낮게 잡으면 큰 문서에서
    조용히 고갈돼 뒤쪽 항목이 통째로 보류되는데, 그건 절약이 아니라 **결과 손실**이다.
    """

    record_batch_rows: int = 30
    """F2 Record Normalizer 의 행 배치 크기(한 LLM 호출당 처리 행 수)."""

    fact_batch_blocks: int = 20
    """F3 Fact Extractor 의 Word/PPT 블록/도형 배치 크기(한 LLM 호출당 처리 블록 수)."""

    max_repair_iters: int = 2
    """Repair Loop 최대 반복(F4b — 미구현)."""

    # --- F5 비교 단계 --------------------------------------------------- #
    match_top_k: int = 3
    """기준 fact 하나당 검토할 대상 후보 수."""

    match_min_score: float = 0.65
    """후보로 인정할 최소 임베딩 코사인 점수. 미만이면 후보 없음 → ``missing``.

    F3.5 spike 실측으로 정했다: 정답 매칭 0.697~0.808 vs 오매칭 0.449~0.700.
    두 분포가 0.697/0.700 에서 겹치므로 **점수만으로는 완전히 가를 수 없다** —
    명백히 무관한 것만 자르고 나머지는 속성 대조/LLM 이 판단한다.
    """

    match_review_score: float = 0.75
    """이 점수 미만이면 코드 단독 판정을 신뢰하지 않고 LLM 에 넘긴다(경계 구간)."""

    compare_use_llm: bool = True
    """False 면 코드 결정적 판정만 한다(애매한 건 전부 ``unknown``). 재현성·비용 우선일 때."""

    max_llm_calls_per_compare: int = 1000
    """비교 단계 전체의 LLM 호출 예산(문서 처리 예산과 별도).

    소요량은 대략 ``애매한 기준 항목 수 × 대상 문서 수`` 다. 1:N 판정(후보 2건 이상은
    무조건 LLM)이 들어오면서 소요가 늘었으므로 여유를 크게 둔다 — 고갈되면 그 시점
    이후가 전부 ``unknown`` 이 되고, 리포트가 🚨 경고로 그것을 알린다.
    """

    # --- F7 개념 그래프 ------------------------------------------------- #
    use_concept_graph: bool = True
    """False 면 F5 가 기존 유사도 매칭으로 동작한다(롤백 스위치)."""

    concept_recall_top_k: int = 10
    """기준 fact 당 개념 판정에 올릴 후보 수.

    **5 는 "한 기준 항목의 진짜 짝 후보가 5개 이하"라는 가정이었고, 규격표에서 그
    가정은 자주 깨진다.** 한 항목이 조건별로 쪼개지면(충전 온도 4~5구간, 온도별
    충전전류…) 형제 fact 만으로 자리가 다 차고, 거기에 다른 온도·전류 항목까지 같은
    자리를 두고 경쟁한다. 실측에서 정답 후보가 6위로 밀려 ``cut_by: top_k`` 로
    탈락했고, 그 기준 항목은 비교 자체가 일어나지 않아 ``missing`` 이 됐다.

    올려도 정확도 위험이 낮은 이유는 이 값이 **판정이 아니라 recall** 이기 때문이다
    (:attr:`concept_recall_min` 주석과 같은 근거). 후보가 늘어도 연결은 여전히 LLM
    제안 → 코드 인용 검증 → ``differs_by`` 병합 차단을 거친다. 반대로 작게 잡아
    놓치면 리포트에 "대상에 없다"는 **확신에 찬 거짓**이 실린다 — 실패 방향이
    비대칭이라 넉넉한 쪽이 안전하다.

    대가는 LLM 호출이다(:attr:`max_llm_calls_per_concept` 참고). ⚠️ 정규화 이름이
    완전히 일치하면 :meth:`FactMatcher.search` 가 **조기 종료로 1건만** 돌려주므로,
    같은 언어 문서쌍에서는 이 값을 올려도 후보가 늘지 않는다.
    """

    concept_recall_min: float = 0.3
    """후보 생성 최소 유사도. **판정이 아니라 계산량 제한**이라 느슨해도 안전하다.

    개념 그래프가 판정을 맡으므로 이 값이 틀려도 손해가 작다 — 낮으면 LLM 호출이 늘고,
    높으면 후보가 안 만들어져 ``missing`` 이 된다(설계 §2.4).
    """

    concept_batch_pairs: int = 20
    """한 LLM 호출당 판정할 쌍 수."""

    max_llm_calls_per_concept: int = 300
    """개념 단계 LLM 호출 예산(문서 처리·비교 예산과 별도).

    소요량은 ``기준 fact 수 × concept_recall_top_k ÷ concept_batch_pairs`` 로 커진다 —
    200행 문서에 기본값(top_k 10 · batch 20)이면 100회다. 뒤집으면 이 예산은 기준
    fact **600개**까지 감당한다. 여기서 고갈되면 남은 쌍이 ``unknown`` → 연결 없음 →
    **전 항목 ``missing``** 으로 귀결돼 피해가 가장 크다.
    """

    ontology_path: str = "knowledge/ontology.yaml"
    """사람이 승격한 개념 관계 파일. 없으면 빈 온톨로지로 시작한다."""

    # --- Fast Path 게이트(Phase 1) ---------------------------------------- #
    fast_path: FastPathConfig = field(default_factory=FastPathConfig)

    # --- 진단 계측(디버깅 뷰어 입력) ------------------------------------- #
    # ``missing`` 판정의 원인은 여섯 가지인데(recall 실패·LLM 미판정·근거 게이트 강등·
    # F3 추출 누락·의도된 차단·F5 LLM 판정) 기존 산출물만으로는 앞의 둘을 구분할 수
    # 없다. 아래 두 산출물이 그 구분을 가능하게 한다 — **오판 추적이 목적이므로
    # 끄지 말 것.** 둘 다 LLM 을 부르지 않아 비용이 0 이다.
    save_candidate_pairs: bool = True
    """F7 후보 쌍을 ``candidate_pairs.json`` 으로 남긴다(컷오프된 후보 포함)."""

    save_facts_by_block: bool = True
    """블록/행 → fact 매핑을 ``facts_by_block.json`` 으로 남긴다."""


@dataclass
class LoggingConfig:
    """로그 잡음 조절. 기본 목록(``logging_setup.NOISY_LOGGERS``)을 덧대는 용도다.

    기본 목록을 코드 상수로 둔 이유는 순서다 — :func:`logging_setup.setup_logging` 은
    ``AppConfig.load`` **보다 먼저** 불려야 설정 파일을 읽는 동안의 로그까지 잡는다.
    그래서 config 는 "그 뒤에 얹는 조정"만 담당한다.
    """

    quiet_extra: list = field(default_factory=list)
    """기본 목록 외에 추가로 조용히 만들 로거 이름(접두어). 예: ``["pptx", "docx"]``."""

    verbose_extra: list = field(default_factory=list)
    """반대로 전부 보고 싶은 로거. 기본 목록에 있어도 DEBUG 로 되돌린다.

    예: 사내 LLM HTTP 를 의심할 때 ``["urllib3", "httpcore"]``. 환경변수
    ``CONTENTCOMPARE_LOG_NOISY=1`` 이 전체를 여는 것과 달리 **골라서** 연다.
    """


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    excel: ExcelConfig = field(default_factory=ExcelConfig)
    similarity: SimilarityConfig = field(default_factory=SimilarityConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)
    fact: FactConfig = field(default_factory=FactConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

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
        langfuse = LangfuseConfig(**llm_raw.pop("langfuse", {}) or {})
        llm = LLMConfig(ollama=ollama, internal=internal, langfuse=langfuse, **llm_raw)
        # fact 도 중첩 섹션을 갖는다 — pop 하지 않으면 dict 인 채로 필드에 박힌다.
        fact_raw = dict(data.get("fact", {}) or {})
        fast_path = FastPathConfig(**fact_raw.pop("fast_path", {}) or {})
        return cls(
            llm=llm,
            excel=ExcelConfig(**data.get("excel", {}) or {}),
            similarity=SimilarityConfig(**data.get("similarity", {}) or {}),
            report=ReportConfig(**data.get("report", {}) or {}),
            knowledge=KnowledgeConfig(**data.get("knowledge", {}) or {}),
            fact=FactConfig(fast_path=fast_path, **fact_raw),
            logging=LoggingConfig(**data.get("logging", {}) or {}),
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
