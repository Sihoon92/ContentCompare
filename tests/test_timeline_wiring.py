"""타임라인 배선 — 파이프라인 각 지점이 실제로 이벤트를 흘리는가.

코어(``test_timeline.py``)가 "이벤트를 어떻게 남기고 어떻게 보여 주는가"를 본다면,
이쪽은 **어디서 남기는가**를 본다: 단계 경계·배치 번호·LLM 호출·SDK 내부 재시도·
전송 재시도·한도 대기·JSON 파싱 재시도.

배선이 빠지면 코어가 아무리 멀쩡해도 화면은 조용하다 — 실제로 그렇게 8분을 잃었다.
"""

from __future__ import annotations

import time

import pytest

from contentcompare import timeline as tl


# --------------------------------------------------------------------------- #
# 도구
# --------------------------------------------------------------------------- #
class FakeClock:
    """호출할 때마다 ``step`` 초씩 흐르는 시계(기본 1초)."""

    def __init__(self, start: float = 1_700_000_000.0, step: float = 1.0) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


def _make(tmp_path, **kwargs):
    return tl.Timeline(tmp_path / "run.jsonl", clock=FakeClock(), console=False, **kwargs)


@pytest.fixture(autouse=True)


def _clean_singleton():
    """싱글턴이 테스트 사이에 새지 않게 한다."""
    tl.reset_timeline()
    yield
    tl.reset_timeline()


# --------------------------------------------------------------------------- #
# 단계 경계 (llm.tracing.stage / substage)
# --------------------------------------------------------------------------- #
def test_stage_emits_start_and_end(tmp_path):
    from contentcompare.llm.tracing import stage

    tl.set_timeline(_make(tmp_path))
    with stage("F2 records"):
        pass

    events = tl.load_timeline(tmp_path / "run.jsonl")
    assert [(e.kind, e.name) for e in events] == [
        ("stage_start", "F2 records"),
        ("stage_end", "F2 records"),
    ]
    assert events[1].status == "ok"


def test_stage_records_where_the_exception_happened(tmp_path):
    """에러가 '어느 단계'에서 났는지는 여기서 확정된다 — 역산을 없애는 지점."""
    from contentcompare.llm.tracing import stage

    tl.set_timeline(_make(tmp_path))
    with pytest.raises(TimeoutError):
        with stage("F2 records · 자표준원문.xlsx"):
            raise TimeoutError("Request timed out")

    end = tl.load_timeline(tmp_path / "run.jsonl")[-1]
    assert end.kind == "stage_end"
    assert end.status == "error"
    assert "TimeoutError" in end.detail["error"]
    assert "F2 records" in end.name


def test_substage_joins_the_parent_name(tmp_path):
    from contentcompare.llm.tracing import current_stage, stage, substage

    tl.set_timeline(_make(tmp_path))
    seen = {}
    with stage("F2 records · 자표준원문.xlsx"):
        with substage("배치 1/4"):
            seen["inside"] = current_stage(depth=1)
        seen["after"] = current_stage(depth=1)

    assert seen["inside"] == "F2 records · 자표준원문.xlsx · 배치 1/4"
    assert seen["after"] == "F2 records · 자표준원문.xlsx"


def test_substage_without_parent_stands_alone(tmp_path):
    from contentcompare.llm.tracing import current_stage, substage

    tl.set_timeline(_make(tmp_path))
    with substage("배치 1/4"):
        assert current_stage(depth=1) == "배치 1/4"


def test_stage_is_silent_when_timeline_is_off(tmp_path):
    """꺼져 있으면 기존 동작(ContextVar 설정)만 남는다."""
    from contentcompare.llm.tracing import current_stage, stage

    tl.reset_timeline()
    with stage("F1 profile"):
        assert current_stage(depth=1) == "F1 profile"
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- #
# LLM 호출 (TracedChat)
# --------------------------------------------------------------------------- #
class FakeChat:
    """호출 결과를 미리 정해 두는 chat 클라이언트."""

    def __init__(self, answer: str = "{}", raises: Exception | None = None) -> None:
        self.answer = answer
        self.raises = raises

    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> str:
        if self.raises is not None:
            raise self.raises
        return self.answer


def _traced(tmp_path, inner):
    from contentcompare.llm.tracing import NullTracer, TracedChat

    tl.set_timeline(_make(tmp_path))
    return TracedChat(inner, model="m", backend="fake", tracer=NullTracer())


def test_llm_call_leaves_start_and_end(tmp_path):
    from contentcompare.llm.tracing import stage

    chat = _traced(tmp_path, FakeChat(answer='{"ok": 1}'))
    with stage("F2 records · 배치 1/4"):
        chat.complete("sys", "user prompt")

    kinds = [e.kind for e in tl.load_timeline(tmp_path / "run.jsonl")]
    assert kinds == ["stage_start", "llm_start", "llm_end", "stage_end"]


def test_llm_event_carries_sizes_not_text(tmp_path):
    chat = _traced(tmp_path, FakeChat(answer="1234567890"))
    chat.complete("sys", "0123456789" * 3)

    events = tl.load_timeline(tmp_path / "run.jsonl")
    start = next(e for e in events if e.kind == "llm_start")
    end = next(e for e in events if e.kind == "llm_end")
    assert start.detail["prompt_chars"] == len("sys") + 30
    assert end.detail["output_chars"] == 10
    raw = (tmp_path / "run.jsonl").read_text("utf-8")
    assert "0123456789" not in raw


def test_failed_llm_call_is_marked_and_reraised(tmp_path):
    chat = _traced(tmp_path, FakeChat(raises=TimeoutError("Request timed out")))
    with pytest.raises(TimeoutError):
        chat.complete("sys", "user")

    end = tl.load_timeline(tmp_path / "run.jsonl")[-1]
    assert end.kind == "llm_end"
    assert end.status == "timeout"
    assert "TimeoutError" in end.detail["error"]


# --------------------------------------------------------------------------- #
# 느림 경고 — 타임아웃은 이미 늦은 신호다
# --------------------------------------------------------------------------- #
def test_slow_call_is_warned_before_it_times_out(tmp_path):
    """타임아웃은 이미 늦은 신호다. 죽기 전에 보이는 경고가 조치를 앞당긴다."""
    from contentcompare.llm.tracing import NullTracer, TracedChat

    class Slow:
        def complete(self, system, user, *, temperature=0.0):
            # Windows 의 monotonic 해상도가 ~15ms 라 그보다 넉넉히 잔다.
            time.sleep(0.05)
            return "x"

    tl.set_timeline(_make(tmp_path))
    chat = TracedChat(Slow(), model="m", backend="fake",
                      tracer=NullTracer(), slow_after_ms=1)
    chat.complete("s", "u")

    end = tl.load_timeline(tmp_path / "run.jsonl")[-1]
    assert end.detail.get("slow") is True
    assert "느림" in tl.format_line(end)


def test_fast_call_is_not_flagged(tmp_path):
    from contentcompare.llm.tracing import NullTracer, TracedChat

    tl.set_timeline(_make(tmp_path))
    chat = TracedChat(FakeChat(answer="x"), model="m", backend="fake",
                      tracer=NullTracer(), slow_after_ms=10_000)
    chat.complete("s", "u")

    end = tl.load_timeline(tmp_path / "run.jsonl")[-1]
    assert "slow" not in end.detail
    assert "느림" not in tl.format_line(end)


# --------------------------------------------------------------------------- #
# 래핑 조건 (factory)
# --------------------------------------------------------------------------- #
def test_timeline_alone_is_enough_to_wrap_the_chat_client():
    """추적이 꺼져 있어도 타임라인이 켜져 있으면 감싸야 LLM 호출이 잡힌다."""
    from contentcompare.config import AppConfig
    from contentcompare.llm.factory import build_clients
    from contentcompare.llm.tracing import TracedChat, reset_tracer

    reset_tracer()
    config = AppConfig.from_dict(
        {"llm": {"backend": "ollama"}, "logging": {"timeline": True}}
    )
    chat, _ = build_clients(config)
    reset_tracer()
    assert isinstance(chat, TracedChat)


def test_everything_off_returns_the_bare_client():
    """전부 끄면 오늘과 동일 객체 — 래핑도 하지 않는다."""
    from contentcompare.config import AppConfig
    from contentcompare.llm.factory import build_clients
    from contentcompare.llm.ollama import OllamaBackend
    from contentcompare.llm.tracing import reset_tracer

    reset_tracer()
    config = AppConfig.from_dict(
        {"llm": {"backend": "ollama"}, "logging": {"timeline": False}}
    )
    chat, _ = build_clients(config)
    reset_tracer()
    assert isinstance(chat, OllamaBackend)


# --------------------------------------------------------------------------- #
# SDK 내부 재시도 (httpx event hooks)
# --------------------------------------------------------------------------- #
def _probe(max_retries: int = 3):
    from contentcompare.llm.http_probe import HttpProbe

    return HttpProbe(max_retries=max_retries, clock=FakeClock(start=0.0, step=1.0))


def test_probe_records_a_completed_request(tmp_path):
    import httpx

    tl.set_timeline(_make(tmp_path))
    probe = _probe()
    request = httpx.Request("POST", "https://llm.intra.corp/v1/chat/completions")
    probe.on_request(request)
    probe.on_response(httpx.Response(200, request=request))

    event = tl.load_timeline(tmp_path / "run.jsonl")[-1]
    assert event.kind == "http"
    assert event.detail["status_code"] == 200
    assert event.status == "ok"


def test_probe_reveals_sdk_retry_when_no_response_arrives(tmp_path):
    """응답 훅은 타임아웃에 안 불린다 — **다음 요청이 왔다는 사실**이 재시도의 증거다."""
    import httpx

    tl.set_timeline(_make(tmp_path))
    probe = _probe(max_retries=3)
    request = httpx.Request("POST", "https://llm.intra.corp/v1/chat/completions")
    probe.on_request(request)   # 1차 — 응답 없음
    probe.on_request(request)   # 2차 = SDK 재시도

    retries = [e for e in tl.load_timeline(tmp_path / "run.jsonl") if e.kind == "retry"]
    assert len(retries) == 1
    assert retries[0].detail["attempt"] == 1
    assert retries[0].detail["max"] == 3
    assert retries[0].status == "timeout"


def test_probe_marks_error_status_codes(tmp_path):
    import httpx

    tl.set_timeline(_make(tmp_path))
    probe = _probe()
    request = httpx.Request("POST", "https://llm.intra.corp/v1/chat/completions")
    probe.on_request(request)
    probe.on_response(httpx.Response(429, request=request))

    event = tl.load_timeline(tmp_path / "run.jsonl")[-1]
    assert event.status == "rate_limit"


def test_langchain_backend_always_attaches_hooks():
    """verify_ssl 값과 무관하게 훅이 달려야 한다 — 관측이 SSL 설정에 딸려가면 안 된다."""
    from contentcompare.config import AppConfig
    from contentcompare.llm.langchain_backend import LangChainBackend

    for verify in (True, False):
        config = AppConfig.from_dict(
            {"llm": {"backend": "langchain", "internal": {"verify_ssl": verify}}}
        )
        client = LangChainBackend(config.llm)._http_client()
        assert client is not None
        assert client.event_hooks["request"]
        assert client.event_hooks["response"]
        client.close()


def test_sdk_retries_show_up_as_timeline_events(tmp_path):
    """실제 openai SDK 재시도가 타임라인에 남는가 — 이 태스크의 핵심 수용 기준."""
    import httpx

    pytest.importorskip("openai")
    from openai import APITimeoutError, OpenAI

    from contentcompare.llm.http_probe import HttpProbe

    tl.set_timeline(_make(tmp_path))

    def always_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Request timed out", request=request)

    probe = HttpProbe(max_retries=2)
    http_client = httpx.Client(
        transport=httpx.MockTransport(always_timeout),
        event_hooks={"request": [probe.on_request], "response": [probe.on_response]},
    )
    client = OpenAI(api_key="sk-none", base_url="https://x/v1",
                    http_client=http_client, max_retries=2)

    with pytest.raises(APITimeoutError):
        client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])

    events = tl.load_timeline(tmp_path / "run.jsonl")
    # 요청 3회(최초 + 재시도 2회) → 그 사이 응답이 없으므로 재시도 2건이 드러난다.
    assert len([e for e in events if e.kind == "retry"]) == 2
    http_client.close()


# --------------------------------------------------------------------------- #
# 대기 · 파싱 재시도
# --------------------------------------------------------------------------- #
def test_throttle_wait_is_visible(tmp_path):
    """사전 스로틀의 정지는 '행'이 아니라 '대기'로 보여야 한다."""
    from contentcompare.llm.ratelimit import RateLimiter

    tl.set_timeline(_make(tmp_path))
    ticks = iter([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    limiter = RateLimiter(1, clock=lambda: next(ticks, 0.0), sleep=lambda _s: None)
    limiter.acquire()
    limiter.acquire()   # 한도에 닿아 대기

    waits = [e for e in tl.load_timeline(tmp_path / "run.jsonl") if e.kind == "wait"]
    assert len(waits) == 1
    assert waits[0].detail["reason"] == "요청 한도 스로틀"


def test_rate_limit_retry_wait_is_visible(tmp_path):
    from contentcompare.llm.ratelimit import RateLimitedChat, RateLimiter

    class Boom:
        def __init__(self):
            self.calls = 0

        def complete(self, system, user, *, temperature=0.0):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("429 too many requests")
            return "ok"

    tl.set_timeline(_make(tmp_path))
    chat = RateLimitedChat(
        Boom(), limiter=RateLimiter(0), wait=1, max_retries=2, sleep=lambda _s: None,
    )
    assert chat.complete("s", "u") == "ok"

    waits = [e for e in tl.load_timeline(tmp_path / "run.jsonl") if e.kind == "wait"]
    assert len(waits) == 1
    assert waits[0].status == "rate_limit"
    assert waits[0].detail["attempt"] == 1


def test_json_parse_retry_is_visible(tmp_path):
    """전송 재시도와 파싱 재시도는 원인이 달라 타임라인에서도 갈려야 한다."""
    from contentcompare.fact.llm_stage import LlmRunner

    class Sloppy:
        def __init__(self):
            self.calls = 0

        def complete(self, system, user, *, temperature=0.0):
            self.calls += 1
            return "설명이 섞인 응답" if self.calls == 1 else '{"ok": 1}'

    tl.set_timeline(_make(tmp_path))
    assert LlmRunner(Sloppy(), max_calls=5).complete_json("sys", "user") == {"ok": 1}

    retries = [e for e in tl.load_timeline(tmp_path / "run.jsonl") if e.kind == "retry"]
    assert len(retries) == 1
    assert retries[0].detail["reason"] == "JSON 파싱 실패"
    assert retries[0].detail["attempt"] == 1


def test_http_layer_retry_is_visible(tmp_path):
    """internal·ollama 경로(requests)도 같은 축에 놓인다."""
    from contentcompare.llm.http import RetryPolicy, post_json

    tl.set_timeline(_make(tmp_path))
    calls = {"n": 0}

    class Resp:
        def __init__(self, code):
            self.status_code = code
            self.text = "{}"

        def json(self):
            return {"ok": 1}

    def poster(url, **kwargs):
        calls["n"] += 1
        return Resp(500 if calls["n"] == 1 else 200)

    out = post_json("http://x/api", {}, retry=RetryPolicy(max_retries=2, backoff_base=1),
                    poster=poster, sleep=lambda _s: None)
    assert out == {"ok": 1}

    retries = [e for e in tl.load_timeline(tmp_path / "run.jsonl") if e.kind == "retry"]
    assert len(retries) == 1
    assert retries[0].detail["attempt"] == 1


# --------------------------------------------------------------------------- #
# 배치 번호 — "몇 번째 배치에서 죽었나"를 역산 없이
# --------------------------------------------------------------------------- #
def _schema_and_profile():
    from contentcompare.fact.schema_models import (
        ColumnSchema, ColumnSpec, HeaderStructure, RowGrain, TableProfile,
    )

    schema = ColumnSchema(columns=[
        ColumnSpec(column="A", field_name="항목", semantic_role="entity_name",
                   data_type="text"),
        ColumnSpec(column="B", field_name="값", semantic_role="target_value",
                   data_type="number"),
    ])
    profile = TableProfile(
        header_structure=HeaderStructure(header_start_row=1, header_rows=1,
                                         data_start_row=2),
        row_grain=RowGrain(description="항목 1건"),
    )
    return schema, profile


def test_record_batches_are_named_in_the_timeline(tmp_path):
    from contentcompare.fact.llm_stage import LlmRunner
    from contentcompare.fact.record_normalizer import normalize_records

    schema, profile = _schema_and_profile()
    compact = {"doc_type": "excel", "sheets": [{
        "sheet_name": "Sheet1",
        "rows": [{"r": r, "cells": {"A": f"항목{r}", "B": r}} for r in range(2, 8)],
    }]}

    class Chat:
        def complete(self, system, user, *, temperature=0.0):
            return '{"records": []}'

    tl.set_timeline(_make(tmp_path))
    normalize_records(compact, profile, schema, LlmRunner(Chat(), max_calls=10),
                      batch_rows=2)

    names = [e.name for e in tl.load_timeline(tmp_path / "run.jsonl")
             if e.kind == "stage_start"]
    assert names == ["배치 1/3", "배치 2/3", "배치 3/3"]


def test_failing_batch_points_at_itself(tmp_path):
    """지난 실패에서 run_stats 의 llm.calls 를 세어 알아내던 것을 여기서 끝낸다."""
    from contentcompare.fact.llm_stage import LlmRunner
    from contentcompare.fact.record_normalizer import normalize_records
    from contentcompare.llm.tracing import stage

    schema, profile = _schema_and_profile()
    compact = {"doc_type": "excel", "sheets": [{
        "sheet_name": "Sheet1",
        "rows": [{"r": r, "cells": {"A": f"항목{r}", "B": r}} for r in range(2, 8)],
    }]}

    class Chat:
        def __init__(self):
            self.calls = 0

        def complete(self, system, user, *, temperature=0.0):
            self.calls += 1
            if self.calls == 2:
                raise TimeoutError("Request timed out")
            return '{"records": []}'

    tl.set_timeline(_make(tmp_path))
    with pytest.raises(TimeoutError):
        with stage("F2 records · 자표준원문.xlsx"):
            normalize_records(compact, profile, schema,
                              LlmRunner(Chat(), max_calls=10), batch_rows=2)

    failed = [e for e in tl.load_timeline(tmp_path / "run.jsonl")
              if e.kind == "stage_end" and e.status == "error"]
    assert failed[0].name == "F2 records · 자표준원문.xlsx · 배치 2/3"
    assert "TimeoutError" in failed[0].detail["error"]


# --------------------------------------------------------------------------- #
# 산출물 폴더 규약
# --------------------------------------------------------------------------- #
def test_microscope_does_not_mistake_the_timeline_folder_for_a_document(tmp_path):
    """``_timeline`` 이 예약 목록에 없으면 현미경이 문서 폴더로 오인한다."""
    from contentcompare.fact.artifact_reader import RESERVED_DIRS, list_runs

    (tmp_path / "_timeline").mkdir()
    (tmp_path / "_timeline" / "comparison_result.json").write_text("{}", "utf-8")
    assert "_timeline" in RESERVED_DIRS
    assert [r.label for r in list_runs(tmp_path)] == []


# --------------------------------------------------------------------------- #
# 토큰 사용량 — 백엔드가 받아놓고 버리던 숫자가 줄에 닿는가
# --------------------------------------------------------------------------- #
class UsageChat(FakeChat):
    """``last_usage`` 규약을 지키는 백엔드 흉내(:mod:`contentcompare.llm.usage`)."""

    def __init__(self, answer: str = "{}", usage=None, raises=None) -> None:
        super().__init__(answer=answer, raises=raises)
        self.last_usage = usage
        self._usage = usage

    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> str:
        from contentcompare.llm.usage import UNKNOWN

        self.last_usage = UNKNOWN          # 실제 백엔드와 같은 순서로 리셋 후
        answer = super().complete(system, user, temperature=temperature)
        self.last_usage = self._usage      # 응답을 받고서야 채운다
        return answer


def test_llm_end_carries_token_counts(tmp_path):
    """서버가 준 입력·출력 토큰이 ``llm_end`` 에 그대로 실린다."""
    from contentcompare.llm.usage import Usage

    chat = _traced(tmp_path, UsageChat(usage=Usage(input_tokens=3204, output_tokens=512)))
    chat.complete("sys", "user")

    end = tl.load_timeline(tmp_path / "run.jsonl")[-1]
    assert end.detail["input_tokens"] == 3204
    assert end.detail["output_tokens"] == 512


def test_llm_end_carries_generation_rate(tmp_path):
    """토큰/초 — 다음 배치 크기를 정하는 근거가 이 숫자다."""
    from contentcompare.llm.tracing import NullTracer, TracedChat
    from contentcompare.llm.usage import Usage

    tl.set_timeline(_make(tmp_path))
    clock = iter([0.0, 4.0])  # monotonic 두 번 호출 → 4초 걸린 것으로 만든다
    chat = TracedChat(UsageChat(usage=Usage(input_tokens=100, output_tokens=40)),
                      model="m", backend="fake", tracer=NullTracer())
    real = time.monotonic
    time.monotonic = lambda: next(clock)
    try:
        chat.complete("sys", "user")
    finally:
        time.monotonic = real

    end = tl.load_timeline(tmp_path / "run.jsonl")[-1]
    assert end.duration_ms == 4000
    assert end.detail["tok_per_sec"] == 10.0


def test_backend_without_usage_leaves_no_token_keys(tmp_path):
    """규약을 안 지키는 백엔드(로컬 onnx 등)여도 조용히 넘어간다 — 0 을 남기지 않는다."""
    chat = _traced(tmp_path, FakeChat(answer="hello"))
    chat.complete("sys", "user")

    end = tl.load_timeline(tmp_path / "run.jsonl")[-1]
    assert "input_tokens" not in end.detail
    assert "tok_per_sec" not in end.detail
    assert end.detail["output_chars"] == 5      # 글자 수는 그대로 남는다


def test_failed_call_does_not_reuse_previous_tokens(tmp_path):
    """실패한 호출에 **직전 성공의 토큰 수**가 묻어나면 기록이 거짓말을 한다."""
    from contentcompare.llm.usage import Usage

    inner = UsageChat(usage=Usage(input_tokens=777, output_tokens=88))
    chat = _traced(tmp_path, inner)
    chat.complete("sys", "user")               # 성공 1회로 last_usage 를 채우고
    inner.raises = TimeoutError("Request timed out")
    with pytest.raises(TimeoutError):
        chat.complete("sys", "user")           # 다음 호출은 실패

    end = tl.load_timeline(tmp_path / "run.jsonl")[-1]
    assert end.status == "timeout"
    assert "input_tokens" not in end.detail


def test_token_line_is_rendered_for_humans(tmp_path):
    """콘솔·로그 한 줄에 토큰이 실제로 보이는가 — 이 작업의 목적이 이 줄이다."""
    from contentcompare.llm.usage import Usage

    chat = _traced(tmp_path, UsageChat(answer="x" * 1880,
                                       usage=Usage(input_tokens=3204, output_tokens=512)))
    chat.complete("sys", "user")

    end = tl.load_timeline(tmp_path / "run.jsonl")[-1]
    line = tl.format_line(end)
    assert "input_tokens=3204" in line
    assert "output_tokens=512" in line
    assert "output_chars=1880" in line
