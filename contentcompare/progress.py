"""진행률 보고 — "전체 N 단계 중 몇 단계가 끝났나"를 파이프라인 밖으로 알린다.

:mod:`contentcompare.timeline` 과 **역할이 다르다.** 타임라인은 사람이 읽는 진단 기록이라
배치 번호가 ``"배치 3/7"`` **문자열 안에만** 있고, F5·F7 반복에는 이벤트가 아예 없다.
진행률을 이름 파싱에 기대면 이름 형식만 바꿔도 조용히 깨지므로 따로 둔다.

**쓰는 쪽은 사실만 남기고 계산은 읽는 쪽이 한다.** 파이프라인은 :func:`plan`·
:func:`unit_start`·:func:`part`·:func:`step`·:func:`unit_done` 로 "무엇이 시작/끝났다"만
알리고, 비율과 "뒤로 가지 않는다"는 보장은 :func:`summarize`(순수 함수)가 이벤트 전체를
보고 정한다. 그래서 웹 서버가 재시작돼도 파일만으로 같은 숫자를 다시 만든다.

단계 N 은 :func:`plan` 으로 **시작 시 확정하고 실행 중 바꾸지 않는다**(설계 §7.1). 진행 중인
단위만 내부 비율(하위 단계 i/P, 배치 d/t)을 소수로 더한다.

기본 보고기는 :class:`NullProgress` 라 CLI·기존 테스트는 동작이 같다. 웹 worker 는 작업마다
별도 프로세스라 모듈 전역 보고기로 충분하다(:func:`set_reporter`).

⚠️ **진행률 기록 실패는 절대 실행을 막지 않는다** — 타임라인과 같은 원칙이다. 그리고 이벤트에
문서 원문을 넣지 않는다(라벨은 파일 basename 과 단계 이름뿐).
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Union

logger = logging.getLogger(__name__)

# 단위 종류 — 화면이 아이콘·묶음 표시에 쓴다.
DOC = "doc"
CONCEPT = "concept"
COMPARE = "compare"
RAG_PREPARE = "rag_prepare"
RAG_JUDGE = "rag_judge"

# 단위 상태
PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"
FINISHED = (DONE, FAILED, SKIPPED)


@dataclass(frozen=True)
class Unit:
    """진행률의 한 단계. ``key`` 는 실행 안에서 유일해야 한다(겹치면 첫 것만 센다)."""

    key: str
    label: str
    kind: str


# --------------------------------------------------------------------------- #
# 보고기
# --------------------------------------------------------------------------- #
class NullProgress:
    """아무 일도 하지 않는다 — 기본값. CLI·테스트가 영향을 받지 않게 한다."""

    def emit(self, event: dict) -> None:
        return None


class MemoryProgress:
    """이벤트를 메모리에 모은다(테스트·같은 프로세스 소비자용)."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self._seq = 0

    def emit(self, event: dict) -> None:
        self._seq += 1
        self.events.append({"seq": self._seq, "ts": time.time(), **event})


class JsonlProgress:
    """append-only JSONL. 죽는 순간에도 앞줄이 남는다(타임라인과 같은 이유).

    첫 기록 실패에서 경고 한 번을 남기고 스스로 꺼진다 — 매 이벤트마다 실패를 반복하면
    디스크 문제가 로그를 뒤덮는다.
    """

    def __init__(self, path: Union[str, Path]) -> None:
        self.path = Path(path)
        self._seq = 0
        self._lock = threading.Lock()
        self._broken = False

    def emit(self, event: dict) -> None:
        if self._broken:
            return
        with self._lock:
            self._seq += 1
            record = {"seq": self._seq, "ts": round(time.time(), 3), **event}
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError as exc:
                self._broken = True
                logger.warning("[progress] 기록 실패 — 이후 진행률을 남기지 않습니다: %s", exc)


_REPORTER: Any = NullProgress()
# 지금 진행 중인 단위. ``part``/``step`` 이 키를 따로 받지 않게 해서 배치 루프 깊숙한
# 곳(F2·F3·F7)이 자기가 어느 단위 안에 있는지 몰라도 되게 한다.
_CURRENT = {"key": ""}


def set_reporter(reporter: Any) -> None:
    global _REPORTER
    _REPORTER = reporter
    _CURRENT["key"] = ""


def reset_reporter() -> None:
    set_reporter(NullProgress())


def get_reporter() -> Any:
    return _REPORTER


def _emit(event: dict) -> None:
    try:
        _REPORTER.emit(event)
    except Exception as exc:  # noqa: BLE001 — 진행률이 실행을 막으면 안 된다
        logger.warning("[progress] 보고기 오류(무시): %s", exc)


# --------------------------------------------------------------------------- #
# 쓰는 쪽 API
# --------------------------------------------------------------------------- #
def plan(units: Iterable[Unit]) -> None:
    """단계 N 을 확정한다. 실행마다 한 번, 맨 처음에 부른다."""
    _emit({"ev": "plan", "units": [asdict(u) for u in units]})


def unit_start(key: str, parts: int = 1) -> None:
    """단위 시작. ``parts`` 는 그 안의 하위 단계 수(문서 처리: Excel 6, Word/PPT 4)."""
    _CURRENT["key"] = key
    _emit({"ev": "unit_start", "key": key, "parts": max(1, int(parts))})


def part(index: int, name: str) -> None:
    """진행 중 단위의 ``index``(1부터) 번째 하위 단계에 들어섰다."""
    _emit({"ev": "part", "key": _CURRENT["key"], "index": int(index), "name": name})


def step(done: int, total: int) -> None:
    """진행 중 (하위) 단계 안의 진척 — 배치 d/t, 항목 i/M."""
    _emit({"ev": "step", "key": _CURRENT["key"], "done": int(done), "total": int(total)})


def unit_done(key: str, *, ok: bool = True, error: str = "") -> None:
    """단위 완료. 실패도 완료로 센다(문서 실패를 격리하고 계속하는 동작과 같다)."""
    _emit({"ev": "unit_done", "key": key, "ok": bool(ok), "error": error})
    if _CURRENT["key"] == key:
        _CURRENT["key"] = ""


def finish_remaining(error: str = "") -> None:
    """아직 안 닫힌 단위를 모두 닫는다 — 실행 끝 ``finally`` 에서 부른다.

    진행 중이던 것은 실패(``"중단"``), 시작도 못 한 것은 건너뜀(``error``)이 된다. 이게
    없으면 실패한 대상 문서의 F5 단위처럼 **영영 시작되지 않는 단위** 때문에 진행률이
    100% 에 닿지 못해 "끝났는데 멈춘 것처럼" 보인다.
    """
    _emit({"ev": "finish_remaining", "error": error})
    _CURRENT["key"] = ""


@contextmanager
def unit(key: str, parts: int = 1) -> Iterator[None]:
    """``unit_start`` … ``unit_done`` 을 감싼다. 예외는 실패로 남기고 그대로 올린다."""
    unit_start(key, parts)
    try:
        yield
    except BaseException as exc:  # noqa: BLE001 — 기록만 하고 그대로 올려보낸다
        unit_done(key, ok=False, error=type(exc).__name__)
        raise
    unit_done(key)


# --------------------------------------------------------------------------- #
# 읽는 쪽
# --------------------------------------------------------------------------- #
def load_events(path: Union[str, Path]) -> list[dict]:
    """JSONL 을 읽는다. 없으면 빈 목록. **잘린 마지막 줄은 건너뛴다**(죽는 순간의 흔적)."""
    p = Path(path)
    if not p.is_file():
        return []
    out: list[dict] = []
    with p.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


@dataclass
class UnitState:
    key: str
    label: str
    kind: str
    state: str = PENDING
    error: str = ""


@dataclass
class Snapshot:
    """이벤트 목록 하나에서 계산한 진행 상태."""

    units: list[UnitState] = field(default_factory=list)
    fraction: float = 0.0
    current: str = ""
    current_label: str = ""
    part_name: str = ""
    part_index: int = 0
    parts: int = 0
    step_done: int = 0
    step_total: int = 0
    started_ts: float = 0.0
    last_ts: float = 0.0
    last_seq: int = 0

    @property
    def total(self) -> int:
        return len(self.units)

    @property
    def finished(self) -> int:
        return sum(1 for u in self.units if u.state in FINISHED)

    @property
    def percent(self) -> int:
        # 내림이다 — 마지막 단위가 끝나기 전에 100% 가 보이면 "다 됐는데 왜 안 끝나나"가 된다.
        return int(self.fraction * 100 + 1e-9)

    @property
    def units_done(self) -> float:
        """화면의 ``4.6 / 8 단계`` 에서 ``4.6``."""
        return round(self.fraction * self.total, 1)

    def to_dict(self) -> dict:
        return {
            "units": [asdict(u) for u in self.units],
            "total": self.total,
            "finished": self.finished,
            "fraction": self.fraction,
            "percent": self.percent,
            "units_done": self.units_done,
            "current": {
                "key": self.current, "label": self.current_label,
                "part_name": self.part_name, "part_index": self.part_index,
                "parts": self.parts, "step_done": self.step_done,
                "step_total": self.step_total,
            } if self.current else None,
            "started_ts": self.started_ts,
            "last_ts": self.last_ts,
            "last_seq": self.last_seq,
        }


def _fraction(units: list[UnitState], run: dict) -> float:
    if not units:
        return 0.0
    finished = sum(1 for u in units if u.state in FINISHED)
    inner = 0.0
    if run:
        idx = max(run["index"], 1)
        within = run["done"] / run["total"] if run["total"] > 0 else 0.0
        within = min(max(within, 0.0), 1.0)
        inner = min(((idx - 1) + within) / run["parts"], 1.0)
    return min((finished + inner) / len(units), 1.0)


def summarize(events: Iterable[dict]) -> Snapshot:
    """이벤트 → 진행 상태. **진행률은 이전 최댓값 아래로 내려가지 않는다.**

    배치가 출력 절단으로 쪼개져 다시 세거나 하위 단계가 넘어가며 배치 수가 초기화되어도
    화면의 막대가 뒤로 가지 않게 하는 곳이 여기 한 군데다.

    **모양이 틀린 이벤트는 건너뛴다.** 우리 쓰기 코드는 그런 값을 만들지 않지만, 웹 서버가
    이 함수를 매 요청 부르므로 손상된 줄 하나가 API 를 500 으로 만들면 안 된다.
    """
    snap = Snapshot()
    index: dict[str, UnitState] = {}
    run: dict = {}
    best = 0.0
    for ev in events:
        try:
            run, best = _apply(ev, snap, index, run, best)
        except Exception:  # noqa: BLE001 — 모양이 틀린 이벤트가 API 를 500 으로 만들면 안 된다
            continue
    snap.fraction = best
    if run and run.get("key") in index:
        snap.current = run["key"]
        snap.current_label = index[run["key"]].label
        snap.part_name = run["name"]
        snap.part_index = run["index"]
        snap.parts = run["parts"]
        snap.step_done = run["done"]
        snap.step_total = run["total"]
    return snap


def _apply(ev: dict, snap: Snapshot, index: dict[str, UnitState], run: dict,
           best: float) -> tuple[dict, float]:
    """이벤트 하나를 반영하고 ``(진행 중 단위, 최댓값)`` 을 돌려준다.

    예외가 나면 호출자가 그 이벤트만 버린다. 그래서 **상태를 바꾸기 전에 값을 모두 계산**한다
    (``unit_start`` 가 ``parts`` 변환에 실패했는데 상태만 RUNNING 이 되면 안 된다).

    plan 이벤트의 경우, 새 units 와 index 를 **로컬 변수에 먼저 만들고** 개별 항목마다
    try/except 로 malformed 항목을 건너뛴 다음, 모두 처리한 뒤에만 상태를 바꾼다.
    이렇게 하지 않으면 malformed 항목이 index 를 부분 청소해서 stale run 을 남긴다.
    """
    kind = ev.get("ev")
    ts = float(ev.get("ts") or 0.0)
    seq = int(ev.get("seq") or 0)
    if ts and math.isfinite(ts):
        snap.started_ts = snap.started_ts or ts
        snap.last_ts = ts
    snap.last_seq = max(snap.last_seq, seq)
    if kind == "plan":
        # 로컬 변수에 먼저 만든다 — 실패해도 상태는 안 바뀐다.
        new_units: list[UnitState] = []
        new_index: dict[str, UnitState] = {}
        for u in ev.get("units") or []:
            try:
                key = str(u.get("key") or "")
                if not key or key in new_index:
                    continue  # 겹치는 키는 첫 것만 센다
                state = UnitState(key, str(u.get("label") or key), str(u.get("kind") or ""))
                new_index[key] = state
                new_units.append(state)
            except Exception:  # noqa: BLE001 — 개별 항목의 malformed 은 그 항목만 버린다
                continue
        # 모두 성공했으면 상태를 바꾼다.
        snap.units = new_units
        index.clear()
        index.update(new_index)
        return {}, 0.0
    key = str(ev.get("key") or "")
    state = index.get(key)
    if kind == "unit_start" and state is not None and state.state not in FINISHED:
        new_run = {"key": key, "parts": max(1, int(ev.get("parts") or 1)),
                   "index": 0, "name": "", "done": 0, "total": 0}
        state.state = RUNNING
        run = new_run
    elif kind == "part" and key and run.get("key") == key:
        run = {**run, "index": int(ev.get("index") or 0),
               "name": str(ev.get("name") or ""), "done": 0, "total": 0}
    elif kind == "step" and key and run.get("key") == key:
        run = {**run, "done": int(ev.get("done") or 0), "total": int(ev.get("total") or 0)}
    elif kind == "unit_done" and state is not None and state.state not in FINISHED:
        state.state = DONE if ev.get("ok", True) else FAILED
        state.error = str(ev.get("error") or "")
        if run.get("key") == key:
            run = {}
    elif kind == "finish_remaining":
        for u in snap.units:
            if u.state == RUNNING:
                u.state, u.error = FAILED, u.error or "중단"
            elif u.state == PENDING:
                u.state, u.error = SKIPPED, str(ev.get("error") or "")
        run = {}
    return run, max(best, _fraction(snap.units, run))
