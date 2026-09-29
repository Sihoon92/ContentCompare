"""Langfuse 추적 — 설정 해석과 chat 래퍼(네트워크·SDK 불필요).

이 파이프라인의 LLM 디버깅은 로그 파일(1000자 절단)과 최종 산출물 JSON 뿐이라
"어떤 프롬프트를 넣어 무엇을 받았는지"를 볼 수 없었다. Langfuse 연동은 그 공백을
메운다. 테스트는 기존 규약대로 **가짜 tracer 주입**으로 플랫폼·네트워크 독립을 지킨다.

핵심 불변식 두 가지를 여기서 고정한다:

1. **미설정이면 아무것도 바뀌지 않는다.** ``build_clients`` 가 오늘과 **동일 객체**를
   돌려줘야 한다 — 설정을 건드리지 않은 사용자에게 동작·의존성 변화가 0 이어야 한다.
2. **추적 실패가 비교를 죽이지 않는다.** tracer 가 무슨 짓을 해도 ``complete()`` 는
   정상 반환해야 한다(FactPipeline 의 문서 단위 격리와 같은 원칙).
"""

import os
import sys

import pytest

from contentcompare.config import AppConfig, LangfuseConfig

HOST = "http://langfuse.intra.corp"
PUB = "pk-lf-test"
SEC = "sk-lf-secret-value"


@pytest.fixture(autouse=True)
def _isolate_ssl_env():
    """``apply_ssl_env`` 는 ``os.environ`` 을 **직접** 고친다 — 테스트 뒤 되돌린다.

    복원하지 않으면 ``SSL_CERT_FILE`` 이 이미 지워진 tmp_path 인증서를 가리킨 채
    남아, 이후 TLS 를 건드리는 모든 테스트가 ``[X509] PEM lib`` 로 죽는다(실측:
    타임라인의 httpx 클라이언트 테스트가 이 유출로 실패했다). 프로덕션에서는 프로세스
    수명 동안 유지되는 것이 의도된 동작이므로 코드가 아니라 테스트에서 격리한다.
    """
    saved = {k: os.environ.get(k) for k in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE")}
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture(autouse=True)
def _isolate_tracer_cache():
    """``get_tracer`` 는 프로세스당 하나를 캐시한다 — 테스트 간에 새지 않게 비운다."""
    from contentcompare.llm.tracing import reset_tracer

    reset_tracer()
    yield
    reset_tracer()


# --------------------------------------------------------------------- #
# 설정 해석
# --------------------------------------------------------------------- #
def test_blank_config_is_inactive():
    """기본값(전부 빈 값)이면 비활성 — 아무도 켜지 않았는데 켜지면 안 된다."""
    assert LangfuseConfig().is_active() is False


def test_direct_values_activate():
    cfg = LangfuseConfig(host=HOST, public_key=PUB, secret_key=SEC)
    assert cfg.is_active() is True
    assert cfg.resolved() == (HOST, PUB, SEC)


def test_partial_config_is_inactive():
    """셋 중 하나라도 비면 비활성 — 반쯤 켜진 상태로 실행되면 원인 추적이 어렵다."""
    assert LangfuseConfig(host=HOST, public_key=PUB).is_active() is False
    assert LangfuseConfig(public_key=PUB, secret_key=SEC).is_active() is False


def test_keys_fall_back_to_environment(monkeypatch):
    """``internal.api_key_env`` 와 같은 규약 — 시크릿을 파일에 적지 않아도 된다."""
    monkeypatch.setenv("LF_PUB", PUB)
    monkeypatch.setenv("LF_SEC", SEC)
    cfg = LangfuseConfig(host=HOST, public_key_env="LF_PUB", secret_key_env="LF_SEC")
    assert cfg.resolved() == (HOST, PUB, SEC)
    assert cfg.is_active() is True


def test_direct_value_wins_over_environment(monkeypatch):
    monkeypatch.setenv("LF_PUB", "pk-from-env")
    cfg = LangfuseConfig(host=HOST, public_key=PUB, public_key_env="LF_PUB",
                         secret_key=SEC)
    assert cfg.resolved()[1] == PUB


def test_enabled_false_overrides_complete_keys():
    """키가 다 있어도 스위치 한 번으로 끌 수 있어야 한다."""
    cfg = LangfuseConfig(host=HOST, public_key=PUB, secret_key=SEC, enabled=False)
    assert cfg.is_active() is False


def test_config_is_read_from_yaml_shape():
    """``ollama``/``internal`` 과 같은 nested 규약으로 읽힌다."""
    cfg = AppConfig.from_dict({
        "llm": {"backend": "internal",
                "langfuse": {"host": HOST, "public_key": PUB, "secret_key": SEC}}
    })
    assert cfg.llm.langfuse.is_active() is True
    assert cfg.llm.backend == "internal"      # 형제 키가 망가지지 않는다


def test_missing_langfuse_section_uses_defaults():
    cfg = AppConfig.from_dict({"llm": {"backend": "ollama"}})
    assert cfg.llm.langfuse.is_active() is False


# --------------------------------------------------------------------- #
# chat 래퍼
# --------------------------------------------------------------------- #
class _FakeChat:
    """LLMClient 최소 구현 — 호출을 기록하고 정해진 답을 준다."""

    def __init__(self, reply: str = "OK", error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.seen: list[tuple[str, str, float]] = []

    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> str:
        self.seen.append((system, user, temperature))
        if self.error is not None:
            raise self.error
        return self.reply


class _FakeTracer:
    """수집만 하는 tracer. ``boom`` 이면 매 호출마다 터진다(격리 검증용)."""

    def __init__(self, boom: bool = False) -> None:
        self.boom = boom
        self.events: list = []
        self.runs: list = []
        self.flushed = 0

    def start_run(self, name, metadata):
        if self.boom:
            raise RuntimeError("tracer down")
        self.runs.append((name, metadata))

    def end_run(self):
        if self.boom:
            raise RuntimeError("tracer down")

    def record(self, event):
        if self.boom:
            raise RuntimeError("tracer down")
        self.events.append(event)

    def flush(self):
        self.flushed += 1


def _active_config() -> AppConfig:
    return AppConfig.from_dict({
        # rate_limit_wait 0 — 이 파일의 주제는 추적이라 한도 래퍼를 끈다
        # (켜면 RateLimitedChat 이 바깥에 붙어 "무엇으로 감쌌나"가 가려진다).
        "llm": {"backend": "ollama", "chat_model": "gemma4:12b", "rate_limit_wait": 0,
                "langfuse": {"host": HOST, "public_key": PUB, "secret_key": SEC}}
    })


def test_active_config_records_one_generation_per_call():
    from contentcompare.llm.tracing import wrap_chat

    chat, tracer = _FakeChat("응답입니다"), _FakeTracer()
    traced = wrap_chat(chat, _active_config(), tracer=tracer)

    out = traced.complete("시스템 지시", "사용자 프롬프트", temperature=0.3)

    assert out == "응답입니다"                        # 원래 응답을 그대로 통과시킨다
    assert chat.seen == [("시스템 지시", "사용자 프롬프트", 0.3)]  # 인자 변형 없음
    assert len(tracer.events) == 1
    ev = tracer.events[0]
    assert ev.system == "시스템 지시"                 # 프롬프트가 잘리지 않고 원문 그대로
    assert ev.user == "사용자 프롬프트"
    assert ev.output == "응답입니다"
    assert ev.model == "gemma4:12b"
    assert ev.error is None


def test_stage_name_is_attached():
    """단계 이름이 붙어야 Langfuse UI 에서 F7 개념 판정만 골라 볼 수 있다."""
    from contentcompare.llm.tracing import stage, wrap_chat

    tracer = _FakeTracer()
    traced = wrap_chat(_FakeChat(), _active_config(), tracer=tracer)
    with stage("F7 개념 판정"):
        traced.complete("s", "u")
    assert tracer.events[0].stage == "F7 개념 판정"


def test_stage_falls_back_to_caller_module():
    """명시하지 않아도 호출자 모듈명이 붙는다.

    ``comparison/`` 은 코드 무수정 원칙이라 ``stage()`` 를 심을 수 없다. 그래도
    이름 없이 뭉뚱그려지면 안 되므로 호출자 모듈로 폴백한다.
    """
    from contentcompare.llm.tracing import wrap_chat

    tracer = _FakeTracer()
    traced = wrap_chat(_FakeChat(), _active_config(), tracer=tracer)
    traced.complete("s", "u")
    assert "test_llm_tracing" in tracer.events[0].stage


def test_tracer_failure_does_not_break_the_call():
    """추적이 죽어도 비교는 끝까지 간다 — FactPipeline 의 격리 원칙과 같다."""
    from contentcompare.llm.tracing import wrap_chat

    chat, tracer = _FakeChat("정상 응답"), _FakeTracer(boom=True)
    traced = wrap_chat(chat, _active_config(), tracer=tracer)

    assert traced.complete("s", "u") == "정상 응답"
    assert traced.complete("s", "u") == "정상 응답"
    assert tracer.events == []


def test_tracer_is_disabled_after_first_failure():
    """첫 실패 후 no-op 으로 강등 — 매 호출 경고가 쏟아지면 로그가 못 쓰게 된다."""
    from contentcompare.llm.tracing import wrap_chat

    class _CountingTracer(_FakeTracer):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        def record(self, event):
            self.attempts += 1
            raise RuntimeError("tracer down")

    tracer = _CountingTracer()
    traced = wrap_chat(_FakeChat(), _active_config(), tracer=tracer)
    for _ in range(5):
        traced.complete("s", "u")
    assert tracer.attempts == 1


def test_client_error_is_recorded_and_reraised():
    """LLM 자체가 터진 것은 삼키지 않는다 — 다만 그 사실도 trace 에 남는다."""
    from contentcompare.llm.tracing import wrap_chat

    boom = RuntimeError("연결 실패")
    tracer = _FakeTracer()
    traced = wrap_chat(_FakeChat(error=boom), _active_config(), tracer=tracer)

    with pytest.raises(RuntimeError, match="연결 실패"):
        traced.complete("s", "u")
    assert len(tracer.events) == 1
    assert "연결 실패" in tracer.events[0].error


def test_secret_key_never_appears_in_recorded_data():
    """시크릿이 trace 페이로드로 새면 안 된다."""
    from contentcompare.llm.tracing import run_metadata, wrap_chat

    tracer = _FakeTracer()
    config = _active_config()
    traced = wrap_chat(_FakeChat(), config, tracer=tracer)
    traced.complete("s", "u")

    blob = repr(tracer.events[0]) + repr(run_metadata(config, "fact", "기준.xlsx", ["대상.docx"]))
    assert SEC not in blob
    assert PUB not in blob


def test_run_metadata_carries_context_but_not_keys():
    from contentcompare.llm.tracing import run_metadata

    meta = run_metadata(_active_config(), "fact", "기준.xlsx", ["A.docx", "B.pptx"])
    assert meta["engine"] == "fact"
    assert meta["reference"] == "기준.xlsx"
    assert meta["targets"] == ["A.docx", "B.pptx"]
    assert meta["chat_model"] == "gemma4:12b"
    assert "secret_key" not in meta


# --------------------------------------------------------------------- #
# SDK 부재 / 비활성
# --------------------------------------------------------------------- #
def test_missing_sdk_degrades_to_noop():
    """langfuse 미설치인데 설정이 켜져 있어도 실행은 계속돼야 한다.

    이 저장소의 테스트 환경에는 langfuse 가 없으므로 이 경로가 실제로 탄다.
    """
    from contentcompare.llm.tracing import build_tracer, wrap_chat

    tracer = build_tracer(_active_config())      # 예외를 던지면 안 된다
    traced = wrap_chat(_FakeChat("응답"), _active_config(), tracer=tracer)
    assert traced.complete("s", "u") == "응답"


# --------------------------------------------------------------------- #
# 사설 CA (사내 인증서)
# --------------------------------------------------------------------- #
DER = b"\x30\x82\x01\xa2\x02\x01\x00\x30\x0d"      # 바이너리 .cer 의 시작 바이트
PEM = b"-----BEGIN CERTIFICATE-----\nMIIBogIBADAN\n-----END CERTIFICATE-----\n"
"""형식 판별용 가짜 PEM. 경로/형식 검사에는 이걸로 충분하다."""


def _cert(tmp_path, name="corp.cer", data=None):
    p = tmp_path / name
    p.write_bytes(PEM if data is None else data)
    return str(p)


@pytest.fixture
def real_cert(tmp_path):
    """**실제로 로드 가능한** 자체서명 인증서 경로.

    모양만 PEM 인 문자열은 OpenSSL 이 ``[X509] PEM lib`` 로 거부하므로 "인증서를
    물린 httpx 클라이언트" 경로는 진짜 인증서가 아니면 검증할 수 없다.
    ``cryptography`` 는 dev extra 로 들어 있고, 없으면 이 두 건만 건너뛴다
    (나머지 테스트는 의존성 없이 돈다 — 플랫폼 독립 규약).
    """
    import datetime

    x509 = pytest.importorskip("cryptography.x509")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Corp Test CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    path = tmp_path / "corp-ca.pem"
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(path)


def test_ca_bundle_accepts_a_pem_certificate(tmp_path):
    from contentcompare.llm.tracing import ca_bundle

    path = _cert(tmp_path)
    cfg = LangfuseConfig(host=HOST, public_key=PUB, secret_key=SEC, ssl_cert=path)
    assert ca_bundle(cfg) == path


def test_ca_bundle_rejects_a_missing_file(tmp_path, caplog):
    """경로 오타를 조용히 넘기면 '왜 여전히 SSL 오류지'로 시간을 잃는다."""
    from contentcompare.llm.tracing import ca_bundle

    cfg = LangfuseConfig(host=HOST, public_key=PUB, secret_key=SEC,
                         ssl_cert=str(tmp_path / "없는파일.cer"))
    assert ca_bundle(cfg) is None
    assert "찾을 수 없" in caplog.text


def test_ca_bundle_rejects_a_der_certificate(tmp_path, caplog):
    """``.cer`` 는 DER(바이너리)인 경우가 많은데 requests/httpx 는 PEM 만 읽는다.

    이걸 안 걸러주면 '파일은 맞는데 왜 안 되지'가 된다 — 변환 명령까지 안내한다.
    """
    from contentcompare.llm.tracing import ca_bundle

    cfg = LangfuseConfig(host=HOST, public_key=PUB, secret_key=SEC,
                         ssl_cert=_cert(tmp_path, data=DER))
    assert ca_bundle(cfg) is None
    assert "PEM" in caplog.text and "openssl" in caplog.text


def test_apply_ssl_env_points_requests_and_openssl_at_the_bundle(tmp_path, monkeypatch):
    """SDK 내부가 requests 든 httpx 든 타도록 환경변수로도 깔아둔다."""
    from contentcompare.llm.tracing import apply_ssl_env

    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    path = _cert(tmp_path)
    apply_ssl_env(LangfuseConfig(host=HOST, public_key=PUB, secret_key=SEC,
                                 ssl_cert=path))
    assert os.environ["REQUESTS_CA_BUNDLE"] == path
    assert os.environ["SSL_CERT_FILE"] == path


def test_apply_ssl_env_does_nothing_without_a_cert(monkeypatch):
    """설정하지 않은 사람의 환경변수를 건드리면 안 된다."""
    from contentcompare.llm.tracing import apply_ssl_env

    monkeypatch.setenv("SSL_CERT_FILE", "기존값")
    apply_ssl_env(LangfuseConfig(host=HOST, public_key=PUB, secret_key=SEC))
    assert os.environ["SSL_CERT_FILE"] == "기존값"


def test_client_is_built_with_httpx_client_when_supported(real_cert):
    """SDK 가 httpx_client 를 받으면 사설 CA 를 문 클라이언트를 넘긴다."""
    from contentcompare.llm.tracing import new_langfuse_client

    seen = {}

    def factory(**kwargs):
        seen.update(kwargs)
        return "client"

    cfg = LangfuseConfig(host=HOST, public_key=PUB, secret_key=SEC,
                         ssl_cert=real_cert)
    assert new_langfuse_client(cfg, factory) == "client"
    assert "httpx_client" in seen


def test_client_falls_back_when_sdk_rejects_httpx_client(real_cert):
    """SDK 버전에 따라 httpx_client 를 안 받을 수 있다 — 그때도 살아남아야 한다.

    환경변수 CA 경로는 이미 깔려 있으므로 인자 없이도 대개 붙는다.
    """
    from contentcompare.llm.tracing import new_langfuse_client

    calls = []

    def picky_factory(**kwargs):
        calls.append(kwargs)
        if "httpx_client" in kwargs:
            raise TypeError("unexpected keyword argument 'httpx_client'")
        return "client"

    cfg = LangfuseConfig(host=HOST, public_key=PUB, secret_key=SEC,
                         ssl_cert=real_cert)
    assert new_langfuse_client(cfg, picky_factory) == "client"
    assert len(calls) == 2 and "httpx_client" not in calls[1]


def test_broken_certificate_does_not_crash_the_run(tmp_path):
    """깨진 인증서를 줘도 실행은 계속된다 — 사유는 로그에 남는다."""
    from contentcompare.llm.tracing import new_langfuse_client

    cfg = LangfuseConfig(host=HOST, public_key=PUB, secret_key=SEC,
                         ssl_cert=_cert(tmp_path))   # 모양만 PEM 인 가짜
    assert new_langfuse_client(cfg, lambda **kw: "client") == "client"


def test_verify_ssl_false_is_the_last_resort(tmp_path):
    """인증서를 못 구했을 때의 탈출구 — 켜져 있으면 경고가 남아야 한다."""
    from contentcompare.llm.tracing import ca_bundle

    cfg = LangfuseConfig(host=HOST, public_key=PUB, secret_key=SEC, verify_ssl=False)
    assert ca_bundle(cfg) is False        # False = 검증 끔 (None = 기본 동작)


# --------------------------------------------------------------------------- #
# OS 인증서 저장소 (truststore)
#
# 사내 PC 는 브라우저가 붙으므로 루트 CA 가 이미 Windows 저장소에 있다. 파이썬만
# certifi(공인 CA)를 봐서 실패하는 것이라, 저장소를 쓰게 하면 PEM 파일을 손으로
# 만드는 절차 자체가 사라진다. 실측: 이걸 넣기 전에는 SDK 가 자기 httpx 클라이언트를
# certifi 로 만들어 CERTIFICATE_VERIFY_FAILED 가 났다.
# --------------------------------------------------------------------------- #
@pytest.fixture
def fake_truststore(monkeypatch):
    """``truststore`` 설치 여부와 무관하게 돌도록 가짜 모듈을 꽂는다."""
    import types

    mod = types.ModuleType("truststore")
    mod.calls = []
    mod.inject_into_ssl = lambda: mod.calls.append("injected")
    monkeypatch.setitem(sys.modules, "truststore", mod)
    return mod


def test_os_trust_store_is_used_when_no_certificate_is_given(fake_truststore):
    """인증서를 지정하지 않은 사람이 기본으로 얻어야 하는 동작."""
    from contentcompare.llm.tracing import use_os_trust_store

    cfg = LangfuseConfig(host=HOST, public_key=PUB, secret_key=SEC)
    assert use_os_trust_store(cfg) is True
    assert fake_truststore.calls == ["injected"]


def test_explicit_certificate_wins_over_the_os_trust_store(fake_truststore, real_cert):
    """사람이 ssl_cert 를 명시했으면 그 의도를 덮지 않는다."""
    from contentcompare.llm.tracing import use_os_trust_store

    cfg = LangfuseConfig(host=HOST, public_key=PUB, secret_key=SEC, ssl_cert=real_cert)
    assert use_os_trust_store(cfg) is False
    assert fake_truststore.calls == []


def test_os_trust_store_is_skipped_when_verification_is_off(fake_truststore):
    """검증을 끄기로 한 사람에게 신뢰 저장소를 들이밀 이유가 없다."""
    from contentcompare.llm.tracing import use_os_trust_store

    cfg = LangfuseConfig(host=HOST, public_key=PUB, secret_key=SEC, verify_ssl=False)
    assert use_os_trust_store(cfg) is False
    assert fake_truststore.calls == []


def test_missing_truststore_does_not_crash_the_run(monkeypatch):
    """선택 의존성이다 — 없으면 예전대로 certifi 로 돈다."""
    from contentcompare.llm.tracing import use_os_trust_store

    monkeypatch.setitem(sys.modules, "truststore", None)   # import 시 ImportError
    cfg = LangfuseConfig(host=HOST, public_key=PUB, secret_key=SEC)
    assert use_os_trust_store(cfg) is False


def test_broken_truststore_does_not_crash_the_run(monkeypatch, caplog):
    """관측 기능의 실패가 비교 실행을 막으면 안 된다 — 이 원칙이 여기에도 적용된다."""
    import types

    from contentcompare.llm.tracing import use_os_trust_store

    mod = types.ModuleType("truststore")

    def boom():
        raise RuntimeError("이 플랫폼에서는 못 씁니다")

    mod.inject_into_ssl = boom
    monkeypatch.setitem(sys.modules, "truststore", mod)
    cfg = LangfuseConfig(host=HOST, public_key=PUB, secret_key=SEC)
    assert use_os_trust_store(cfg) is False


def test_client_creation_reaches_for_the_os_trust_store(fake_truststore):
    """배선 확인 — 클라이언트를 만들기 **전에** 주입돼야 SDK 내부가 그걸 본다."""
    from contentcompare.llm.tracing import new_langfuse_client

    cfg = LangfuseConfig(host=HOST, public_key=PUB, secret_key=SEC)

    def factory(**kwargs):
        assert fake_truststore.calls == ["injected"]   # 생성 시점엔 이미 끝나 있어야
        return "client"

    assert new_langfuse_client(cfg, factory) == "client"


# --------------------------------------------------------------------- #
# 연결 점검(--check)
# --------------------------------------------------------------------- #
def test_health_says_nothing_when_langfuse_is_unused():
    """안 쓰는 사람 화면에 실패 줄이 뜨면 안 된다."""
    from contentcompare.llm.health import _langfuse_result

    assert _langfuse_result(AppConfig.from_dict({"llm": {}})) is None


def test_health_reports_which_key_is_missing():
    """반쯤 설정한 상태를 조용히 넘기면 '왜 trace 가 없지'로 시간을 잃는다."""
    from contentcompare.llm.health import _langfuse_result

    cfg = AppConfig.from_dict({"llm": {"langfuse": {"host": HOST}}})
    r = _langfuse_result(cfg)
    assert r is not None and r.ok is False
    assert "public_key" in r.detail and "secret_key" in r.detail


@pytest.fixture
def fake_langfuse_sdk(monkeypatch):
    """``langfuse`` 모듈을 흉내 내 SDK 설치 이후의 경로를 검증한다.

    로컬에 SDK 가 없어 ``--check`` 가 '미설치'에서 멈추면 정작 사내에서 가장 흔한
    실패(SSL)와 성공 메시지를 한 번도 확인하지 못한 채 배포하게 된다.
    """
    import types

    def _install(behaviour):
        mod = types.ModuleType("langfuse")

        class _Langfuse:
            def __init__(self, **kwargs):
                behaviour(kwargs)

            def auth_check(self):
                return True

        mod.Langfuse = _Langfuse
        monkeypatch.setitem(sys.modules, "langfuse", mod)

    return _install


def test_health_success_line_shows_the_ca_in_use(fake_langfuse_sdk, tmp_path):
    from contentcompare.llm.health import _langfuse_result

    fake_langfuse_sdk(lambda kwargs: None)
    cfg = AppConfig.from_dict({"llm": {"langfuse": {
        "host": HOST, "public_key": PUB, "secret_key": SEC,
        "ssl_cert": _cert(tmp_path, name="SDI_certificate.pem")}}})
    r = _langfuse_result(cfg)
    assert r.ok is True
    assert "SDI_certificate.pem" in r.detail       # 어느 CA 를 쓰는지 보여준다


def test_health_warns_when_verification_is_off(fake_langfuse_sdk):
    """검증을 끈 채로 돌고 있다는 사실이 화면에 남아야 한다."""
    from contentcompare.llm.health import _langfuse_result

    fake_langfuse_sdk(lambda kwargs: None)
    cfg = AppConfig.from_dict({"llm": {"langfuse": {
        "host": HOST, "public_key": PUB, "secret_key": SEC, "verify_ssl": False}}})
    r = _langfuse_result(cfg)
    assert r.ok is True and "검증 꺼짐" in r.detail


def test_health_explains_how_to_fix_a_certificate_error(fake_langfuse_sdk):
    """사내에서 가장 흔한 실패 — 원인만 보여주지 말고 해결책까지 준다."""
    from contentcompare.llm.health import _langfuse_result

    def boom(_kwargs):
        raise Exception(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
            "unable to get local issuer certificate"
        )

    fake_langfuse_sdk(boom)
    cfg = AppConfig.from_dict({"llm": {"langfuse": {
        "host": HOST, "public_key": PUB, "secret_key": SEC}}})
    r = _langfuse_result(cfg)
    assert r.ok is False
    assert "ssl_cert" in r.detail          # 어느 설정을 만져야 하는지
    assert "openssl x509" in r.detail      # DER → PEM 변환 명령까지


def test_health_never_prints_the_secret():
    """점검 결과는 화면과 로그에 그대로 남는다 — 키가 섞이면 안 된다."""
    from contentcompare.llm.health import _langfuse_result

    cfg = AppConfig.from_dict({
        "llm": {"langfuse": {"host": HOST, "public_key": PUB, "secret_key": SEC}}
    })
    r = _langfuse_result(cfg)
    assert r is not None
    assert SEC not in r.line() and PUB not in r.line()


def test_build_clients_returns_the_same_object_when_inactive(monkeypatch):
    """관측을 전부 끄면 래핑조차 하지 않는다 — 그 설정의 사용자에게 변화 0.

    ``is`` 비교여야 의미가 있다. 동등한 새 객체를 돌려주는 것으로는
    "동작이 그대로"를 보장하지 못한다.

    ⚠️ **기본값이 켜진 배선이 둘**이라 여기서 함께 꺼야 한다: ``logging.timeline``
    (추적과 같은 래퍼를 쓴다)과 ``rate_limit_wait``(429 대기 — 429 를 스스로 처리하지
    않는 백엔드면 ``RateLimitedChat`` 이 바깥에 붙는다). 주입한 가짜는 그 선언이
    없으므로 ``backend: ollama`` 여도 붙는다.
    """
    from contentcompare.llm import factory

    sentinel = _FakeChat()
    monkeypatch.setattr(factory, "_make", lambda *_a, **_k: sentinel)
    config = AppConfig.from_dict(
        {"llm": {"backend": "ollama", "rate_limit_wait": 0},
         "logging": {"timeline": False}}
    )
    chat, embed = factory.build_clients(config)
    assert chat is sentinel


def test_build_clients_wraps_chat_when_active(monkeypatch):
    from contentcompare.llm import factory

    sentinel = _FakeChat()
    monkeypatch.setattr(factory, "_make", lambda *_a, **_k: sentinel)
    chat, embed = factory.build_clients(_active_config())
    assert chat is not sentinel
    assert embed is sentinel            # 임베딩은 감싸지 않는다(trace_embeddings=False)
    assert chat.complete("s", "u") == "OK"
