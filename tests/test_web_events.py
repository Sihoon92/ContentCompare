"""SSE — 완성된 줄만, 재연결은 이어서, 멈춤은 한 번만 알린다."""

from __future__ import annotations

import json

from contentcompare.web import events as E
from contentcompare.web.jobs import FAILED, RUNNING, SUCCEEDED, Job


def _job(state=RUNNING, started=100.0):
    return Job(id="20260929-140211-a3f9", engine="fact", state=state, started_ts=started)


def test_partial_line_waits_until_newline(tmp_path):
    log = tmp_path / "console.log"
    log.write_bytes("첫 줄\n둘째 줄 쓰는 중".encode("utf-8"))
    lines, offset = E.read_complete_lines(log, 0)
    assert lines == ["첫 줄"]
    with open(log, "ab") as f:
        f.write(" 끝\n".encode("utf-8"))
    lines, offset = E.read_complete_lines(log, offset)
    assert lines == ["둘째 줄 쓰는 중 끝"]
    assert E.read_complete_lines(log, offset) == ([], offset)


def test_oversized_line_without_newline_is_flushed(tmp_path):
    log = tmp_path / "console.log"
    log.write_bytes(b"x" * 100)
    lines, offset = E.read_complete_lines(log, 0, max_bytes=40)
    assert lines == ["x" * 40] and offset == 40


def test_truncated_file_restarts_from_zero(tmp_path):
    log = tmp_path / "console.log"
    log.write_bytes(b"a\n")  # write_text 는 Windows 에서 \r\n 으로 바꿔 바이트 위치가 어긋난다
    assert E.read_complete_lines(log, 999) == (["a"], 2)


def test_missing_file_is_quiet(tmp_path):
    assert E.read_complete_lines(tmp_path / "없음.log", 0) == ([], 0)


def test_format_sse_is_one_data_line():
    text = E.format_sse("log", {"lines": ["가\n나"]}, "3.1")
    assert text == 'id: 3.1\nevent: log\ndata: {"lines": ["가\\n나"]}\n\n'


def test_cursor_round_trip_and_garbage():
    assert E.Cursor.parse(E.Cursor(log=12, seq=5).to_id()) == E.Cursor(log=12, seq=5)
    assert E.Cursor.parse("쓰레기") == E.Cursor()
    assert E.Cursor.parse(None) == E.Cursor()


def test_first_collect_sends_progress_and_status(tmp_path):
    (tmp_path / "console.log").write_bytes("시작\n".encode("utf-8"))
    items, cursor = E.collect(_job(), tmp_path, E.Cursor(), now=101.0, stall_after_s=300)
    kinds = [k for k, _ in items]
    assert kinds == ["log", "progress", "status"]
    assert items[0][1] == {"lines": ["시작"]}
    assert cursor.state == RUNNING and cursor.log == len("시작\n".encode("utf-8"))
    again, _ = E.collect(_job(), tmp_path, cursor, now=102.0, stall_after_s=300)
    assert again == []


def test_stall_is_announced_once_and_cleared(tmp_path):
    (tmp_path / "console.log").write_bytes(b"")
    import os
    os.utime(tmp_path / "console.log", (100.0, 100.0))
    _, cursor = E.collect(_job(), tmp_path, E.Cursor(), now=101.0, stall_after_s=300)
    items, cursor = E.collect(_job(), tmp_path, cursor, now=500.0, stall_after_s=300)
    assert items == [("stall", {"stalled": True, "idle_s": 400})]
    items, cursor = E.collect(_job(), tmp_path, cursor, now=600.0, stall_after_s=300)
    assert items == []
    items, _ = E.collect(_job(state=SUCCEEDED), tmp_path, cursor, now=700.0, stall_after_s=300)
    assert ("stall", {"stalled": False, "idle_s": 600}) in items


def test_stream_ends_after_final_state(tmp_path):
    (tmp_path / "console.log").write_bytes("끝\n".encode("utf-8"))
    job = _job(state=FAILED)
    job.error = "ValueError: x"
    chunks = list(E.stream(lambda: job, tmp_path, E.Cursor(), stall_after_s=300,
                           sleep=lambda s: None, clock=lambda: 200.0))
    events = [c.split("\n")[1] for c in chunks if c.startswith("id:")]
    assert events == ["event: log", "event: progress", "event: status", "event: end"]
    status = json.loads(chunks[2].split("data: ", 1)[1])
    assert status["state"] == FAILED and status["error"] == "ValueError: x"


def test_stream_resumes_after_last_event_id(tmp_path):
    (tmp_path / "console.log").write_bytes("본 줄\n새 줄\n".encode("utf-8"))
    seen = len("본 줄\n".encode("utf-8"))
    chunks = list(E.stream(lambda: _job(state=SUCCEEDED), tmp_path,
                           E.Cursor.parse(f"{seen}.0"), stall_after_s=300,
                           sleep=lambda s: None, clock=lambda: 200.0))
    logs = [json.loads(c.split("data: ", 1)[1]) for c in chunks if "event: log" in c]
    assert logs == [{"lines": ["새 줄"]}]


def test_stream_for_a_deleted_job_ends(tmp_path):
    chunks = list(E.stream(lambda: None, tmp_path, E.Cursor(), stall_after_s=300,
                           sleep=lambda s: None))
    assert chunks == [E.format_sse("end", {"reason": "not_found"})]


def test_cursor_rejects_negative_log():
    """음수 오프셋은 seek() 실패를 막기 위해 거부한다."""
    assert E.Cursor.parse("-5.0") == E.Cursor()
    assert E.Cursor.parse("5.-1") == E.Cursor()


def test_progress_with_infinite_ts_produces_valid_sse(tmp_path):
    """progress.jsonl 의 ts: Infinity 도 유효한 JSON 을 만든다."""
    import json as json_mod
    
    (tmp_path / "console.log").write_bytes(b"")
    
    # progress.jsonl 에 Infinity 를 담는다
    progress_file = tmp_path / "progress.jsonl"
    progress_file.write_text(
        '{"seq": 1, "ts": Infinity, "ev": "plan", "units": [{"key": "a", "label": "A", "kind": "doc"}]}\n'
        '{"seq": 2, "ts": 100.0, "ev": "unit_start", "key": "a"}\n',
        encoding="utf-8"
    )
    
    items, _ = E.collect(_job(), tmp_path, E.Cursor(), now=101.0, stall_after_s=300)
    
    # progress 이벤트가 있어야 한다
    progress_items = [data for kind, data in items if kind == "progress"]
    assert len(progress_items) > 0
    
    # 그 데이터를 SSE 로 포맷하면 JSON 파싱이 성공해야 한다
    sse_text = E.format_sse("progress", progress_items[0])
    data_line = sse_text.split("data: ", 1)[1].split("\n\n")[0]
    
    # Infinity 나 NaN 문자열이 없어야 한다
    assert "Infinity" not in data_line
    assert "NaN" not in data_line
    
    # JSON 파싱이 성공해야 한다
    parsed = json_mod.loads(data_line)
    assert parsed is not None


def test_astream_matches_the_sync_stream_for_a_finished_job(tmp_path):
    import anyio

    (tmp_path / "console.log").write_bytes("끝\n".encode("utf-8"))
    job = _job(state=FAILED)
    job.error = "ValueError: x"

    async def collect_all():
        return [c async for c in E.astream(lambda: job, tmp_path, E.Cursor(),
                                           stall_after_s=300, clock=lambda: 200.0)]

    chunks = anyio.run(collect_all)
    events = [c.split("\n")[1] for c in chunks if c.startswith("id:")]
    assert events == ["event: log", "event: progress", "event: status", "event: end"]
    sync = list(E.stream(lambda: job, tmp_path, E.Cursor(), stall_after_s=300,
                         sleep=lambda s: None, clock=lambda: 200.0))
    assert chunks == sync


# --------------------------------------------------------------------------- #
# 진행 스냅샷 캐시 — 구독자·요청마다 progress.jsonl 전체를 다시 읽지 않는다(설계 §6 #10)
def _count_loads(monkeypatch):
    calls = []
    real = E.prog.load_events

    def counting(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(E.prog, "load_events", counting)
    return calls


def _append_event(path, seq):
    with open(path, "ab") as f:
        f.write(json.dumps({"ev": "note", "seq": seq, "ts": 100.0 + seq}).encode("utf-8") + b"\n")


def test_snapshot_is_parsed_once_until_the_file_changes(tmp_path, monkeypatch):
    calls = _count_loads(monkeypatch)
    path = tmp_path / "progress.jsonl"
    _append_event(path, 1)
    assert E.snapshot_for(path).last_seq == 1
    assert E.snapshot_for(path).last_seq == 1
    assert len(calls) == 1
    _append_event(path, 2)
    assert E.snapshot_for(path).last_seq == 2
    assert len(calls) == 2


def test_collect_uses_the_snapshot_cache(tmp_path, monkeypatch):
    calls = _count_loads(monkeypatch)
    _append_event(tmp_path / "progress.jsonl", 1)
    _, cur = E.collect(_job(), tmp_path, E.Cursor(), now=101.0, stall_after_s=60)
    E.collect(_job(), tmp_path, cur, now=101.5, stall_after_s=60)
    assert len(calls) == 1


def test_missing_progress_is_empty_and_not_cached(tmp_path, monkeypatch):
    path = tmp_path / "progress.jsonl"
    assert E.snapshot_for(path).last_seq == 0
    _append_event(path, 5)
    assert E.snapshot_for(path).last_seq == 5


def test_snapshot_cache_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "_MAX_SNAPSHOTS", 2)
    paths = []
    for i in range(3):
        p = tmp_path / f"p{i}.jsonl"
        _append_event(p, i + 1)
        E.snapshot_for(p)
        paths.append(p)
    calls = _count_loads(monkeypatch)
    E.snapshot_for(paths[2])                       # 최근 것은 남아 있다
    assert len(calls) == 0
    E.snapshot_for(paths[0])                       # 가장 오래된 것은 밀려났다
    assert len(calls) == 1
