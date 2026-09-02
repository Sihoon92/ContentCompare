"""실행 타임라인 코어(:mod:`contentcompare.timeline`) — 이벤트·표현·설정.

이 모듈의 목적은 "언제 무엇이 왜 멈췄나"를 **역산 없이** 읽게 하는 것이다. 그래서
테스트도 그 관점으로 쓴다 — 이벤트가 남는가보다 *실패한 자리를 가리키는가*를 본다.

파이프라인 배선은 ``test_timeline_wiring.py``, 조회·화면은 ``test_timeline_view.py``.
Office/네트워크/LLM 없이 돈다(기존 규약). 시각은 주입한 시계로 고정한다.
"""

from __future__ import annotations

import json

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


def _events(*rows):
    return [tl.TimelineEvent(ts=float(i), kind=k, name=n, status=s,
                             duration_ms=d, detail=dict(det))
            for i, (k, n, s, d, det) in enumerate(rows)]


@pytest.fixture(autouse=True)


def _clean_singleton():
    """싱글턴이 테스트 사이에 새지 않게 한다."""
    tl.reset_timeline()
    yield
    tl.reset_timeline()


# --------------------------------------------------------------------------- #
# 이벤트 기록
# --------------------------------------------------------------------------- #
def test_events_are_appended_as_jsonl(tmp_path):
    line = _make(tmp_path)
    line.event("stage_start", "F2 records")
    line.event("stage_end", "F2 records", status="ok", duration_ms=1200)

    rows = [json.loads(x) for x in (tmp_path / "run.jsonl").read_text("utf-8").splitlines()]
    assert [r["kind"] for r in rows] == ["stage_start", "stage_end"]
    assert rows[1]["duration_ms"] == 1200


def test_timestamps_increase_monotonically(tmp_path):
    line = _make(tmp_path)
    for i in range(5):
        line.event("retry", "F2 records", attempt=i)

    events = tl.load_timeline(tmp_path / "run.jsonl")
    stamps = [e.ts for e in events]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == 5


def test_truncated_last_line_does_not_lose_earlier_events(tmp_path):
    """강제 종료로 마지막 줄이 잘려도 앞줄은 읽혀야 한다(append-only 의 이유)."""
    line = _make(tmp_path)
    line.event("stage_start", "F1 profile")
    line.event("stage_start", "F2 records")

    path = tmp_path / "run.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"ts": 1.0, "kind": "llm_st')  # 쓰다 만 줄

    events = tl.load_timeline(path)
    assert [e.name for e in events] == ["F1 profile", "F2 records"]


def test_detail_never_carries_prompt_text(tmp_path):
    """기본 ON 이므로 원문이 들어가면 평문 유출이 기본값이 된다(Global Constraints)."""
    line = _make(tmp_path)
    line.event("llm_start", "F2 records", prompt_chars=4102, rows=30)

    raw = (tmp_path / "run.jsonl").read_text("utf-8")
    assert "4102" in raw
    assert "prompt_chars" in raw
    # 길이만 담는 규약이므로 본문 키 자체가 없어야 한다.
    for banned in ("system", "user", "output", "prompt_text"):
        assert f'"{banned}"' not in raw


# --------------------------------------------------------------------------- #
# 콘솔 표현 (format_line)
# --------------------------------------------------------------------------- #
def test_format_line_marks_stage_boundaries():
    start = tl.TimelineEvent(ts=1_700_000_000.0, kind="stage_start", name="F2 records")
    ok = tl.TimelineEvent(
        ts=1_700_000_001.0, kind="stage_end", name="F2 records",
        status="ok", duration_ms=1200,
    )
    assert "▶" in tl.format_line(start)
    assert "F2 records" in tl.format_line(start)
    assert "✓" in tl.format_line(ok)
    assert "1.2s" in tl.format_line(ok)


def test_format_line_names_the_failing_stage_and_reason():
    """실패 줄 하나만 봐도 '어느 단계·왜'가 나와야 한다 — 이 계획의 수용 기준."""
    ev = tl.TimelineEvent(
        ts=1_700_000_000.0, kind="stage_end", name="F2 records · 자표준원문.xlsx · 배치 1/4",
        status="error", duration_ms=366_400, detail={"error": "APITimeoutError"},
    )
    text = tl.format_line(ev)
    assert "✗" in text
    assert "배치 1/4" in text
    assert "APITimeoutError" in text


def test_format_line_shows_retry_progress():
    ev = tl.TimelineEvent(
        ts=1_700_000_000.0, kind="retry", name="F2 records", status="timeout",
        duration_ms=120_100, detail={"attempt": 1, "max": 3, "error": "APITimeoutError"},
    )
    text = tl.format_line(ev)
    assert "재시도 1/3" in text
    assert "APITimeoutError" in text


def test_format_line_includes_clock_time():
    ev = tl.TimelineEvent(ts=1_700_000_000.0, kind="stage_start", name="F1")
    # 로컬 타임존과 무관하게 HH:MM:SS.d 모양이면 된다.
    head = tl.format_line(ev).split()[0]
    assert len(head) == len("18:42:01.3")
    assert head[2] == ":" and head[5] == ":" and head[8] == "."


# --------------------------------------------------------------------------- #
# 반복 구간(배치) 줄 모양
# --------------------------------------------------------------------------- #
def test_substage_line_is_indented_and_short():
    """반복 구간이 부모 이름을 매번 다시 찍으면 콘솔이 읽히지 않는다."""
    start = tl.TimelineEvent(
        ts=1_700_000_000.0, kind="stage_start",
        name="F2 records · 자표준원문.xlsx · 배치 1/4", detail={"depth": 1, "rows": 30},
    )
    text = tl.format_line(start)
    assert "배치 1/4" in text
    assert "자표준원문" not in text          # 부모는 이미 위 줄에 있다
    assert text.split("▶")[0].endswith("  ")  # 들여쓰기로 소속을 보인다


def test_failed_substage_keeps_the_full_name():
    """실패 줄만은 예외 — 문서·단계·배치가 한 줄에 다 있어야 한다."""
    end = tl.TimelineEvent(
        ts=1_700_000_000.0, kind="stage_end",
        name="F2 records · 자표준원문.xlsx · 배치 2/4", status="error",
        duration_ms=366_400, detail={"depth": 1, "error": "APITimeoutError"},
    )
    text = tl.format_line(end)
    assert "자표준원문.xlsx" in text and "배치 2/4" in text


# --------------------------------------------------------------------------- #
# 싱글턴 · 안전망
# --------------------------------------------------------------------------- #
def test_emit_without_setup_is_a_noop():
    """설정 전에 불려도 죽지 않는다(연결 점검 등 파이프라인 밖 경로)."""
    tl.emit("stage_start", "아무것도 설정되지 않음")  # 예외가 나면 실패


def test_emit_uses_the_active_timeline(tmp_path):
    line = _make(tmp_path)
    tl.set_timeline(line)
    tl.emit("stage_start", "F1 profile")

    assert [e.name for e in tl.load_timeline(tmp_path / "run.jsonl")] == ["F1 profile"]


def test_recording_failure_degrades_instead_of_raising(tmp_path, caplog):
    """기록 실패가 실행을 막지 않는다 — TracedChat._safe_record 와 같은 강등 패턴."""
    line = _make(tmp_path)

    def boom(*_a, **_k):
        raise OSError("디스크 가득 참")

    line._write = boom  # type: ignore[method-assign]
    tl.set_timeline(line)

    tl.emit("stage_start", "F2 records")  # 예외가 새어 나오면 실패
    tl.emit("stage_start", "F3 facts")    # 강등 후에도 조용해야 한다

    assert not line.active


def test_disabled_timeline_writes_nothing(tmp_path):
    tl.set_timeline(tl.NullTimeline())
    tl.emit("stage_start", "F2 records")
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- #
# 콘솔 인코딩 — Windows PowerShell 5.1 은 cp949 다
# --------------------------------------------------------------------------- #
def test_symbols_survive_a_cp949_console():
    """``✓``·``✗`` 는 cp949 에 없다. 그대로 print 하면 줄이 통째로 사라진다.

    실측: 이것을 놓쳤을 때 콘솔에 stage_start 만 찍히고 **종료 줄이 전부** 유실됐다
    (``emit`` 이 예외를 삼켜 조용히). 사용자 환경이 PowerShell 5.1 이라 기본값이다.
    """
    for kind in ("stage_start", "stage_end", "retry", "wait", "http", "llm_end"):
        event = tl.TimelineEvent(
            ts=1_700_000_000.0, kind=kind, name="F2 records · 자표준원문.xlsx",
            status="error", duration_ms=1200,
            detail={"error": "APITimeoutError", "attempt": 1, "max": 3, "seconds": 60},
        )
        text = tl.console_safe(tl.format_line(event), "cp949")
        text.encode("cp949")  # 예외가 나면 실패


def test_console_safe_keeps_unicode_when_the_console_can_take_it():
    line = tl.format_line(
        tl.TimelineEvent(ts=1_700_000_000.0, kind="stage_end", name="F1",
                         status="ok", duration_ms=1000)
    )
    assert tl.console_safe(line, "utf-8") == line
    assert "✓" in tl.console_safe(line, "utf-8")


def test_console_failure_does_not_stop_file_recording(tmp_path, caplog):
    """화면에 못 찍는 것과 기록이 없는 것은 다르다 — 파일은 계속 남아야 한다."""
    line = tl.Timeline(tmp_path / "run.jsonl", clock=FakeClock(), console=True)
    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        raise UnicodeEncodeError("cp949", "✓", 0, 1, "illegal multibyte sequence")

    line._print = boom  # type: ignore[method-assign]
    tl.set_timeline(line)

    tl.emit("stage_start", "F2 records")
    tl.emit("stage_end", "F2 records", status="ok")

    assert line.active                      # 기록은 살아 있고
    assert not line.console                 # 화면만 꺼진다
    assert calls["n"] == 1                  # 매 줄마다 다시 시도하지 않는다
    assert len(tl.load_timeline(tmp_path / "run.jsonl")) == 2


def test_console_safe_only_swaps_what_the_encoding_cannot_take():
    """cp949 는 ``▶``·``│`` 를 받는다 — 쓸 수 있는 기호까지 버리면 화면만 초라해진다."""
    text = "▶ 배치 1/4 │ ✓ 완료 — 끝"
    out = tl.console_safe(text, "cp949")
    out.encode("cp949")
    assert "▶" in out and "│" in out
    assert "✓" not in out and "—" not in out


# --------------------------------------------------------------------------- #
# 요약 · 진단 힌트
# --------------------------------------------------------------------------- #
def test_stage_durations_rank_the_slow_ones():
    events = _events(
        ("stage_end", "F1 document_profile", "ok", 4_200, {}),
        ("stage_end", "F2 records", "error", 366_400, {"error": "APITimeoutError"}),
        ("stage_end", "F1 column_schema", "ok", 8_100, {}),
    )
    rows = tl.stage_durations(events)
    assert [r["name"] for r in rows] == ["F2 records", "F1 column_schema",
                                         "F1 document_profile"]
    assert rows[0]["status"] == "error"


def test_stage_durations_skip_batches():
    """배치까지 세면 요약이 수십 줄이 되어 요약이 아니게 된다."""
    events = _events(
        ("stage_end", "F2 records", "ok", 30_000, {}),
        ("stage_end", "F2 records · 배치 1/4", "ok", 8_000, {"depth": 1}),
        ("stage_end", "F2 records · 배치 2/4", "ok", 7_000, {"depth": 1}),
    )
    assert [r["name"] for r in tl.stage_durations(events)] == ["F2 records"]


def test_diagnose_turns_a_timeout_into_a_next_step():
    """이번 진단에서 사람이 코드를 읽어 도달한 결론을 실패한 자리에서 알려준다."""
    events = _events(
        ("stage_end", "F2 records · 자표준원문.xlsx", "timeout", 366_400,
         {"error": "APITimeoutError: Request timed out"}),
    )
    hints = tl.diagnose(events)
    assert any("record_batch_rows" in h for h in hints)
    assert any("timeout" in h for h in hints)


def test_diagnose_points_at_the_throttle_for_rate_limits():
    events = _events(("llm_end", "F7", "rate_limit", 100, {"error": "429"}))
    assert any("max_calls_per_minute" in h for h in tl.diagnose(events))


def test_diagnose_reads_an_empty_ollama_response():
    """Ollama 는 컨텍스트가 모자라면 오류가 아니라 빈 응답을 준다 — 아는 사람만 안다."""
    events = _events(("llm_end", "F3", "ok", 500, {"output_chars": 0}))
    assert any("num_ctx" in h for h in tl.diagnose(events))


def test_diagnose_is_quiet_when_nothing_is_wrong():
    events = _events(("stage_end", "F1", "ok", 100, {}))
    assert tl.diagnose(events) == []


# --------------------------------------------------------------------------- #
# 설정 연결
# --------------------------------------------------------------------------- #
def test_build_timeline_respects_config(tmp_path):
    from contentcompare.config import AppConfig

    off = AppConfig.from_dict({"logging": {"timeline": False}})
    assert not tl.build_timeline(off).active

    on = AppConfig.from_dict(
        {"logging": {"timeline": True, "timeline_dir": str(tmp_path)}}
    )
    built = tl.build_timeline(on)
    assert built.active
    built.event("stage_start", "F1")
    built.close()
    assert list(tmp_path.glob("*.jsonl"))


def test_timeline_dir_defaults_to_artifacts(tmp_path):
    from contentcompare.config import AppConfig

    config = AppConfig.from_dict({"fact": {"artifacts_dir": str(tmp_path / "arts")}})
    built = tl.build_timeline(config)
    built.event("stage_start", "F1")
    built.close()
    assert list((tmp_path / "arts" / "_timeline").glob("*.jsonl"))
