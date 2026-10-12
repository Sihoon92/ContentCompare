"""사내 LLM 호출의 TLS(인증서 검증) 정책 — :func:`~.factory.build_clients` 가 한 번 적용한다.

**문제.** 사내 게이트웨이는 사설 CA 로 서명돼 있고, 파이썬은 ``certifi`` 의 공인 CA 만
믿어서 ``CERTIFICATE_VERIFY_FAILED`` 가 난다. 그래서 ``llm.internal.verify_ssl`` 기본이
``false`` 였고, 그 대가로 두 가지가 생겼다.

1. 검증을 **아예 안 한다.**
2. 알림이 백엔드마다 달랐다 — ``internal``(requests)은 urllib3 가 호출마다 경고를
   찍고 ``langchain``(httpx)은 조용하다. 실측: 사내 PC 에서 F7 개념 판정을 시작할 때
   ``InsecureRequestWarning`` 이 떴는데, F7 이 fact 파이프라인에서 **임베딩을 처음
   부르는 단계**이고 임베딩만 ``internal`` 로 나가서였다. 같은 "검증 꺼짐"을 한쪽만
   말하고 있었던 것이다.

**해법은 두 갈래다.**

- ``verify_ssl: true`` → OS(Windows) 인증서 저장소를 쓴다(:func:`inject_os_trust_store`).
  브라우저로 게이트웨이가 열리는 PC 면 사내 루트가 이미 거기 있다 — Langfuse 에서
  실측으로 확인한 방법을 LLM 호출에도 쓴다. ``truststore`` 는 ``ssl.SSLContext`` 와
  urllib3 의 사본까지 갈아끼우므로 httpx·requests 두 경로를 한 번에 덮는다.
- ``verify_ssl: false`` → 사람이 의도해서 끈 것이다. urllib3 경고를 **설정한 호스트에
  대해서만** 끄고, 대신 **우리 말로 한 번** 알린다. 설정에 없는 호스트의 무검증
  요청은 계속 경고한다 — 우리가 모르는 것까지 덮지 않는다.

``disable_proxy`` 와 같이 **프로세스 전역**을 바꾸며 복원하지 않는다. 주입은 클라이언트를
**만들기 전에** 해야 한다 — httpx 는 생성 시점에 SSL 컨텍스트를 만든다.
"""

from __future__ import annotations

import logging
import re
import sys
import warnings
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

REMOTE_BACKENDS = ("internal", "langchain")
"""TLS 로 사내 게이트웨이에 붙는 백엔드. ollama·fastembed·onnx 는 대상이 아니다."""

OS_STORE = "os_store"
CERTIFI = "certifi"
OFF = "off"
MIXED = "mixed"
NOT_APPLICABLE = ""


@dataclass
class _State:
    """한 번만 알리기 위한 프로세스 전역 상태(Streamlit 은 한 프로세스에서 여러 번 부른다)."""

    injected: bool = False
    hint_logged: bool = False
    announced: set = field(default_factory=set)


_state = _State()


# --------------------------------------------------------------------------- #
# OS 인증서 저장소
# --------------------------------------------------------------------------- #
def inject_os_trust_store() -> bool:
    """``truststore`` 로 OS 인증서 저장소를 파이썬 전역 SSL 에 주입한다. 성공하면 ``True``.

    선택 의존성이라 없으면 예전대로 certifi 로 돈다. 어떤 실패도 실행을 막지 않는다 —
    검증이 실패하면 그때 호출이 ``CERTIFICATE_VERIFY_FAILED`` 로 원인을 말해 준다.
    주입은 여러 번 해도 결과가 같아서 매번 시도한다(알림만 한 번).
    """
    try:
        import truststore  # noqa: WPS433 - 지연 import (선택 의존성)

        truststore.inject_into_ssl()
    except ImportError:
        if not _state.hint_logged:
            _state.hint_logged = True
            logger.info(
                "사내 CA 를 쓰는 환경이면 `pip install truststore` 를 권합니다 "
                "- OS 인증서 저장소를 그대로 써서 PEM 파일이 필요 없어집니다."
            )
        return False
    except Exception as exc:  # noqa: BLE001 - 신뢰 저장소 실패가 실행을 막으면 안 된다
        logger.warning("OS 인증서 저장소를 쓰지 못했습니다: %s", exc)
        return False
    if _breaks_requests(truststore):
        # 켜 둔 채 두면 첫 requests 호출이 RecursionError 로 죽는다 — 되돌리고 알린다.
        try:
            truststore.extract_from_ssl()
        except Exception as exc:  # noqa: BLE001
            logger.warning("truststore 주입을 되돌리지 못했습니다: %s", exc)
        logger.warning(
            "truststore %s 는 이미 import 된 requests(2.32+)와 충돌해 OS 인증서 저장소를 "
            "쓰지 않습니다. `pip install -U truststore` 로 올리세요(0.10 에서 확인).",
            getattr(truststore, "__version__", "?"),
        )
        return False
    if not _state.injected:
        _state.injected = True
        logger.info("OS 인증서 저장소 사용(truststore) - 사내 CA 를 그대로 신뢰합니다.")
    return True


def _breaks_requests(truststore: Any) -> bool:
    """주입 뒤에도 requests 의 **미리 만든** SSL 컨텍스트가 truststore 것이 아닌가.

    실측(truststore 0.8.0 · requests 2.32.2): requests 2.32 는 import 시점에 SSL 컨텍스트를
    만들어 두고, 옛 truststore 는 그것을 갈아끼우지 않아 ``verify_mode`` 설정이 무한
    재귀에 빠졌다. 새 버전(실측 0.10.4)은 ``inject_into_ssl`` 이 그것까지 바꾼다.
    버전 번호가 아니라 **결과**를 본다 — 어느 버전에서 고쳐졌는지 추측하지 않는다.
    """
    trust_cls = getattr(truststore, "SSLContext", None)
    adapters = sys.modules.get("requests.adapters")
    preloaded = getattr(adapters, "_preloaded_ssl_context", None) if adapters else None
    if trust_cls is None or preloaded is None:
        return False
    return not isinstance(preloaded, trust_cls)


# --------------------------------------------------------------------------- #
# 정책
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Endpoint:
    key: str          # 사람이 고칠 설정 키
    verify: bool
    host: Optional[str]


def _endpoints(llm: Any) -> list[_Endpoint]:
    """TLS 로 붙는 접속 정보들. chat 과 임베딩이 다른 주소·다른 설정일 수 있다."""
    backend = (llm.backend or "").lower()
    embed_backend = (llm.embed_backend or backend).lower()
    chat_remote = backend in REMOTE_BACKENDS
    found: list[_Endpoint] = []
    if chat_remote:
        found.append(_endpoint("llm.internal", llm.internal))
    if embed_backend in REMOTE_BACKENDS:
        # embed_internal 을 안 적으면 internal 을 그대로 물려받는다 — 그때 사람이 고칠
        # 곳은 internal 이고, chat 이 이미 그 접속이면 같은 것을 두 번 셀 이유가 없다.
        if llm.embed_internal != llm.internal:
            found.append(_endpoint("llm.embed_internal", llm.embed_internal))
        elif not chat_remote:
            found.append(_endpoint("llm.internal", llm.internal))
    return found


def _endpoint(key: str, cfg: Any) -> _Endpoint:
    return _Endpoint(key=key, verify=bool(cfg.verify_ssl),
                     host=urlparse(cfg.base_url or "").hostname)


def _mode(endpoints: list[_Endpoint]) -> str:
    """주입하지 않고 현재 상태만 말한다(점검 화면용)."""
    if not endpoints:
        return NOT_APPLICABLE
    flags = {e.verify for e in endpoints}
    if flags == {False}:
        return OFF
    on = OS_STORE if _state.injected else CERTIFI
    return on if flags == {True} else MIXED


def apply_tls_policy(llm: Any) -> str:
    """설정대로 TLS 정책을 적용하고 결과 모드를 돌려준다.

    ``OS_STORE``·``CERTIFI``(truststore 없음/실패)·``OFF``·``MIXED``(chat·임베딩이
    다름)·``NOT_APPLICABLE``(원격 백엔드 없음).
    """
    endpoints = _endpoints(llm)
    if any(e.verify for e in endpoints):
        inject_os_trust_store()
    for e in endpoints:
        if not e.verify:
            _silence_urllib3(e.host)
            _announce_off(e)
    return _mode(endpoints)


def describe(llm: Any) -> str:
    """점검 화면용 한 줄. 아무것도 바꾸지 않는다."""
    endpoints = _endpoints(llm)
    mode = _mode(endpoints)
    if mode == OS_STORE:
        return "켜짐 - OS 인증서 저장소(truststore)"
    if mode == CERTIFI:
        return "켜짐 - certifi(공인 CA 만). 사내 CA 면 `pip install -U truststore` 필요"
    if mode == OFF:
        return "⚠️ 꺼짐 (" + ", ".join(f"{e.key}.verify_ssl: false" for e in endpoints) + ")"
    if mode == MIXED:
        return "일부 꺼짐 (" + ", ".join(
            f"{e.key}.verify_ssl: {str(e.verify).lower()}" for e in endpoints) + ")"
    return "해당 없음(원격 백엔드 아님)"


def _silence_urllib3(host: Optional[str]) -> None:
    """이 호스트에 대한 urllib3 ``InsecureRequestWarning`` 만 끈다."""
    if not host:
        return
    try:
        from urllib3.exceptions import InsecureRequestWarning  # noqa: WPS433
    except ImportError:  # pragma: no cover - requests 의 의존성이라 항상 있다
        return
    warnings.filterwarnings(
        "ignore",
        message=rf"Unverified HTTPS request is being made to host '{re.escape(host)}'",
        category=InsecureRequestWarning,
    )


def _announce_off(endpoint: _Endpoint) -> None:
    """검증 꺼짐을 우리 말로 한 번. cp949 콘솔에서도 안 사라지게 ASCII 구두점만 쓴다."""
    if endpoint.key in _state.announced:
        return
    _state.announced.add(endpoint.key)
    logger.warning(
        "사내 LLM 호출의 SSL 인증서 검증이 꺼져 있습니다(%s.verify_ssl: false, host=%s). "
        "브라우저로 이 주소가 인증서 경고 없이 열리는 PC 라면 true 로 켜세요 "
        "- OS 인증서 저장소(truststore)로 사내 CA 를 신뢰합니다.",
        endpoint.key, endpoint.host or "?",
    )
