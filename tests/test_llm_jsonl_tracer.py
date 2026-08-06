"""로컬 LLM 추적(:class:`JsonlTracer`) 테스트 — 서버·SDK·네트워크 불필요.

이 계측의 존재 이유는 **"LLM 이 JSON 형식은 지켰는데 내용이 틀렸다"를 디버깅**하는
것이다. 그러려면 프롬프트와 응답 원문이 남아야 하고, 특히 실패한 호출의 응답이
살아 있어야 한다. 그래서 아래 테스트의 중심은 (a) 원문 보존, (b) 기록이 판정에
영향을 주지 않음, (c) 꺼져 있으면 아무 일도 없음 셋이다.
"""

from __future__ import annotations

import json

import pytest

from contentcompare.config import AppConfig
from contentcompare.llm.tracing import (
    GenerationEvent,
    JsonlTracer,
    MultiTracer,
    NullTracer,
    TracedChat,
    build_tracer,
    tracing_enabled,
    wrap_chat,
)


class _Chat:
    """고정 응답 chat. 호출 인자를 그대로 기록해 래퍼가 변형하지 않았음을 본다."""

    def __init__(self, reply: str = '{"ok": true}') -> None:
        self.reply = reply
        self.seen: list[tuple] = []

    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> str:
        self.seen.append((system, user, temperature))
        return self.reply


class _BoomChat:
    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> str:
        raise RuntimeError("백엔드 죽음")


def _event(**kw) -> GenerationEvent:
    base = {"stage": "F7 개념 판정", "model": "qwen2.5:14b",
            "system": "너는 판정기다", "user": "쌍을 판정하라", "output": '{"pairs": []}'}
    base.update(kw)
    return GenerationEvent(**base)


def _files(d):
    return sorted(p.name for p in d.iterdir() if p.suffix == ".json")


# --------------------------------------------------------------------------- #
# 기록 내용
# --------------------------------------------------------------------------- #
def test_records_prompt_and_output_verbatim(tmp_path):
    tracer = JsonlTracer(str(tmp_path))
    tracer.start_run("실행1", {"engine": "fact"})
    tracer.record(_event())

    written = json.loads((tmp_path / "실행1" / "001-F7_개념_판정.json").read_text("utf-8"))
    assert written["system"] == "너는 판정기다"
    assert written["user"] == "쌍을 판정하라"
    assert written["output"] == '{"pairs": []}'
    assert written["stage"] == "F7 개념 판정"
    assert written["truncated"] is False


def test_failed_call_output_is_preserved(tmp_path):
    """파싱 실패의 원인은 그 실패한 응답 원문에만 있다 — 반드시 남아야 한다."""
    tracer = JsonlTracer(str(tmp_path))
    tracer.start_run("실행1", {})
    tracer.record(_event(output="죄송합니다. 다음은 결과입니다: ...", error=""))
    tracer.record(_event(output="", error="LLMRequestError: timeout"))

    payloads = [json.loads((tmp_path / "실행1" / n).read_text("utf-8"))
                for n in _files(tmp_path / "실행1") if n != "index.json"]
    assert "죄송합니다" in payloads[0]["output"]
    assert payloads[1]["error"] == "LLMRequestError: timeout"


def test_sequence_numbers_order_the_calls(tmp_path):
    tracer = JsonlTracer(str(tmp_path))
    tracer.start_run("실행1", {})
    for _ in range(3):
        tracer.record(_event())
    names = [n for n in _files(tmp_path / "실행1") if n != "index.json"]
    assert names == ["001-F7_개념_판정.json", "002-F7_개념_판정.json",
                     "003-F7_개념_판정.json"]


def test_max_chars_truncates_and_flags(tmp_path):
    tracer = JsonlTracer(str(tmp_path), max_chars=5)
    tracer.start_run("실행1", {})
    tracer.record(_event(user="1234567890"))

    written = json.loads((tmp_path / "실행1" / "001-F7_개념_판정.json").read_text("utf-8"))
    assert written["user"] == "12345"
    assert written["truncated"] is True


def test_index_lists_only_this_runs_files(tmp_path):
    """이전 실행이 남긴 순번 큰 파일이 뷰어에 섞이면 안 된다."""
    (tmp_path / "실행1").mkdir()
    (tmp_path / "실행1" / "099-예전.json").write_text("{}", encoding="utf-8")

    tracer = JsonlTracer(str(tmp_path))
    tracer.start_run("실행1", {"engine": "fact"})
    tracer.record(_event())
    tracer.end_run()

    index = json.loads((tmp_path / "실행1" / "index.json").read_text("utf-8"))
    assert index["calls"] == 1
    assert index["files"] == ["001-F7_개념_판정.json"]
    assert index["metadata"] == {"engine": "fact"}


def test_record_without_start_run_still_lands_somewhere(tmp_path):
    """연결 점검처럼 trace_run 밖에서 부르는 경로도 기록을 잃지 않는다."""
    tracer = JsonlTracer(str(tmp_path))
    tracer.record(_event())
    assert (tmp_path / "_adhoc" / "001-F7_개념_판정.json").exists()


def test_stage_name_with_path_characters_is_slugged(tmp_path):
    tracer = JsonlTracer(str(tmp_path))
    tracer.start_run("a/b:c", {})
    tracer.record(_event(stage="F1 profile · 기준.xlsx"))
    assert (tmp_path / "a_b_c").is_dir()
    assert (tmp_path / "a_b_c" / "001-F1_profile___기준_xlsx.json").exists()


# --------------------------------------------------------------------------- #
# 래퍼가 판정에 영향을 주지 않는다
# --------------------------------------------------------------------------- #
def test_wrapped_chat_returns_identical_value(tmp_path):
    chat = _Chat('{"verdict": "same"}')
    traced = TracedChat(chat, model="m", backend="ollama",
                        tracer=JsonlTracer(str(tmp_path)))
    assert traced.complete("S", "U", temperature=0.3) == chat.complete("S", "U",
                                                                      temperature=0.3)
    assert chat.seen[0] == ("S", "U", 0.3)  # 인자도 변형되지 않는다


def test_exception_is_reraised_and_recorded(tmp_path):
    tracer = JsonlTracer(str(tmp_path))
    tracer.start_run("실행1", {})
    traced = TracedChat(_BoomChat(), model="m", backend="ollama", tracer=tracer)

    with pytest.raises(RuntimeError):
        traced.complete("S", "U")

    written = json.loads(next((tmp_path / "실행1").glob("001-*.json")).read_text("utf-8"))
    assert "백엔드 죽음" in written["error"]


# --------------------------------------------------------------------------- #
# MultiTracer — 한쪽 실패가 다른 쪽을 죽이지 않는다
# --------------------------------------------------------------------------- #
class _BrokenTracer:
    active = True

    def start_run(self, name, metadata): raise RuntimeError("나쁜 tracer")
    def end_run(self): raise RuntimeError("나쁜 tracer")
    def record(self, event): raise RuntimeError("나쁜 tracer")
    def flush(self): raise RuntimeError("나쁜 tracer")


def test_multi_tracer_survives_a_broken_member(tmp_path):
    local = JsonlTracer(str(tmp_path))
    multi = MultiTracer([_BrokenTracer(), local])
    multi.start_run("실행1", {})
    multi.record(_event())
    multi.flush()

    assert (tmp_path / "실행1" / "001-F7_개념_판정.json").exists()


def test_multi_tracer_drops_the_broken_one_after_first_failure(tmp_path):
    broken = _BrokenTracer()
    multi = MultiTracer([broken, JsonlTracer(str(tmp_path))])
    multi.start_run("실행1", {})
    assert broken not in multi._tracers


# --------------------------------------------------------------------------- #
# 설정 분기 — 꺼져 있으면 아무 일도 없다
# --------------------------------------------------------------------------- #
def test_disabled_by_default():
    cfg = AppConfig()
    assert tracing_enabled(cfg) is False
    assert isinstance(build_tracer(cfg), NullTracer)


def test_local_only_builds_jsonl_tracer(tmp_path):
    cfg = AppConfig()
    cfg.llm.trace_local = True
    cfg.llm.trace_dir = str(tmp_path)
    assert tracing_enabled(cfg) is True
    assert isinstance(build_tracer(cfg), JsonlTracer)


def test_disabled_tracer_writes_nothing(tmp_path):
    cfg = AppConfig()
    cfg.llm.trace_dir = str(tmp_path)
    tracer = build_tracer(cfg)
    tracer.start_run("실행1", {})
    tracer.record(_event())
    assert list(tmp_path.iterdir()) == []


def test_factory_wraps_chat_when_local_tracing_is_on(tmp_path):
    """래핑 지점은 ``build_clients`` 한 곳뿐 — 여기가 끊기면 아무것도 기록되지 않는다."""
    from contentcompare.llm.factory import build_clients
    from contentcompare.llm.tracing import reset_tracer

    cfg = AppConfig()
    cfg.llm.trace_local = True
    cfg.llm.trace_dir = str(tmp_path)
    reset_tracer()
    try:
        chat, _embed = build_clients(cfg)
        assert isinstance(chat, TracedChat)
    finally:
        reset_tracer()


def test_factory_returns_bare_client_when_tracing_is_off():
    """설정을 건드리지 않은 사용자에게는 **오늘과 동일 객체**여야 한다."""
    from contentcompare.llm.factory import build_clients
    from contentcompare.llm.tracing import reset_tracer

    reset_tracer()
    try:
        chat, _embed = build_clients(AppConfig())
        assert not isinstance(chat, TracedChat)
    finally:
        reset_tracer()


def test_wrap_chat_delegates_unknown_attributes(tmp_path):
    """chat 과 embed 가 같은 객체인 백엔드에서 임베딩이 깨지지 않아야 한다."""
    class _Both(_Chat):
        def embed(self, texts, kind="passage"):
            return [[1.0] for _ in texts]

    cfg = AppConfig()
    cfg.llm.trace_local = True
    cfg.llm.trace_dir = str(tmp_path)
    traced = wrap_chat(_Both(), cfg, tracer=JsonlTracer(str(tmp_path)))
    assert traced.embed(["a", "b"]) == [[1.0], [1.0]]
