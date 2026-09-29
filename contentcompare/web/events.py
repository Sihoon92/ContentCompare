"""작업 폴더의 파일 → SSE(Server-Sent Events).

worker 는 서버와 파일로만 말한다(설계 §4.1). 여기서 ``console.log``(화면 로그)·
``progress.jsonl``(진행률)·``job.json``(상태)을 짧은 간격으로 읽어 이벤트로 흘린다.

- **완성된 줄만 보낸다.** worker 가 쓰는 도중이거나 강제 종료로 끝이 잘린 줄은 개행이 올 때까지
  기다린다. 단, 개행 없는 거대한 줄에서 영영 멈추지 않도록 ``max_bytes`` 를 넘으면 흘린다.
- **재연결은 이어서.** 이벤트 ``id`` 가 ``"<로그 바이트 위치>.<진행 seq>"`` 라, 브라우저가
  ``Last-Event-ID`` 로 다시 붙으면 본 줄을 또 보내지 않는다.
- **멈춤은 상태가 바뀔 때만 알린다**(설계 §6 #7). 활동 = 진행 이벤트·로그 파일 갱신·시작 시각
  중 가장 최근. 자동 종료는 하지 않는다.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterator, Optional

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
    snap = prog.summarize(prog.load_events(job_dir / "progress.jsonl"))
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


def stream(load_job: Callable[[], Optional[Job]], job_dir: Path, cursor: Cursor, *,
           stall_after_s: float, poll_s: float = 0.5,
           sleep: Callable[[float], None] = time.sleep,
           clock: Callable[[], float] = time.time,
           heartbeat_s: float = 15.0) -> Iterator[str]:
    quiet = 0.0
    while True:
        job = load_job()
        if job is None:
            yield format_sse("end", {"reason": "not_found"})
            return
        items, cursor = collect(job, job_dir, cursor, now=clock(), stall_after_s=stall_after_s)
        for event, data in items:
            yield format_sse(event, data, cursor.to_id())
        if job.is_final and not any(event == "log" for event, _ in items):
            # 끝난 뒤에도 남은 로그 줄이 없어질 때까지 한 바퀴 더 돈다.
            yield format_sse("end", {"state": job.state}, cursor.to_id())
            return
        if items:
            quiet = 0.0
        else:
            quiet += poll_s
            if quiet >= heartbeat_s:
                yield ": keepalive\n\n"  # 프록시가 조용한 연결을 끊지 않게
                quiet = 0.0
        sleep(poll_s)
