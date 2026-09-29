"""사내 LLM 호출의 TLS 정책(:mod:`contentcompare.llm.tls`) 테스트.

실측 계기: 사내 PC 에서 F7 개념 판정을 시작할 때마다 urllib3 의
``InsecureRequestWarning`` 이 화면에 떴다. F7 이 fact 파이프라인에서 **임베딩을 처음
부르는 단계**이고, 임베딩이 ``internal`` 백엔드(requests + ``verify=False``)로 나가서다.
chat 은 langchain(httpx)이라 같은 "검증 꺼짐"인데도 조용했다 — 백엔드마다 알림이
달랐던 것이 문제다.

두 갈래를 고정한다:

- ``verify_ssl: true`` → OS 인증서 저장소(truststore)를 **클라이언트를 만들기 전에**
  주입한다. 브라우저로 게이트웨이가 열리는 PC 면 사내 루트가 이미 거기 있다.
- ``verify_ssl: false`` → 사람이 의도해서 끈 것이다. urllib3 의 경고를 **그 호스트에
  대해서만** 끄고, 대신 우리 말로 **한 번** 알린다(백엔드와 무관하게).

``truststore`` 와 ``warnings`` 필터는 둘 다 프로세스 전역이라 테스트는 가짜 모듈과
``warnings.catch_warnings()`` 로 격리한다.
"""

from __future__ import annotations

import logging
import sys
import types
import warnings

import pytest

from contentcompare.config import AppConfig
from contentcompare.llm import tls

CHAT_URL = "https://chat.intra.corp/v1"
EMBED_URL = "https://api-genai.intra.corp/api/llm/openai/v1"
UNVERIFIED = "Unverified HTTPS request is being made to host '{}'. Adding certificate verification is strongly advised."


@pytest.fixture
def fake_truststore(monkeypatch):
    mod = types.ModuleType("truststore")
    mod.calls = []
    mod.inject_into_ssl = lambda: mod.calls.append("injected")
    monkeypatch.setitem(sys.modules, "truststore", mod)
    return mod


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    """한 번만 알리는 플래그는 프로세스 전역이다 — 테스트마다 처음 상태로."""
    monkeypatch.setattr(tls, "_state", tls._State())


def _config(*, backend="langchain", embed_backend="", verify=False, embed=None):
    raw = {"llm": {"backend": backend, "embed_backend": embed_backend,
                   "internal": {"base_url": CHAT_URL, "verify_ssl": verify}}}
    if embed is not None:
        raw["llm"]["embed_internal"] = embed
    return AppConfig.from_dict(raw)


def _insecure_warning():
    from urllib3.exceptions import InsecureRequestWarning

    return InsecureRequestWarning


def _recorded_after(config, host):
    """정책을 적용한 뒤 urllib3 가 ``host`` 에 대해 경고를 내면 기록되는가."""
    category = _insecure_warning()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        tls.apply_tls_policy(config.llm)
        warnings.warn(UNVERIFIED.format(host), category)
    return [w for w in caught if issubclass(w.category, category)]


# --------------------------------------------------------------------------- #
# 검증 켬 → OS 인증서 저장소
# --------------------------------------------------------------------------- #
def test_verification_on_uses_the_os_trust_store(fake_truststore):
    assert tls.apply_tls_policy(_config(verify=True).llm) == tls.OS_STORE
    assert fake_truststore.calls == ["injected"]


def test_missing_truststore_falls_back_to_certifi_without_crashing(monkeypatch):
    monkeypatch.setitem(sys.modules, "truststore", None)   # import 시 ImportError
    assert tls.apply_tls_policy(_config(verify=True).llm) == tls.CERTIFI


def test_broken_truststore_falls_back_to_certifi(monkeypatch, caplog):
    mod = types.ModuleType("truststore")

    def boom():
        raise RuntimeError("이 플랫폼에서는 못 씁니다")

    mod.inject_into_ssl = boom
    monkeypatch.setitem(sys.modules, "truststore", mod)
    with caplog.at_level(logging.WARNING, logger="contentcompare.llm.tls"):
        assert tls.apply_tls_policy(_config(verify=True).llm) == tls.CERTIFI
    assert "이 플랫폼에서는 못 씁니다" in caplog.text


def test_old_truststore_that_breaks_requests_is_rolled_back(monkeypatch, caplog):
    """실측: truststore 0.8.0 + requests 2.32 에서 ``requests.get`` 이 RecursionError 로 죽었다.

    requests 2.32 는 import 시점에 SSL 컨텍스트를 미리 만들어 두는데, 옛 truststore 는
    그것을 갈아끼우지 않는다 — 우리 코드는 ``llm/http.py`` 가 requests 를 **먼저** import
    하므로 정확히 이 조건이다. 켜 두면 비교 실행이 통째로 죽으므로 되돌리고 알린다.
    """
    class TrustContext:   # truststore.SSLContext 자리
        pass

    mod = types.ModuleType("truststore")
    mod.SSLContext = TrustContext
    mod.calls = []
    mod.inject_into_ssl = lambda: mod.calls.append("injected")   # 옛 버전: preloaded 를 안 바꿈
    mod.extract_from_ssl = lambda: mod.calls.append("extracted")
    mod.__version__ = "0.8.0"
    monkeypatch.setitem(sys.modules, "truststore", mod)
    adapters = types.ModuleType("requests.adapters")
    adapters._preloaded_ssl_context = object()                    # 주입 전에 만들어진 평범한 컨텍스트
    monkeypatch.setitem(sys.modules, "requests.adapters", adapters)

    with caplog.at_level(logging.WARNING, logger="contentcompare.llm.tls"):
        assert tls.apply_tls_policy(_config(verify=True).llm) == tls.CERTIFI
    assert mod.calls == ["injected", "extracted"]
    assert "pip install -U truststore" in caplog.text
    assert "0.8.0" in caplog.text


def test_new_truststore_that_patches_requests_is_kept(monkeypatch):
    class TrustContext:
        pass

    mod = types.ModuleType("truststore")
    mod.SSLContext = TrustContext
    adapters = types.ModuleType("requests.adapters")
    adapters._preloaded_ssl_context = object()

    def inject():   # 새 버전: preloaded 까지 truststore 것으로 바꾼다
        adapters._preloaded_ssl_context = TrustContext()

    mod.inject_into_ssl = inject
    mod.extract_from_ssl = lambda: pytest.fail("되돌리면 안 된다")
    monkeypatch.setitem(sys.modules, "truststore", mod)
    monkeypatch.setitem(sys.modules, "requests.adapters", adapters)
    assert tls.apply_tls_policy(_config(verify=True).llm) == tls.OS_STORE


# --------------------------------------------------------------------------- #
# 검증 끔 → urllib3 경고 대신 우리 말로 한 번
# --------------------------------------------------------------------------- #
def test_verification_off_silences_urllib3_only_for_the_configured_host(fake_truststore):
    config = _config(verify=False)
    assert _recorded_after(config, "chat.intra.corp") == []
    # 설정에 없는 호스트의 무검증 요청은 여전히 경고한다 — 우리가 모르는 것까지 덮지 않는다.
    assert len(_recorded_after(config, "somewhere.else")) == 1
    assert fake_truststore.calls == []


def test_verification_off_is_announced_once_in_our_words(caplog):
    config = _config(verify=False)
    with caplog.at_level(logging.WARNING, logger="contentcompare.llm.tls"):
        with warnings.catch_warnings():
            assert tls.apply_tls_policy(config.llm) == tls.OFF
            tls.apply_tls_policy(config.llm)   # Streamlit 처럼 한 프로세스에서 여러 번
    notices = [r for r in caplog.records if "llm.internal.verify_ssl" in r.getMessage()]
    assert len(notices) == 1
    assert "chat.intra.corp" in notices[0].getMessage()


def test_notice_survives_a_cp949_console(caplog):
    """``log_print``·콘솔이 cp949 인 사내 PC 에서 줄이 통째로 사라지면 안 된다."""
    with caplog.at_level(logging.WARNING, logger="contentcompare.llm.tls"):
        with warnings.catch_warnings():
            tls.apply_tls_policy(_config(verify=False).llm)
    for record in caplog.records:
        record.getMessage().encode("cp949")


# --------------------------------------------------------------------------- #
# chat/임베딩 분리 — 실측 구성(chat=langchain, embed=internal)
# --------------------------------------------------------------------------- #
def test_split_endpoints_are_judged_separately(fake_truststore, caplog):
    config = _config(backend="langchain", embed_backend="internal", verify=True,
                     embed={"base_url": EMBED_URL, "verify_ssl": False})
    with caplog.at_level(logging.WARNING, logger="contentcompare.llm.tls"):
        assert _recorded_after(config, "api-genai.intra.corp") == []
    assert fake_truststore.calls == ["injected"]          # chat 은 검증을 켰다
    assert "llm.embed_internal.verify_ssl" in caplog.text  # 어느 설정인지 짚는다


def test_local_backends_are_left_alone(fake_truststore, caplog):
    config = _config(backend="ollama", embed_backend="fastembed", verify=False)
    with caplog.at_level(logging.WARNING, logger="contentcompare.llm.tls"):
        assert tls.apply_tls_policy(config.llm) == tls.NOT_APPLICABLE
    assert fake_truststore.calls == []
    assert caplog.records == []


# --------------------------------------------------------------------------- #
# 호출 경로 — "설정에는 있는데 호출 경로에는 없다"를 막는다
# --------------------------------------------------------------------------- #
def test_build_clients_applies_the_policy_before_making_clients(fake_truststore, monkeypatch):
    """주입은 클라이언트 **생성 전**이어야 httpx 가 만드는 SSL 컨텍스트까지 덮는다."""
    from contentcompare.llm import factory

    order = []
    fake_truststore.inject_into_ssl = lambda: order.append("inject")
    real_make = factory._make

    def spy_make(backend, llm):
        order.append(f"make:{backend}")
        return real_make(backend, llm)

    monkeypatch.setattr(factory, "_make", spy_make)
    config = _config(backend="internal", verify=True)
    config.logging.timeline = False
    config.llm.rate_limit_wait = 0
    factory.build_clients(config)
    assert order[0] == "inject"
    assert "make:internal" in order


def test_health_check_reports_the_tls_mode(fake_truststore):
    from contentcompare.llm.health import check_llm

    class OkChat:
        def complete(self, system, user, *, temperature=0.0):
            return "OK"

    class OkEmbed:
        def embed(self, texts):
            return [[0.1] for _ in texts]

    with warnings.catch_warnings():
        results = check_llm(_config(verify=False), chat_client=OkChat(), embed_client=OkEmbed())
    line = next(r for r in results if r.name == "SSL 인증서 검증")
    assert line.ok
    assert "꺼짐" in line.detail


def test_health_check_explains_certificate_failures():
    from contentcompare.llm.health import check_llm

    class CertChat:
        def complete(self, system, user, *, temperature=0.0):
            raise ConnectionError("[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate in chain")

    class OkEmbed:
        def embed(self, texts):
            return [[0.1] for _ in texts]

    with warnings.catch_warnings():
        results = check_llm(_config(verify=True), chat_client=CertChat(), embed_client=OkEmbed())
    chat = next(r for r in results if r.name.startswith("chat"))
    assert not chat.ok
    assert "truststore" in chat.detail
    assert "verify_ssl" in chat.detail
