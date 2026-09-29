"""작업 폴더의 파일 → SSE(Server-Sent Events).

worker 는 서버와 파일로만 말한다(설계 §4.1). 여기서 ``console.log``(화면 로그)·
``progress.jsonl``(진행률)·``job.json``(상태)을 짧은 간격으로 읽어 이벤트로 흘린다.

- **완성된 줄만 보낸다.** worker 가 쓰는 도중이거나 강제 종료로 끝이 잘린 줄은 개행이 올 때까지
  기다린다. 단, 개행 없는 거대한 줄에서 영영 멈추지 않도록 ``max_bytes`` 를 넘으면 흘린다.
- **재연결은 이어서.** 이벤트 ``id`` 가 ``"<로그 바이트 위치>.<진행 seq>"`` 라, 브라우저가
  ``Last-Event-ID`` 로 다시 붙으면 본 줄을 또 보내지 않는다.
- **멈춤은 상태가 바뀔 때만 알린다**(설계 §6 #7). 활동 = 진행 이벤트·로그 파일 갱신·시작 시각
  중 가장 최근. 자동 종료는 하지 않는다.
- **진행 스냅샷은 파일이 바뀔 때만 다시 계산한다**(설계 §6 #10, :func:`snapshot_for`). F5 는 기준
  fact 마다 한 줄을 써서 ``progress.jsonl`` 이 수천 줄이 되는데, 구독자마다 0.5초에 한 번씩 전체를
  다시 읽으면 한 프로세스(GIL) 안에서 사람 수만큼 곱해진다.
"""

from __future__ import annotations

import functools
import json
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import AsyncIterator, Callable, Iterator, Optional

import anyio
import anyio.to_thread

from .. import progress as prog
from .jobs import RUNNING, Job


def read_complete_lines(path: Path, offset: int, *,
                        max_bytes: int = 256 * 1024) -> tuple[list[str], int]:
    try:
        size = path.stat().st_size
    except OSError:
        return [], offset
    if size < offset:
        offset = 0  # 파일이 새로 쓰였다
    if size == offset:
        return [], offset
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read(min(size - offset, max_bytes))
    end = data.rfind(b"\n")
    if end < 0:
        if len(data) < max_bytes:
            return [], offset  # 아직 줄이 완성되지 않았다
        return [data.decode("utf-8", errors="replace")], offset + len(data)
    chunk = data[:end + 1]
    return chunk.decode("utf-8", errors="replace").splitlines(), offset + len(chunk)


_MAX_SNAPSHOTS = 64
_snapshots: "OrderedDict[tuple, prog.Snapshot]" = OrderedDict()
_snapshots_lock = threading.Lock()


def snapshot_for(path: Path) -> prog.Snapshot:
    """``progress.jsonl`` → 진행 상태. ``(경로, 크기, 수정 시각)`` 이 같으면 계산한 것을 돌려준다.

    돌려준 객체는 여러 요청이 **공유**한다 — 읽기만 할 것. 파일이 없으면 빈 상태(캐시하지 않음).
    최근 :data:`_MAX_SNAPSHOTS` 개만 들고 있는다.
    """
    try:
        path = Path(path).resolve()
        st = path.stat()
    except OSError:
        return prog.Snapshot()
    key = (str(path), st.st_size, st.st_mtime_ns)
    with _snapshots_lock:  # 안에서 계산한다 — 같은 변화를 구독자 수만큼 동시에 파싱하지 않게
        snap = _snapshots.get(key)
        if snap is not None:
            _snapshots.move_to_end(key)
            return snap
        snap = prog.summarize(prog.load_events(path))
        _snapshots[key] = snap
        while len(_snapshots) > _MAX_SNAPSHOTS:
            _snapshots.popitem(last=False)
        return snap


def format_sse(event: str, data: dict, event_id: str = "") -> str:
    head = f"id: {event_id}\n" if event_id else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False, allow_nan=False)}\n\n"


@dataclass
class Cursor:
    log: int = 0
    seq: int = 0
    state: str = ""
    stalled: bool = False

    def to_id(self) -> str:
        return f"{self.log}.{self.seq}"

    @classmethod
    def parse(cls, value: Optional[str]) -> "Cursor":
        try:
            log, seq = str(value).split(".")
            log_int = int(log)
            seq_int = int(seq)
            if log_int < 0 or seq_int < 0:
                return cls()
            return cls(log=log_int, seq=seq_int)
        except (ValueError, AttributeError):
            return cls()


def collect(job: Job, job_dir: Path, cursor: Cursor, *, now: float,
            stall_after_s: float) -> tuple[list[tuple[str, dict]], Cursor]:
    out: list[tuple[str, dict]] = []
    cur = replace(cursor)
    log_path = job_dir / "console.log"
    lines, cur.log = read_complete_lines(log_path, cursor.log)
    if lines:
        out.append(("log", {"lines": lines}))
    snap = snapshot_for(job_dir / "progress.jsonl")
    if snap.last_seq != cursor.seq or not cursor.state:
        cur.seq = snap.last_seq
        out.append(("progress", snap.to_dict()))
    if job.state != cursor.state:
        cur.state = job.state
        out.append(("status", {"state": job.state, "error": job.error,
                               "started_ts": job.started_ts, "finished_ts": job.finished_ts}))
    try:
        log_mtime = os.path.getmtime(log_path)
    except OSError:
        log_mtime = 0.0
    idle = now - max(snap.last_ts, log_mtime, job.started_ts)
    stalled = job.state == RUNNING and idle > stall_after_s
    if stalled != cursor.stalled:
        cur.stalled = stalled
        out.append(("stall", {"stalled": stalled, "idle_s": int(idle)}))
    return out, cur


def _round(load_job: Callable[[], Optional[Job]], job_dir: Path, cursor: Cursor, quiet: float, *,
           now: float, stall_after_s: float, poll_s: float,
           heartbeat_s: float) -> tuple[list[str], Cursor, float, bool]:
    """한 바퀴 — 보낼 SSE 조각, 갱신된 커서·조용한 시간, 종료 여부. ``stream``/``astream`` 공용."""
    job = load_job()
    if job is None:
        return [format_sse("end", {"reason": "not_found"})], cursor, quiet, True
    items, cursor = collect(job, job_dir, cursor, now=now, stall_after_s=stall_after_s)
    chunks = [format_sse(event, data, cursor.to_id()) for event, data in items]
    if job.is_final and not any(event == "log" for event, _ in items):
        # 끝난 뒤에도 남은 로그 줄이 없어질 때까지 한 바퀴 더 돈다.
        chunks.append(format_sse("end", {"state": job.state}, cursor.to_id()))
        return chunks, cursor, quiet, True
    if items:
        quiet = 0.0
    else:
        quiet += poll_s
        if quiet >= heartbeat_s:
            chunks.append(": keepalive\n\n")  # 프록시가 조용한 연결을 끊지 않게
            quiet = 0.0
    return chunks, cursor, quiet, False


def stream(load_job: Callable[[], Optional[Job]], job_dir: Path, cursor: Cursor, *,
           stall_after_s: float, poll_s: float = 0.5,
           sleep: Callable[[float], None] = time.sleep,
           clock: Callable[[], float] = time.time,
           heartbeat_s: float = 15.0) -> Iterator[str]:
    quiet = 0.0
    while True:
        chunks, cursor, quiet, stop = _round(
            load_job, job_dir, cursor, quiet, now=clock(), stall_after_s=stall_after_s,
            poll_s=poll_s, heartbeat_s=heartbeat_s)
        yield from chunks
        if stop:
            return
        sleep(poll_s)


async def astream(load_job: Callable[[], Optional[Job]], job_dir: Path, cursor: Cursor, *,
                  stall_after_s: float, poll_s: float = 0.5,
                  clock: Callable[[], float] = time.time,
                  heartbeat_s: float = 15.0) -> AsyncIterator[str]:
    """``stream`` 의 비동기판 — 대기는 ``anyio.sleep`` 이라 연결이 스레드를 붙들지 않는다.

    sync 제너레이터를 StreamingResponse 에 주면 Starlette 이 스레드풀에서 ``next()`` 를 돌리는데,
    그 안에서 잠들면 열린 연결마다 스레드 하나를 점유해 (기본 40개) 다른 sync 라우트가 굶는다.
    파일·JSON 읽기 한 바퀴만 스레드에서 돌리고 기다림은 이벤트 루프에서 한다.
    """
    quiet = 0.0
    while True:
        chunks, cursor, quiet, stop = await anyio.to_thread.run_sync(
            functools.partial(
                _round, load_job, job_dir, cursor, quiet, now=clock(),
                stall_after_s=stall_after_s, poll_s=poll_s, heartbeat_s=heartbeat_s))
        for chunk in chunks:
            yield chunk
        if stop:
            return
        await anyio.sleep(poll_s)
