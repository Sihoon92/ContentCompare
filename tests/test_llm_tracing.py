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

import pytest

from contentcompare.config import AppConfig, LangfuseConfig

HOST = "http://langfuse.intra.corp"
PUB = "pk-lf-test"
SEC = "sk-lf-secret-value"


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
        "llm": {"backend": "ollama", "chat_model": "gemma4:12b",
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
    """미설정이면 래핑조차 하지 않는다 — 기존 사용자에게 변화 0.

    ``is`` 비교여야 의미가 있다. 동등한 새 객체를 돌려주는 것으로는
    "동작이 그대로"를 보장하지 못한다.
    """
    from contentcompare.llm import factory

    sentinel = _FakeChat()
    monkeypatch.setattr(factory, "_make", lambda *_a, **_k: sentinel)
    config = AppConfig.from_dict({"llm": {"backend": "ollama"}})
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
