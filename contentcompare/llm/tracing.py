"""LLM 입출력 추적 — Langfuse(서버) 와 로컬 파일(:class:`JsonlTracer`) 두 갈래.

**Langfuse SDK 를 아는 유일한 파일**이기도 하다.

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
import json
import logging
import os
import re
import sys
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
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


_trust_store_logged = False


def use_os_trust_store(lf: Any) -> bool:
    """OS(Windows) 인증서 저장소를 파이썬 전역 SSL 에 주입한다. 성공하면 ``True``.

    **왜 필요한가.** 사내 PC 는 브라우저로 Langfuse 웹이 열린다 = 루트 CA 가 이미
    Windows 저장소에 있다. 그런데 파이썬은 ``certifi`` 의 공인 CA 만 믿어서 혼자
    실패한다. :func:`apply_ssl_env` 로 ``SSL_CERT_FILE`` 을 깔아도 부족한데,
    **httpx 는 그 환경변수를 읽지 않고** certifi 로 기본 컨텍스트를 만들기 때문이다
    — Langfuse SDK 가 내부에서 자기 httpx 클라이언트를 만들면 우리가 넘긴 CA 를
    전부 우회한다. ``truststore`` 는 ``ssl.SSLContext`` 자체를 갈아끼우므로 그
    경로까지 덮는다(실측: 이것만이 사내 자체호스팅 접속을 통과시켰다).

    ``disable_proxy`` / :func:`apply_ssl_env` 와 같이 **프로세스 전역**을 바꾸며
    복원하지 않는다. 그래서 사람이 의도를 밝힌 경우엔 손대지 않는다:

    - ``ssl_cert`` 를 명시했다 → 그 인증서가 우선이다
    - ``verify_ssl: false`` → 검증할 게 없다

    선택 의존성이라 없으면 예전대로 certifi 로 돈다. 관측 기능의 실패가 비교 실행을
    막아선 안 되므로 어떤 예외도 삼킨다.
    """
    global _trust_store_logged

    if (getattr(lf, "ssl_cert", "") or "").strip():
        return False
    if not getattr(lf, "verify_ssl", True):
        return False
    try:
        import truststore  # noqa: WPS433 — 지연 import (선택 의존성)

        truststore.inject_into_ssl()
    except ImportError:
        if not _trust_store_logged:
            _trust_store_logged = True
            logger.info(
                "사내 CA 를 쓰는 환경이면 `pip install truststore` 를 권합니다 "
                "— OS 인증서 저장소를 그대로 써서 PEM 파일이 필요 없어집니다."
            )
        return False
    except Exception as exc:  # noqa: BLE001 — 관측 실패가 실행을 막으면 안 된다
        logger.warning("OS 인증서 저장소를 쓰지 못했습니다: %s", exc)
        return False
    if not _trust_store_logged:
        _trust_store_logged = True
        logger.info("OS 인증서 저장소 사용(truststore) — 사내 CA 를 그대로 신뢰합니다.")
    return True


def trust_source(lf: Any) -> str:
    """지금 무엇을 신뢰하는지 한 줄로. ``--check`` 가 사람에게 그대로 보여 준다.

    설정과 실제가 어긋날 때(인증서를 지정했는데 형식이 틀려 무시됐다, truststore 를
    안 깔았다) 그 사실이 화면에 드러나야 한다 — 안 그러면 "설정은 했는데 왜 여전히
    SSL 오류지"를 반복한다. **상태를 바꾸지 않는다**(주입은 하지 않는다).
    """
    bundle = ca_bundle(lf)
    if bundle is False:
        return "⚠️ 인증서 검증 꺼짐"
    if isinstance(bundle, str):
        return f"ca={os.path.basename(bundle)}"
    # 여기부터는 ca_bundle 이 None — 지정이 없거나, 있었지만 못 쓸 파일이었다.
    try:
        import importlib.util

        if importlib.util.find_spec("truststore") is not None:
            return "ca=OS 인증서 저장소(truststore)"
    except (ImportError, ValueError):
        pass
    return "ca=certifi(공인 CA 만) — 사내 CA 면 pip install truststore"


def import_langfuse(lf: Any) -> Any:
    """``Langfuse`` 클래스를 돌려준다. **반드시 이 함수로만 import 할 것.**

    순서가 load-bearing 이다 — :func:`use_os_trust_store` 를 **먼저** 부른 뒤에
    SDK 를 import 한다. truststore 는 ``ssl.SSLContext`` 를 갈아끼우는 방식이라
    다른 모듈이 컨텍스트를 만든 뒤에 주입하면 그 컨텍스트는 옛것(certifi)을 그대로
    쓴다. langfuse import 는 httpx 와 OTLP 익스포터를 끌고 오므로 정확히 그 함정에
    걸린다 — 실측: 단독 스크립트(주입 후 import)는 통과하는데 ``--check``
    (import 후 주입)만 ConnectError 로 죽었다.

    미설치 ``ImportError`` 는 삼키지 않는다. 호출부가 "SDK 미설치" 안내로 바꾼다.
    """
    use_os_trust_store(lf)
    from langfuse import Langfuse  # type: ignore[import-not-found]

    return Langfuse


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
    # 클라이언트를 만들기 **전에** 주입해야 SDK 내부가 만드는 httpx 클라이언트까지 덮는다.
    use_os_trust_store(lf)
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


class JsonlTracer:
    """LLM 호출을 **로컬 파일**로 남긴다 — 서버가 없는 환경의 추적 수단.

    Langfuse 와 목적이 다르다. Langfuse 는 사람이 브라우저로 보는 것이고, 이쪽은
    파이프라인 현미경(``ui/micro_world``)이 **읽어서 화면에 싣는 입력**이다. 그래서
    한 줄짜리 로그가 아니라 단계별로 분리된 JSON 이어야 한다 — 그래야 프롬프트를
    고치기 전/후를 ``diff`` 로 비교할 수 있다.

    ``artifacts/_traces/<실행>/`` 아래에 ``<순번>-<단계>.json`` 과 매니페스트
    ``index.json`` 을 쓴다. 매니페스트가 필요한 이유는 **이전 실행이 남긴 파일**과
    섞이지 않게 하기 위해서다(부분 실패로 순번이 큰 파일이 남아 있을 수 있다).
    """

    active = True

    _ILLEGAL = re.compile(r"[^\w\-]", re.UNICODE)

    def __init__(self, root: str, *, max_chars: int = 0) -> None:
        self.root = Path(root)
        self.max_chars = max_chars
        self._dir: Optional[Path] = None
        self._seq = 0
        self._files: list[str] = []
        self._meta: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    @classmethod
    def _slug(cls, name: str) -> str:
        """경로에 못 쓰는 문자를 ``_`` 로(한글 보존 — ``ArtifactStore.slug`` 와 같은 규약)."""
        return cls._ILLEGAL.sub("_", name)[:80] or "run"

    def start_run(self, name: str, metadata: dict) -> None:
        self._dir = self.root / self._slug(name)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self._files = []
        self._meta = dict(metadata or {})
        self._write_index()

    def end_run(self) -> None:
        self._write_index()
        self._dir = None

    def record(self, event: GenerationEvent) -> None:
        target = self._dir if self._dir is not None else self._fallback_dir()
        self._seq += 1
        system, sys_cut = self._clip(event.system)
        user, user_cut = self._clip(event.user)
        output, out_cut = self._clip(event.output)
        name = f"{self._seq:03d}-{self._slug(event.stage)}.json"
        payload = {
            "seq": self._seq,
            "stage": event.stage,
            "model": event.model,
            "duration_ms": event.duration_ms,
            # 실패한 호출의 응답 원문이 가장 중요하다 — parse_failures 가 왜 났는지는
            # 그것 없이 알 수 없다(재시도로 성공해도 첫 응답은 사라진다).
            "error": event.error or "",
            "truncated": bool(sys_cut or user_cut or out_cut),
            "system": system,
            "user": user,
            "output": output,
            "metadata": dict(event.metadata or {}),
        }
        (target / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._files.append(name)
        self._write_index()

    def flush(self) -> None:
        self._write_index()

    # ------------------------------------------------------------------ #
    def _clip(self, text: str) -> tuple[str, bool]:
        text = text or ""
        if self.max_chars and len(text) > self.max_chars:
            return text[: self.max_chars], True
        return text, False

    def _fallback_dir(self) -> Path:
        """``start_run`` 없이 호출된 경우(연결 점검 등)에도 기록은 잃지 않는다."""
        if self._dir is None:
            self._dir = self.root / "_adhoc"
            self._dir.mkdir(parents=True, exist_ok=True)
        return self._dir

    def _write_index(self) -> None:
        if self._dir is None:
            return
        (self._dir / "index.json").write_text(
            json.dumps({"metadata": self._meta, "calls": len(self._files),
                        "files": self._files}, ensure_ascii=False, indent=2),
            encoding="utf-8")


class MultiTracer:
    """여러 tracer 에 같은 이벤트를 흘린다(Langfuse + 로컬 동시 사용).

    **한쪽 실패가 다른 쪽을 죽이지 않는다** — 실패한 tracer 만 목록에서 빼고 계속한다.
    """

    active = True

    def __init__(self, tracers: list[Any]) -> None:
        self._tracers = [t for t in tracers if t is not None]

    def _fan(self, method: str, *args: Any) -> None:
        for tracer in list(self._tracers):
            try:
                getattr(tracer, method)(*args)
            except Exception as exc:  # noqa: BLE001 — 관측 실패가 실행을 막으면 안 된다
                logger.warning("추적기 %s.%s 실패 — 이 실행에서 제외합니다: %s",
                               type(tracer).__name__, method, exc)
                self._tracers.remove(tracer)

    def start_run(self, name: str, metadata: dict) -> None:
        self._fan("start_run", name, metadata)

    def end_run(self) -> None:
        self._fan("end_run")

    def record(self, event: GenerationEvent) -> None:
        self._fan("record", event)

    def flush(self) -> None:
        self._fan("flush")


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


def tracing_enabled(config: AppConfig) -> bool:
    """추적을 켤 것인가 — Langfuse(서버) 또는 로컬 파일 중 하나라도 켜져 있으면.

    :func:`~contentcompare.llm.factory.build_clients` 가 chat 을 감쌀지 판단할 때 쓴다.
    """
    return bool(config.llm.langfuse.is_active() or config.llm.trace_local)


def build_tracer(config: AppConfig) -> Any:
    """설정에 따라 tracer 를 만든다. **어떤 실패에도 예외를 던지지 않는다.**

    Langfuse 와 로컬 파일은 **독립적으로** 켤 수 있고, 둘 다 켜면
    :class:`MultiTracer` 로 합친다. 어느 쪽도 안 켜져 있으면 :class:`NullTracer`.
    """
    local = _build_local_tracer(config)
    remote = _build_langfuse_tracer(config)
    if local and remote:
        return MultiTracer([remote, local])
    return remote or local or NullTracer()


def _build_local_tracer(config: AppConfig) -> Optional[Any]:
    if not config.llm.trace_local:
        return None
    try:
        tracer = JsonlTracer(config.llm.trace_dir,
                             max_chars=config.llm.trace_max_chars)
    except Exception as exc:  # noqa: BLE001 — 관측 실패가 실행을 막으면 안 된다
        logger.warning("로컬 추적 초기화 실패(dir=%s): %s", config.llm.trace_dir, exc)
        return None
    logger.info("로컬 LLM 추적 활성 (dir=%s) — 프롬프트에 문서 원문이 평문으로 남습니다",
                config.llm.trace_dir)
    return tracer


def _build_langfuse_tracer(config: AppConfig) -> Optional[Any]:
    """SDK 미설치·인증 실패·주소 오타 어느 쪽이든 ``None`` 으로 떨어지고 경고만 남긴다."""
    lf = config.llm.langfuse
    if not lf.is_active():
        return None
    host, public, secret = lf.resolved()
    try:
        Langfuse = import_langfuse(lf)   # truststore 주입 → SDK import (순서 중요)
    except ImportError:
        logger.warning(
            "Langfuse 설정이 켜져 있으나 SDK 가 없어 추적을 건너뜁니다 "
            "— pip install -e \".[langfuse]\""
        )
        return None
    try:
        client = new_langfuse_client(lf, Langfuse)
    except Exception as exc:  # noqa: BLE001 — 관측 실패가 실행을 막으면 안 된다
        logger.warning("Langfuse 초기화 실패(host=%s): %s", host, exc)
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            logger.warning(
                "사내 CA 인증서가 필요해 보입니다 — llm.langfuse.ssl_cert 에 "
                "PEM 파일 경로를 지정하세요."
            )
        return None
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
