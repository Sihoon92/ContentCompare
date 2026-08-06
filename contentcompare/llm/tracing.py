"""LLM 입출력 추적(Langfuse) — **SDK 를 아는 유일한 파일**.

이 파이프라인의 LLM 디버깅 수단은 두 가지뿐이었고 둘 다 부족했다:

- ``logs/contentcompare_<시각>.log`` — HTTP 페이로드를 1000자에서 자른다(:mod:`.http`).
- ``artifacts/<문서>/*.json`` — LLM 의 **최종 산출물**만 남고 프롬프트가 없다.

그래서 "어떤 프롬프트를 넣어 무엇을 받았는지"를 볼 수가 없었다. 실제로 개념 판정의
인용문(``left_text``)을 확인하려면 ``concept_graph.json`` 을 직접 파싱해야 했다.

설계 원칙 네 가지:

1. **병목 한 곳만 감싼다.** :func:`~contentcompare.llm.factory.build_clients` 가 chat
   클라이언트를 만드는 유일한 지점이므로, 여기서 :class:`TracedChat` 로 감싸면 fact 6단계 ·
   RAG 판정 · 헤더 추정 · 연결 점검이 **호출부 수정 없이** 전부 추적된다. 특히
   ``comparison/``·``readers/`` 는 **코드 무수정 원칙**이라 이 방식이 아니면 손댈 수 없다.
2. **꺼져 있으면 아무 일도 없다.** 비활성이면 factory 가 이 모듈을 import 조차 하지
   않는다(코어 의존성 최소 정책).
3. **추적 실패가 비교를 죽이지 않는다.** 모든 tracer 호출은 예외를 삼키고, 첫 실패 후
   no-op 으로 강등한다(:class:`FactPipeline` 의 문서 단위 격리와 같은 원칙).
4. **SDK 격리.** Langfuse v2 와 v3(OpenTelemetry 기반)는 API 가 크게 다르다. SDK 접촉을
   :class:`LangfuseTracer` 하나에 가둬 버전 교체가 이 파일 안에서 끝나게 한다.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from ..config import AppConfig

logger = logging.getLogger("contentcompare.llm.tracing")

_STAGE: ContextVar[str] = ContextVar("contentcompare_stage", default="")


# --------------------------------------------------------------------------- #
# 단계 이름
# --------------------------------------------------------------------------- #
@contextlib.contextmanager
def stage(name: str) -> Iterator[None]:
    """이 블록 안의 LLM 호출에 단계 이름을 붙인다.

    Langfuse UI 에서 "F7 개념 판정만" 골라 보려면 이름이 필요하다. fact 파이프라인은
    :mod:`contentcompare.fact.pipeline` 에서 명시적으로 감싸고, 감쌀 수 없는 경로
    (코드 무수정인 ``comparison/``)는 :func:`current_stage` 의 폴백이 처리한다.
    """
    token = _STAGE.set(name)
    try:
        yield
    finally:
        _STAGE.reset(token)


def current_stage(depth: int = 2) -> str:
    """현재 단계 이름. 지정된 게 없으면 **호출자 모듈명**으로 폴백한다.

    이름 없이 전부 한 덩어리로 뭉치면 trace 를 열어봐도 어느 단계인지 알 수 없다.
    ``depth`` 는 :func:`sys._getframe` 기준 거슬러 올라갈 프레임 수.
    """
    explicit = _STAGE.get()
    if explicit:
        return explicit
    try:
        frame = sys._getframe(depth)
    except (ValueError, AttributeError):  # 인터프리터 구현 차이 방어
        return "(unknown)"
    module = frame.f_globals.get("__name__", "")
    return module or "(unknown)"


# --------------------------------------------------------------------------- #
# 이벤트
# --------------------------------------------------------------------------- #
@dataclass
class GenerationEvent:
    """LLM 호출 1건. **키 같은 시크릿은 절대 담지 않는다.**"""

    stage: str
    model: str
    system: str
    user: str
    output: str = ""
    duration_ms: int = 0
    error: Optional[str] = None
    """예외 메시지. ``None`` 이면 정상 — 대시보드에서 유무로 필터하기 쉽다."""
    metadata: dict[str, Any] = field(default_factory=dict)


def run_metadata(config: AppConfig, engine: str, reference: str,
                 targets: list[str]) -> dict[str, Any]:
    """실행 1건의 trace 메타데이터. **키는 넣지 않는다.**"""
    llm = config.llm
    return {
        "engine": engine,
        "reference": reference,
        "targets": list(targets),
        "backend": llm.backend,
        "embed_backend": llm.embed_backend or llm.backend,
        "chat_model": llm.chat_model,
        "embed_model": llm.embed_model,
    }


# --------------------------------------------------------------------------- #
# Tracer 구현
# --------------------------------------------------------------------------- #
class NullTracer:
    """아무것도 하지 않는다. 비활성·SDK 부재·실패 강등 시 쓰인다."""

    active = False

    def start_run(self, name: str, metadata: dict) -> None: ...
    def end_run(self) -> None: ...
    def record(self, event: GenerationEvent) -> None: ...
    def flush(self) -> None: ...


class LangfuseTracer:
    """Langfuse SDK 어댑터. **SDK 를 직접 만지는 유일한 클래스.**

    v3(OpenTelemetry 기반)를 기준으로 쓰되, 없는 메서드는 건너뛰도록 방어적으로
    호출한다 — 사내 미러의 설치 버전이 다를 수 있기 때문이다. 어떤 경우에도
    예외를 밖으로 내보내지 않는다(호출자가 다시 감싸긴 하지만 이중 방어).
    """

    active = True

    def __init__(self, client: Any, flush_timeout: float = 5.0) -> None:
        self._client = client
        self._flush_timeout = flush_timeout
        self._span: Any = None

    def start_run(self, name: str, metadata: dict) -> None:
        starter = getattr(self._client, "start_span", None)
        if starter is None:
            return
        self._span = starter(name=name, input=metadata)
        update = getattr(self._client, "update_current_trace", None)
        if update is not None:
            update(name=name, metadata=metadata)

    def end_run(self) -> None:
        if self._span is not None:
            end = getattr(self._span, "end", None)
            if end is not None:
                end()
            self._span = None

    def record(self, event: GenerationEvent) -> None:
        gen = self._client.start_generation(
            name=event.stage,
            model=event.model,
            input={"system": event.system, "user": event.user},
            metadata=event.metadata,
        )
        gen.update(output=event.output,
                   level="ERROR" if event.error else "DEFAULT",
                   status_message=event.error or "")
        gen.end()

    def flush(self) -> None:
        flush = getattr(self._client, "flush", None)
        if flush is not None:
            flush()


# --------------------------------------------------------------------------- #
# 사설 CA (사내 자체호스팅)
# --------------------------------------------------------------------------- #
_PEM_MARK = b"-----BEGIN"


def ca_bundle(lf: Any) -> Any:
    """SSL 검증에 쓸 값 — 인증서 경로 / ``False``(검증 끔) / ``None``(기본).

    Python 은 ``certifi`` 의 공인 CA 번들만 믿는다. 사내 CA 로 서명된 Langfuse 에
    붙으면 ``CERTIFICATE_VERIFY_FAILED`` 가 나는 이유가 이것이다.

    문제가 있는 경로는 **조용히 넘기지 않는다.** 오타나 DER 파일을 그냥 무시하면
    "설정은 했는데 왜 여전히 SSL 오류지"로 시간을 잃는다 — 원인과 해결책을 로그로 남긴다.
    """
    path = (getattr(lf, "ssl_cert", "") or "").strip()
    if not path:
        # 인증서가 없을 때만 verify_ssl 을 본다. 인증서가 있으면 그것이 우선이다.
        return None if getattr(lf, "verify_ssl", True) else False
    if not os.path.isfile(path):
        logger.warning("Langfuse ssl_cert 파일을 찾을 수 없습니다: %s", path)
        return None
    try:
        head = open(path, "rb").read(64)
    except OSError as exc:
        logger.warning("Langfuse ssl_cert 를 읽을 수 없습니다(%s): %s", path, exc)
        return None
    if _PEM_MARK not in head:
        logger.warning(
            "Langfuse ssl_cert 가 PEM 형식이 아닙니다(DER 로 보임): %s\n"
            "  변환: openssl x509 -inform DER -in \"%s\" -out corp-ca.pem",
            path, path,
        )
        return None
    return path


def apply_ssl_env(lf: Any) -> None:
    """CA 번들을 환경변수로도 깔아둔다 — SDK 내부가 무엇을 쓰든 타도록.

    Langfuse 는 내부적으로 ``requests``(OpenTelemetry 익스포터)와 ``httpx`` 를 섞어
    쓰고 버전마다 다르다. 생성자 인자만으로는 전부 덮지 못하므로, 표준 환경변수를
    함께 설정해 어느 경로로 나가든 같은 CA 를 보게 한다.

    :func:`contentcompare.config.disable_proxy` 와 같이 **프로세스 전역**을 바꾸며
    복원하지 않는다. 사용자가 ``ssl_cert`` 를 명시했을 때만 건드린다.
    """
    bundle = ca_bundle(lf)
    if not isinstance(bundle, str):
        return
    for var in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
        os.environ[var] = bundle
    logger.info("사내 CA 적용: REQUESTS_CA_BUNDLE/SSL_CERT_FILE = %s", bundle)


def _http_client(lf: Any) -> Any:
    """사설 CA(또는 검증 끔)를 문 httpx 클라이언트. 필요 없으면 ``None``.

    경로를 ``verify=`` 에 직접 넘기는 방식은 최신 httpx 에서 deprecated 라
    :func:`ssl.create_default_context` 로 컨텍스트를 만들어 넘긴다. 인증서가
    깨졌으면 여기서 걸리므로(``[X509] PEM lib``) 사유를 남기고 기본 동작으로 돌아간다.
    """
    bundle = ca_bundle(lf)
    if bundle is None:
        return None
    try:
        import httpx  # noqa: WPS433 — 지연 import (langfuse 의 의존성)
    except ImportError:
        return None
    try:
        if bundle is False:
            return httpx.Client(verify=False)
        import ssl

        return httpx.Client(verify=ssl.create_default_context(cafile=bundle))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Langfuse 용 인증서를 불러오지 못했습니다(%s): %s", bundle, exc)
        return None


def new_langfuse_client(lf: Any, factory: Any) -> Any:
    """Langfuse 클라이언트를 만든다. ``httpx_client`` 는 **받아주면** 넘긴다.

    SDK 버전에 따라 이 인자가 없을 수 있어 ``TypeError`` 면 빼고 다시 시도한다
    (환경변수 경로는 이미 깔려 있으므로 그것만으로도 대개 붙는다).
    """
    apply_ssl_env(lf)
    host, public, secret = lf.resolved()
    kwargs: dict[str, Any] = {"public_key": public, "secret_key": secret,
                              "host": host, "debug": lf.debug}
    client = _http_client(lf)
    if client is not None:
        try:
            return factory(httpx_client=client, **kwargs)
        except TypeError as exc:
            logger.info("이 Langfuse SDK 는 httpx_client 를 받지 않습니다(%s) "
                        "— 환경변수 CA 경로로 진행합니다.", exc)
    return factory(**kwargs)


_TRACER: Optional[Any] = None


def get_tracer(config: AppConfig) -> Any:
    """프로세스당 tracer 하나. 진입점과 chat 래퍼가 **같은 것**을 써야 한다.

    각자 :func:`build_tracer` 를 부르면 Langfuse 클라이언트가 두 개 생겨 실행 trace 와
    generation 이 서로 다른 세션에 흩어진다.
    """
    global _TRACER
    if _TRACER is None:
        _TRACER = build_tracer(config)
    return _TRACER


def reset_tracer() -> None:
    """테스트 격리용 — 캐시된 tracer 를 버린다."""
    global _TRACER
    _TRACER = None


def build_tracer(config: AppConfig) -> Any:
    """설정에 따라 tracer 를 만든다. **어떤 실패에도 예외를 던지지 않는다.**

    SDK 미설치·인증 실패·주소 오타 어느 쪽이든 :class:`NullTracer` 로 떨어지고
    경고만 남긴다 — 관측 도구 때문에 비교가 멈추면 본말이 전도된다.
    """
    lf = config.llm.langfuse
    if not lf.is_active():
        return NullTracer()
    host, public, secret = lf.resolved()
    try:
        from langfuse import Langfuse  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "Langfuse 설정이 켜져 있으나 SDK 가 없어 추적을 건너뜁니다 "
            "— pip install -e \".[langfuse]\""
        )
        return NullTracer()
    try:
        client = new_langfuse_client(lf, Langfuse)
    except Exception as exc:  # noqa: BLE001 — 관측 실패가 실행을 막으면 안 된다
        logger.warning("Langfuse 초기화 실패(host=%s): %s", host, exc)
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            logger.warning(
                "사내 CA 인증서가 필요해 보입니다 — llm.langfuse.ssl_cert 에 "
                "PEM 파일 경로를 지정하세요."
            )
        return NullTracer()
    logger.info("Langfuse 추적 활성 (host=%s)", host)
    return LangfuseTracer(client, flush_timeout=lf.flush_timeout)


# --------------------------------------------------------------------------- #
# chat 래퍼
# --------------------------------------------------------------------------- #
class TracedChat:
    """chat 클라이언트를 감싸 호출을 기록한다(:class:`LLMClient` 프로토콜 유지).

    **인자와 반환값을 절대 변형하지 않는다** — 이 래퍼가 끼어들어도 판정 결과는
    바이트 단위로 같아야 한다. 기록은 부수효과일 뿐이다.
    """

    def __init__(self, inner: Any, model: str, backend: str, tracer: Any) -> None:
        self._inner = inner
        self._model = model
        self._backend = backend
        self._tracer = tracer

    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> str:
        name = current_stage(depth=2)
        started = time.monotonic()
        error = ""
        output = ""
        try:
            output = self._inner.complete(system, user, temperature=temperature)
            return output
        except Exception as exc:  # noqa: BLE001 — 기록만 하고 그대로 올려보낸다
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._safe_record(GenerationEvent(
                stage=name, model=self._model, system=system, user=user,
                output=output, error=error or None,
                duration_ms=int((time.monotonic() - started) * 1000),
                metadata={"backend": self._backend, "temperature": temperature},
            ))

    # ------------------------------------------------------------------ #
    def _safe_record(self, event: GenerationEvent) -> None:
        """기록 실패는 삼키고, **첫 실패 후 no-op 으로 강등**한다.

        매 호출마다 경고를 남기면 수십 건의 실패 로그가 진짜 원인을 덮는다.
        강등은 tracer 를 :class:`NullTracer` 로 **교체**하는 방식이라, 이후 호출은
        아무 일도 하지 않는다.
        """
        try:
            self._tracer.record(event)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Langfuse 기록 실패 — 이 실행에서는 추적을 끕니다: %s", exc)
            self._tracer = NullTracer()

    # 감싼 객체의 나머지 속성(embed 등)은 그대로 위임한다 — 백엔드에 따라 chat 과
    # embed 가 같은 객체이므로(InternalBackend), 위임하지 않으면 임베딩이 깨진다.
    def __getattr__(self, item: str) -> Any:
        # ``_inner`` 가 아직 없을 때(생성 도중 예외 등) 무한 재귀를 막는다.
        if item.startswith("_"):
            raise AttributeError(item)
        return getattr(self._inner, item)


def wrap_chat(chat: Any, config: AppConfig, *, tracer: Optional[Any] = None) -> Any:
    """chat 클라이언트를 :class:`TracedChat` 으로 감싼다.

    ``tracer`` 를 주면 그것을 쓴다(테스트 주입용). 없으면 설정으로 만든다.
    """
    return TracedChat(
        chat,
        model=config.llm.chat_model,
        backend=config.llm.backend,
        tracer=tracer if tracer is not None else get_tracer(config),
    )


# --------------------------------------------------------------------------- #
# 실행 단위 trace
# --------------------------------------------------------------------------- #
@contextlib.contextmanager
def trace_run(tracer: Any, name: str, metadata: dict) -> Iterator[None]:
    """실행 1건을 하나의 trace 로 묶는다. 실패는 전부 삼킨다."""
    try:
        tracer.start_run(name, metadata)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Langfuse trace 시작 실패: %s", exc)
    try:
        yield
    finally:
        for step in (tracer.end_run, tracer.flush):
            try:
                step()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Langfuse 종료 처리 실패(%s): %s", step.__name__, exc)
