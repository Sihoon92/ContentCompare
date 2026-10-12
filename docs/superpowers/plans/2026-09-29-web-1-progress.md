# 웹 전환 1/3 — 진행률 보고 모듈과 파이프라인 훅 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** fact·rag 두 엔진이 "전체 N 단계 중 몇 단계"를 파일로 알릴 수 있게 하는 `contentcompare/progress.py` 와, 그것을 부르는 파이프라인 훅을 만든다.

**Architecture:** 쓰는 쪽(파이프라인)은 `plan`/`unit_start`/`part`/`step`/`unit_done` 으로 **사실만** 남기고, 비율·단조 보장은 읽는 쪽 순수 함수 `summarize(events)` 가 이벤트 전체를 보고 계산한다. 기본 보고기는 아무 일도 안 하는 `NullProgress` 라 CLI·기존 테스트는 동작이 같다. 웹 worker(계획 2)가 `JsonlProgress` 를 설치한다.

**Tech Stack:** Python 3.10+ 표준 라이브러리만(json·threading·dataclasses·contextlib), pytest.

**Spec:** `docs/superpowers/specs/2026-09-29-web-frontend-design.md` — 이 계획은 §7(진행률)을 구현한다.

**후속 계획:** 2/3 백엔드 웹 서버(작업·worker·API·관리자), 3/3 React 화면 + `start.bat`. 둘 다 이 계획의 `progress.summarize()`·`Snapshot.to_dict()`·`JsonlProgress`·`load_events()` 를 쓴다.

## 선행 조건

- 작업 트리의 F7 배치 분할 변경(`contentcompare/fact/concept_builder.py`, `llm_stage.py`, `fact_comparator.py`, 관련 테스트, `CLAUDE.md`)이 **먼저 커밋되어 있어야 한다.** Task 3 은 `judge_pairs` 가 `batches = [...]` 목록을 만들고 `for index, batch in enumerate(batches, start=1)` 로 도는 **현재 작업 트리 버전**을 기준으로 한다.
- 그 커밋 위에서 새 브랜치 `feat/web-progress` 를 만든다.
- 기준선: `.venv` 에는 `httpx` 가 없어 `tests/test_timeline_wiring.py` 5건이 원래 실패한다. 이 계획의 "전체 테스트 통과"는 **그 5건을 제외한** 결과를 뜻한다(운영 anaconda 환경에서는 전부 통과해야 한다).

## Global Constraints

- Python ≥ 3.10. `progress.py` 는 **표준 라이브러리만** 쓴다(코어 의존성은 `pyyaml`·`requests` 뿐이다).
- 코드 주석·독스트링·문서는 한국어, 식별자는 영어.
- `contentcompare/comparison/`·`contentcompare/readers/` 는 **수정하지 않는다**(코드 무수정 원칙).
- 기본 보고기(`NullProgress`)에서는 파이프라인 동작이 **바이트 단위로 같아야** 한다 — 기존 테스트를 하나도 고치지 않고 통과해야 한다.
- **진행률 기록 실패는 절대 실행을 막지 않는다**(타임라인과 같은 원칙). 모든 보고 경로는 예외를 삼킨다.
- 진행률 이벤트에는 **문서 원문을 넣지 않는다** — 라벨은 파일 basename 과 단계 이름뿐이다.
- 두 파이프라인의 `run()` 에 이미 `progress` 라는 **매개변수**가 있으므로 모듈은 반드시 `from .. import progress as prog`(또는 `from . import progress as prog`)로 가져온다.
- 테스트는 Office·LLM·네트워크 없이 모든 OS 에서 돈다(가짜 주입).
- 커밋은 그 태스크의 파일만 `git add <경로>` 로 올린다(일괄 스테이징 금지). 메시지 끝에 다음 두 줄:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_015WPCPHZGMqNLc7QQAv1ako
  ```

## Review Focus

1. **대상 파일 basename 이 겹칠 때**(폴더 업로드의 `a/규격.docx`·`b/규격.docx`) — `compare:<이름>` 키가 겹친다. `summarize` 는 죽지 않고, 진행률이 100% 를 넘지 않아야 한다. → Task 1 `test_duplicate_keys_count_once_and_never_exceed_one`
2. **worker 가 쓰는 도중 죽어 마지막 줄이 잘린 `progress.jsonl`** — 앞줄은 그대로 읽혀야 한다. → Task 1 `test_load_events_skips_truncated_tail`
3. **캐시 적중 재실행**(F2 배치 이벤트가 0건) — 진행률이 뒤로 가지 않고 100% 로 끝나야 한다. → Task 2 `test_cache_hit_rerun_is_monotonic_and_completes`
4. **F7 이 예외로 죽을 때** — 실행 중 단위는 실패, 남은 F5 단위는 건너뜀으로 닫혀 "끝났는데 멈춘 것처럼" 보이지 않아야 한다. → Task 3 `test_concept_failure_closes_every_unit`
5. **디스크 기록 실패**(경로가 폴더이거나 권한 없음) — 파이프라인은 계속 돌아야 한다. → Task 1 `test_jsonl_write_failure_never_raises`

---

## 파일 구조

| 파일 | 책임 | 태스크 |
|---|---|---|
| `contentcompare/progress.py` (새로) | 단위 정의, 보고기 3종, 모듈 API, 이벤트 읽기, `summarize` | 1 |
| `tests/test_progress.py` (새로) | 위 모듈 단위 테스트 | 1 |
| `contentcompare/fact/pipeline.py` | 단위 계획, 문서 단위 시작/완료, 문서 하위 단계, F7·F5 단위, `finish_remaining` | 2, 3 |
| `contentcompare/fact/record_normalizer.py` | F2 배치 진척 | 2 |
| `contentcompare/fact/fact_extractor.py` | F3 배치 진척(Word/PPT) | 2 |
| `contentcompare/fact/concept_builder.py` | F7 배치 진척 | 3 |
| `contentcompare/pipeline.py` | RAG 단위 계획·진척 | 4 |
| `tests/test_fact_pipeline_progress.py` (새로) | fact 훅 테스트 | 2, 3 |
| `tests/test_concept_builder_llm.py` | F7 배치 진척 테스트 1건 추가 | 3 |
| `tests/test_pipeline_progress.py` (새로) | RAG 훅 테스트 | 4 |
| `CLAUDE.md` | 진행률 모듈 절 추가 | 1 |

---

### Task 1: `progress.py` — 보고기·모듈 API·`summarize`

**Files:**
- Create: `contentcompare/progress.py`
- Create: `tests/test_progress.py`
- Modify: `CLAUDE.md` (「### 실행 타임라인」 절 바로 **뒤**에 새 절 추가)

**Interfaces:**
- Consumes: 없음(표준 라이브러리만).
- Produces (계획 2·3 과 Task 2~4 가 쓴다):
  - 상수: `DOC`, `CONCEPT`, `COMPARE`, `RAG_PREPARE`, `RAG_JUDGE`(단위 종류) / `PENDING`, `RUNNING`, `DONE`, `FAILED`, `SKIPPED`, `FINISHED`(상태)
  - `@dataclass(frozen=True) Unit(key: str, label: str, kind: str)`
  - 보고기: `NullProgress`, `MemoryProgress`(`.events: list[dict]`), `JsonlProgress(path)` — 모두 `emit(event: dict) -> None`
  - `set_reporter(reporter) -> None`, `reset_reporter() -> None`, `get_reporter()`
  - `plan(units: Iterable[Unit]) -> None`
  - `unit_start(key: str, parts: int = 1) -> None`
  - `part(index: int, name: str) -> None` — 1부터 센다
  - `step(done: int, total: int) -> None`
  - `unit_done(key: str, *, ok: bool = True, error: str = "") -> None`
  - `finish_remaining(error: str = "") -> None`
  - `unit(key: str, parts: int = 1)` — 컨텍스트 매니저
  - `load_events(path) -> list[dict]`
  - `summarize(events: Iterable[dict]) -> Snapshot`
  - `Snapshot` 필드: `units: list[UnitState]`, `fraction: float`, `current: str`, `current_label: str`, `part_name: str`, `part_index: int`, `parts: int`, `step_done: int`, `step_total: int`, `started_ts: float`, `last_ts: float`, `last_seq: int`; 프로퍼티 `total`, `finished`, `percent`, `units_done`; 메서드 `to_dict() -> dict`
  - `UnitState(key, label, kind, state=PENDING, error="")`
  - 이벤트 한 줄 모양: `{"seq": int, "ts": float, "ev": "plan"|"unit_start"|"part"|"step"|"unit_done"|"finish_remaining", ...}`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_progress.py`

```python
"""진행률 모듈 — 쓰는 쪽은 사실만, 계산은 summarize 가 한다."""

from __future__ import annotations

import json

import pytest

from contentcompare import progress as prog


@pytest.fixture(autouse=True)
def _reset():
    prog.reset_reporter()
    yield
    prog.reset_reporter()


def _memory() -> prog.MemoryProgress:
    mem = prog.MemoryProgress()
    prog.set_reporter(mem)
    return mem


def _two_units():
    prog.plan([prog.Unit("a", "문서A", prog.DOC), prog.Unit("b", "문서B", prog.DOC)])


def test_default_reporter_is_null_and_calls_never_raise():
    assert isinstance(prog.get_reporter(), prog.NullProgress)
    _two_units()
    prog.unit_start("a", parts=2)
    prog.part(1, "F0 raw")
    prog.step(1, 3)
    prog.unit_done("a")
    prog.finish_remaining("건너뜀")


def test_fraction_combines_finished_units_and_inner_progress():
    mem = _memory()
    _two_units()
    prog.unit_start("a", parts=2)
    prog.part(1, "F0 raw")
    prog.step(1, 2)
    snap = prog.summarize(mem.events)
    # 단위 a 의 내부 = (0 + 1/2) / 2 = 0.25 → 전체 = 0.25 / 2 = 0.125
    assert snap.fraction == pytest.approx(0.125)
    assert snap.percent == 12
    assert snap.current == "a" and snap.current_label == "문서A"
    assert (snap.part_index, snap.parts, snap.part_name) == (1, 2, "F0 raw")
    assert (snap.step_done, snap.step_total) == (1, 2)


def test_fraction_never_goes_backwards():
    """배치가 쪼개져 다시 세도(3/4 → 1/4) 진행률은 이전 최댓값을 유지한다."""
    mem = _memory()
    _two_units()
    prog.unit_start("a")
    prog.step(3, 4)
    prog.step(1, 4)
    assert prog.summarize(mem.events).fraction == pytest.approx(3 / 4 / 2)


def test_all_units_done_is_exactly_one():
    mem = _memory()
    _two_units()
    for key in ("a", "b"):
        prog.unit_start(key)
        prog.unit_done(key)
    snap = prog.summarize(mem.events)
    assert snap.fraction == 1.0 and snap.percent == 100
    assert snap.finished == snap.total == 2
    assert snap.current == ""


def test_percent_floors_so_100_means_finished():
    mem = _memory()
    prog.plan([prog.Unit(str(i), str(i), prog.DOC) for i in range(1000)])
    for i in range(999):
        prog.unit_start(str(i))
        prog.unit_done(str(i))
    assert prog.summarize(mem.events).percent == 99


def test_finish_remaining_closes_running_and_pending_units():
    mem = _memory()
    prog.plan([prog.Unit(k, k, prog.DOC) for k in ("a", "b", "c")])
    prog.unit_start("a")
    prog.unit_done("a")
    prog.unit_start("b")
    prog.finish_remaining("건너뜀")
    snap = prog.summarize(mem.events)
    states = {u.key: (u.state, u.error) for u in snap.units}
    assert states == {
        "a": (prog.DONE, ""),
        "b": (prog.FAILED, "중단"),
        "c": (prog.SKIPPED, "건너뜀"),
    }
    assert snap.finished == snap.total and snap.fraction == 1.0


def test_duplicate_keys_count_once_and_never_exceed_one():
    """대상 basename 이 겹치면 compare 키가 겹친다 — 죽지 않고 100% 를 넘지 않는다."""
    mem = _memory()
    prog.plan([prog.Unit("compare:규격.docx", "F5", prog.COMPARE),
               prog.Unit("compare:규격.docx", "F5", prog.COMPARE)])
    for _ in range(2):
        prog.unit_start("compare:규격.docx")
        prog.step(5, 5)
        prog.unit_done("compare:규격.docx")
    snap = prog.summarize(mem.events)
    assert snap.total == 1
    assert snap.fraction == 1.0


def test_events_for_unknown_or_empty_keys_are_ignored():
    mem = _memory()
    _two_units()
    prog.step(1, 2)                 # 어떤 단위도 시작 안 함 → 키가 빈 문자열
    prog.unit_start("없는키")
    prog.unit_done("없는키")
    snap = prog.summarize(mem.events)
    assert snap.fraction == 0.0
    assert [u.state for u in snap.units] == [prog.PENDING, prog.PENDING]


def test_summarize_without_plan_is_empty():
    assert prog.summarize([]).fraction == 0.0
    assert prog.summarize([]).total == 0


def test_unit_context_marks_failure_and_reraises():
    mem = _memory()
    _two_units()
    with pytest.raises(RuntimeError):
        with prog.unit("a"):
            raise RuntimeError("boom")
    snap = prog.summarize(mem.events)
    assert (snap.units[0].state, snap.units[0].error) == (prog.FAILED, "RuntimeError")


def test_jsonl_round_trip_keeps_order_and_seq(tmp_path):
    path = tmp_path / "progress.jsonl"
    prog.set_reporter(prog.JsonlProgress(path))
    _two_units()
    prog.unit_start("a")
    prog.unit_done("a")
    events = prog.load_events(path)
    assert [e["ev"] for e in events] == ["plan", "unit_start", "unit_done"]
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert prog.summarize(events).finished == 1


def test_load_events_skips_truncated_tail(tmp_path):
    path = tmp_path / "progress.jsonl"
    good = json.dumps({"seq": 1, "ts": 1.0, "ev": "plan",
                       "units": [{"key": "a", "label": "A", "kind": "doc"}]})
    path.write_text(good + "\n\n" + '{"seq": 2, "ev": "unit_st', encoding="utf-8")
    events = prog.load_events(path)
    assert len(events) == 1 and events[0]["ev"] == "plan"


def test_load_events_missing_file_is_empty(tmp_path):
    assert prog.load_events(tmp_path / "없음.jsonl") == []


def test_jsonl_write_failure_never_raises(tmp_path):
    """경로가 폴더라 쓸 수 없어도 파이프라인은 계속 돈다."""
    folder = tmp_path / "progress.jsonl"
    folder.mkdir()
    prog.set_reporter(prog.JsonlProgress(folder))
    _two_units()
    prog.unit_start("a")      # 두 번째 호출부터는 시도조차 안 한다(broken)


def test_reporter_exception_is_swallowed():
    class _Boom:
        def emit(self, event):
            raise ValueError("boom")

    prog.set_reporter(_Boom())
    _two_units()              # 예외가 새어 나오면 실패


def test_to_dict_is_json_serializable():
    mem = _memory()
    _two_units()
    prog.unit_start("a", parts=2)
    prog.part(2, "F1 profile")
    data = prog.summarize(mem.events).to_dict()
    json.dumps(data, ensure_ascii=False)
    # 단위 a 의 내부 = (2-1 + 0) / 2 = 0.5 → 전체 = 0.5 / 2 = 0.25
    assert data["total"] == 2 and data["percent"] == 25
    assert data["units"][0] == {"key": "a", "label": "문서A", "kind": "doc",
                                "state": "running", "error": ""}
    assert data["current"] == {"key": "a", "label": "문서A", "part_name": "F1 profile",
                               "part_index": 2, "parts": 2, "step_done": 0, "step_total": 0}
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_progress.py -q`
Expected: FAIL — `ImportError: cannot import name 'progress' from 'contentcompare'`

- [ ] **Step 3: 구현** — `contentcompare/progress.py`

```python
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
    """
    snap = Snapshot()
    index: dict[str, UnitState] = {}
    run: dict = {}
    best = 0.0
    for ev in events:
        kind = ev.get("ev")
        ts = float(ev.get("ts") or 0.0)
        if ts:
            snap.started_ts = snap.started_ts or ts
            snap.last_ts = ts
        snap.last_seq = max(snap.last_seq, int(ev.get("seq") or 0))
        if kind == "plan":
            snap.units, index, run, best = [], {}, {}, 0.0
            for u in ev.get("units") or []:
                key = str(u.get("key") or "")
                if not key or key in index:
                    continue  # 겹치는 키(같은 basename 대상)는 첫 것만 센다
                state = UnitState(key, str(u.get("label") or key), str(u.get("kind") or ""))
                index[key] = state
                snap.units.append(state)
            continue
        key = str(ev.get("key") or "")
        state = index.get(key)
        if kind == "unit_start" and state is not None and state.state not in FINISHED:
            state.state = RUNNING
            run = {"key": key, "parts": max(1, int(ev.get("parts") or 1)),
                   "index": 0, "name": "", "done": 0, "total": 0}
        elif kind == "part" and key and run.get("key") == key:
            run.update(index=int(ev.get("index") or 0), name=str(ev.get("name") or ""),
                       done=0, total=0)
        elif kind == "step" and key and run.get("key") == key:
            run.update(done=int(ev.get("done") or 0), total=int(ev.get("total") or 0))
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
        best = max(best, _fraction(snap.units, run))
    snap.fraction = best
    if run:
        snap.current = run["key"]
        snap.current_label = index[run["key"]].label
        snap.part_name = run["name"]
        snap.part_index = run["index"]
        snap.parts = run["parts"]
        snap.step_done = run["done"]
        snap.step_total = run["total"]
    return snap
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_progress.py -q`
Expected: 16 passed

- [ ] **Step 5: `CLAUDE.md` 에 절 추가** — 「### 실행 타임라인 (`timeline.py`)」 절이 끝나는 곳(「조회는 `python scripts/show_timeline.py` …」 문단 다음)에 아래를 붙인다.

```markdown
### 진행률 (`progress.py`) — "전체 N 단계 중 몇 단계"

웹 실행 창의 % 를 만드는 모듈이다. `timeline.py` 와 **역할이 다르다** — 타임라인은 사람이 읽는 진단 기록이라 배치 번호가 `"배치 3/7"` **문자열 안에만** 있고 F5·F7 반복에는 이벤트가 없다. 진행률을 이름 파싱에 기대면 이름 형식만 바꿔도 조용히 깨지므로 따로 뒀다.

**쓰는 쪽은 사실만, 계산은 읽는 쪽이.** 파이프라인은 `prog.plan`/`unit_start`/`part`/`step`/`unit_done` 만 부르고, 비율과 단조 보장은 `summarize(events)` 가 한다. 단계 N 은 `plan` 으로 **시작 시 확정하고 바꾸지 않는다**(fact: 문서 × (1+T) + F7 1 + F5 × T / rag: 준비 1 + 판정 1). ⚠️ 두 파이프라인의 `run()` 에 `progress` 라는 **매개변수**가 이미 있어서 모듈은 `prog` 로 가져온다. 기본 보고기는 `NullProgress` 라 CLI·테스트는 무영향이고, 웹 worker 가 `JsonlProgress` 를 설치한다. 실행 끝 `finally` 의 `finish_remaining()` 을 지우지 말 것 — 실패한 대상의 F5 단위처럼 영영 시작 안 되는 단위가 막대를 100% 아래에 묶어 둔다.
```

- [ ] **Step 6: 커밋**

```bash
git add contentcompare/progress.py tests/test_progress.py CLAUDE.md
git commit -m "feat(progress): 진행률 보고 모듈 — 쓰는 쪽은 사실만, 계산은 summarize 가

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015WPCPHZGMqNLc7QQAv1ako"
```

---

### Task 2: fact 엔진 — 단위 계획과 문서 처리 단위(F0~F4a, F2·F3 배치)

**Files:**
- Modify: `contentcompare/fact/pipeline.py` (import, 모듈 상수, `run()` 118-142, `_process_one_safe()` 366-392, `_process_one()` 394-519)
- Modify: `contentcompare/fact/record_normalizer.py` (import, 배치 루프 200-201)
- Modify: `contentcompare/fact/fact_extractor.py` (import, 배치 루프 `for index, batch in enumerate(batches, start=1):`)
- Create: `tests/test_fact_pipeline_progress.py`

**Interfaces:**
- Consumes: Task 1 의 `prog.plan`, `prog.Unit`, `prog.DOC`/`CONCEPT`/`COMPARE`, `prog.unit_start`, `prog.part`, `prog.step`, `prog.unit_done`, `prog.finish_remaining`, `prog.MemoryProgress`, `prog.summarize`.
- Produces:
  - 단위 키 규약(계획 2·3 이 화면 표시에 쓴다): `doc:<i>`(i=0 이 기준), `concept`, `compare:<대상 basename>`
  - `contentcompare.fact.pipeline.EXCEL_PARTS = ("F0 raw", "F1 profile", "F1 schema", "F2 records", "F3 facts", "F4a 검증")`
  - `contentcompare.fact.pipeline.BLOCK_PARTS = ("F0 raw", "F1 profile", "F3 facts", "F4a 검증")`
  - `FactPipeline._progress_units(reference: str, targets: list[str]) -> list[prog.Unit]`
  - `FactPipeline._process_one_safe(path, store, *, is_reference=False, unit_key: str = "")` — 새 키워드 인자

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_fact_pipeline_progress.py`

```python
"""fact 파이프라인이 진행률 단위를 빠짐없이 열고 닫는가.

``test_fact_pipeline_smoke`` 의 가짜 추출기·chat·embedder 를 그대로 쓴다(tests/ 는
pytest 의 prepend 모드로 sys.path 에 올라와 있다).
"""

from __future__ import annotations

import os

import pytest

from contentcompare import progress as prog
from contentcompare.fact.pipeline import BLOCK_PARTS, EXCEL_PARTS, FactPipeline
from contentcompare.raw.excel_raw import CellProbe, SheetProbe, build_raw_sheet
from contentcompare.raw.models import RawExcelDocument
from test_fact_pipeline_smoke import (
    _config,
    _excel_or_ppt,
    _fake_excel,
    _FakeEmbedder,
    _pipe,
    _ppt_chat,
)


@pytest.fixture(autouse=True)
def mem():
    reporter = prog.MemoryProgress()
    prog.set_reporter(reporter)
    yield reporter
    prog.reset_reporter()


def _events(mem, ev, key=None):
    return [e for e in mem.events if e["ev"] == ev and (key is None or e.get("key") == key)]


def _fractions(events):
    return [prog.summarize(events[:k]).fraction for k in range(1, len(events) + 1)]


def _fake_excel_3rows(path):
    doc = RawExcelDocument(file_name=os.path.basename(path))
    cells = [CellProbe(1, 5, "항목"), CellProbe(1, 6, "하한치"), CellProbe(1, 7, "상한치")]
    for r, name in ((2, "충전환경온도"), (3, "보관온도"), (4, "방전온도")):
        cells += [CellProbe(r, 5, name), CellProbe(r, 6, -5), CellProbe(r, 7, 55)]
    probe = SheetProbe(name="S", cells=cells, min_row=1, max_row=4, min_col=5, max_col=7)
    doc.sheets.append(build_raw_sheet(probe))
    return doc


def test_plan_lists_every_unit_up_front(tmp_path, mem):
    _pipe(tmp_path, extractor=_excel_or_ppt, chat=_ppt_chat()).run("기준.xlsx", ["발표.pptx"])
    plans = _events(mem, "plan")
    assert len(plans) == 1 and mem.events[0]["ev"] == "plan"
    assert [u["key"] for u in plans[0]["units"]] == [
        "doc:0", "doc:1", "concept", "compare:발표.pptx"]


def test_document_parts_follow_the_document_type(tmp_path, mem):
    _pipe(tmp_path, extractor=_excel_or_ppt, chat=_ppt_chat()).run("기준.xlsx", ["발표.pptx"])
    assert [e["name"] for e in _events(mem, "part", "doc:0")] == list(EXCEL_PARTS)
    assert [e["name"] for e in _events(mem, "part", "doc:1")] == list(BLOCK_PARTS)
    starts = {e["key"]: e["parts"] for e in _events(mem, "unit_start")}
    assert starts["doc:0"] == len(EXCEL_PARTS) and starts["doc:1"] == len(BLOCK_PARTS)


def test_every_unit_finishes_and_run_ends_at_one(tmp_path, mem):
    _pipe(tmp_path, extractor=_excel_or_ppt, chat=_ppt_chat()).run("기준.xlsx", ["발표.pptx"])
    snap = prog.summarize(mem.events)
    assert snap.fraction == 1.0
    assert {u.key: u.state for u in snap.units} == {
        "doc:0": prog.DONE, "doc:1": prog.DONE,
        "concept": prog.DONE, "compare:발표.pptx": prog.DONE}
    fractions = _fractions(mem.events)
    assert fractions == sorted(fractions)


def test_f2_batches_report_inside_the_records_part(tmp_path, mem):
    cfg = _config(tmp_path)
    cfg.fact.record_batch_rows = 1
    from test_fact_pipeline_smoke import _FactChat

    FactPipeline(cfg, extractor=_fake_excel_3rows, chat=_FactChat(),
                 embedder=_FakeEmbedder()).run("기준.xlsx", [])
    seq = [(e["ev"], e.get("name"), e.get("done"), e.get("total"))
           for e in mem.events if e.get("key") == "doc:0" and e["ev"] in ("part", "step")]
    i = seq.index(("part", "F2 records", None, None))
    assert seq[i + 1:i + 5] == [("step", None, 0, 3), ("step", None, 1, 3),
                                ("step", None, 2, 3), ("step", None, 3, 3)]


def test_f3_block_batches_end_at_their_total(tmp_path, mem):
    _pipe(tmp_path, extractor=_excel_or_ppt, chat=_ppt_chat()).run("기준.xlsx", ["발표.pptx"])
    steps = _events(mem, "step", "doc:1")
    assert steps, "Word/PPT 의 F3 배치 진척이 없다"
    assert steps[-1]["done"] == steps[-1]["total"] >= 1


def test_failed_document_is_closed_as_failed(tmp_path, mem):
    def flaky(path):
        if path == "깨진.xlsx":
            raise OSError("파일을 열 수 없습니다")
        return _fake_excel(path)

    _pipe(tmp_path, extractor=flaky).run("기준.xlsx", ["깨진.xlsx", "대상.xlsx"])
    snap = prog.summarize(mem.events)
    states = {u.key: (u.state, u.error) for u in snap.units}
    assert states["doc:1"] == (prog.FAILED, "OSError")
    assert states["compare:깨진.xlsx"] == (prog.SKIPPED, "건너뜀")
    assert states["compare:대상.xlsx"] == (prog.DONE, "")
    assert snap.fraction == 1.0


def test_cache_hit_rerun_is_monotonic_and_completes(tmp_path, mem):
    cfg = _config(tmp_path)
    cfg.fact.record_batch_rows = 1
    from test_fact_pipeline_smoke import _FactChat

    FactPipeline(cfg, extractor=_fake_excel_3rows, chat=_FactChat(),
                 embedder=_FakeEmbedder()).run("기준.xlsx", [])
    second = prog.MemoryProgress()
    prog.set_reporter(second)
    FactPipeline(cfg, extractor=_fake_excel_3rows, chat=_FactChat(),
                 embedder=_FakeEmbedder()).run("기준.xlsx", [])
    # 캐시 적중이면 F2 배치 루프가 아예 돌지 않는다.
    assert not [e for e in second.events if e["ev"] == "step" and e.get("key") == "doc:0"
                and e.get("total") == 3]
    fractions = _fractions(second.events)
    assert fractions == sorted(fractions) and fractions[-1] == 1.0


def test_no_targets_skips_concept_unit(tmp_path, mem):
    _pipe(tmp_path).run("기준.xlsx", [])
    snap = prog.summarize(mem.events)
    assert {u.key: u.state for u in snap.units} == {
        "doc:0": prog.DONE, "concept": prog.SKIPPED}
    assert snap.fraction == 1.0
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_fact_pipeline_progress.py -q`
Expected: FAIL — `ImportError: cannot import name 'BLOCK_PARTS' from 'contentcompare.fact.pipeline'`

- [ ] **Step 3: `fact/pipeline.py` — import 와 상수**

`from ..config import AppConfig, FactConfig` 줄 **위**에 추가:

```python
from .. import progress as prog
```

`logger = logging.getLogger(__name__)` 줄 **아래**에 추가:

```python

# 진행률(:mod:`contentcompare.progress`)의 문서 하위 단계 — ``_process_one`` 의 순서와 같다.
# Excel 의 F3 는 코드 결정적이라 순식간에 지나가지만 단계로는 센다(무엇이 끝났는지 보이게).
EXCEL_PARTS = ("F0 raw", "F1 profile", "F1 schema", "F2 records", "F3 facts", "F4a 검증")
BLOCK_PARTS = ("F0 raw", "F1 profile", "F3 facts", "F4a 검증")
_EXCEL_EXTS = (".xlsx", ".xls", ".xlsm")


def _doc_parts(path: str) -> tuple[str, ...]:
    """확장자로 하위 단계 목록을 고른다 — 단위 시작 시점(F0 전)에는 doc_type 을 아직 모른다."""
    return EXCEL_PARTS if os.path.splitext(path)[1].lower() in _EXCEL_EXTS else BLOCK_PARTS


def _enter(parts: tuple[str, ...], name: str) -> None:
    """하위 단계 진입을 알린다. 목록에 없는 이름이면 조용히 넘어간다 — 진행률이 실행을
    막으면 안 된다(확장자와 실제 doc_type 이 어긋나는 문서가 와도 죽지 않는다)."""
    if name in parts:
        prog.part(parts.index(name) + 1, name)
```

- [ ] **Step 4: `fact/pipeline.py` — `run()` 을 다음으로 교체**

```python
    def run(
        self,
        reference: str,
        targets: list[str],
        progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> FactRunResult:
        """문서를 fact 로 정규화(F0~F3)·검증(F4a)한 뒤 fact 끼리 비교(F5)해 리포트(F6)를 만든다.

        문서 하나가 실패해도(COM 오류·LLM 예산 초과·JSON 파싱 실패) 나머지는 계속
        처리하고, 그 문서의 summary 에 ``status="error"`` 와 사유를 남긴다.
        ``finally`` 에서 열린 COM 문서를 정리한다.

        ``progress`` 콜백(문서 단위)과 별개로 :mod:`contentcompare.progress` 에 단계별
        진척을 알린다 — 보고기가 설치되지 않았으면 아무 일도 하지 않는다.
        """
        docs = [reference, *targets]
        store = FactStore()
        result = FactRunResult()
        prog.plan(self._progress_units(reference, targets))
        try:
            for i, path in enumerate(docs, start=1):
                summary = self._process_one_safe(
                    path, store, is_reference=(i == 1), unit_key=f"doc:{i - 1}")
                result.summaries.append(summary)
                if progress:
                    progress(i, len(docs), path)
            self._compare_and_report(store, reference, targets, result)
            return result
        finally:
            # 시작도 못 한 단위(실패한 대상의 F5, 대상이 없어 건너뛴 F7)를 닫는다.
            # 이게 없으면 막대가 100% 에 닿지 못해 "끝났는데 멈춘 것처럼" 보인다.
            prog.finish_remaining("건너뜀")
            close_all_office()

    def _progress_units(self, reference: str, targets: list[str]) -> list[prog.Unit]:
        """진행률 단계 N — **시작 시 확정하고 실행 중 바꾸지 않는다**(설계 §7.1).

        F5 단위 키는 대상 basename 이다(``DocFacts.doc_name`` 과 같은 값이라 비교 루프에서
        그대로 찾을 수 있다). basename 이 겹치면 키도 겹치는데, ``summarize`` 가 첫 것만
        센다 — 같은 이름 대상은 artifacts 폴더도 겹치므로 웹 업로드가 먼저 거절한다.
        """
        docs = [reference, *targets]
        units = [prog.Unit(f"doc:{i}", os.path.basename(p), prog.DOC)
                 for i, p in enumerate(docs)]
        if self.fact.use_concept_graph:
            units.append(prog.Unit("concept", "F7 개념 판정", prog.CONCEPT))
        units += [
            prog.Unit(f"compare:{os.path.basename(t)}",
                      f"F5 값 대조 · {os.path.basename(t)}", prog.COMPARE)
            for t in targets
        ]
        return units
```

- [ ] **Step 5: `fact/pipeline.py` — `_process_one_safe()` 를 다음으로 교체**

```python
    def _process_one_safe(
        self, path: str, store: FactStore, *, is_reference: bool = False,
        unit_key: str = "",
    ) -> dict:
        """문서 1개를 처리하되 예외를 격리해 summary 로 변환하고, fact 를 store 에 넣는다.

        실문서 라이브에서는 COM 추출 예외(``pywintypes.com_error``/``OSError``)가
        LLM 오류만큼 흔하므로 넓게 잡는다. traceback 은 로그 파일에 남긴다.

        진행률 단위는 여기서 열고 닫는다 — 실패도 완료로 센다(격리하고 계속하는 동작과 같다).
        """
        name = os.path.basename(path)
        key = unit_key or f"doc:{name}"
        prog.unit_start(key, parts=len(_doc_parts(path)))
        # 실패해도 "어디까지 갔는지"를 보고해야 하므로 진행 상태를 밖에서 들고 있는다.
        stages: list[str] = []
        stats: dict[str, Any] = {}
        try:
            summary = self._process_one(path, stages, stats, store, is_reference)
            summary["status"] = "ok"
            logger.info("[Fact] ✅ %s (LLM %d회)", name, summary.get("llm_calls", 0))
            prog.unit_done(key)
            return summary
        except Exception as e:  # noqa: BLE001 — 문서 단위 격리가 목적
            logger.exception("[Fact] ❌ %s 처리 실패", name)
            prog.unit_done(key, ok=False, error=type(e).__name__)
            return {
                "path": path,
                "status": "error",
                "error": f"{type(e).__name__}: {e}",
                "stages": stages,
                "llm_calls": stats.get("llm", {}).get("calls", 0),
                "stats": stats,
            }
```

- [ ] **Step 6: `fact/pipeline.py` — `_process_one()` 에 하위 단계 진입 6곳 추가**

`doc_label = os.path.basename(path)` 줄 **아래**에:

```python
        parts = _doc_parts(path)
```

`# F0: raw → physical_raw, compact → compact_raw` 주석 **아래**, `raw_obj = self._extract(path)` **위**에:

```python
        _enter(parts, "F0 raw")
```

`with stage(f"F1 document_profile · {doc_label}"):` **위**에:

```python
            _enter(parts, "F1 profile")
```

`with stage(f"F1 column_schema · {doc_label}"):` **위**에(Excel 분기 안, 같은 들여쓰기):

```python
                _enter(parts, "F1 schema")
```

`with stage(f"F2 records · {doc_label}"):` **위**에:

```python
                _enter(parts, "F2 records")
```

`# F3: records → facts (코드 결정적, 무 LLM)` 주석 **위**에:

```python
                _enter(parts, "F3 facts")
```

`else:` 분기의 `# F3: Word/PPT 는 블록/도형 → facts 직행 (LLM)` 주석 **위**에:

```python
                _enter(parts, "F3 facts")
```

`report = validate_facts(facts, compact, column_schema=column_schema)` **위**에:

```python
            _enter(parts, "F4a 검증")
```

- [ ] **Step 7: `record_normalizer.py` — F2 배치 진척**

import 블록의 `from ..logging_setup import log_print` **위**에:

```python
from .. import progress as prog
```

`compute()` 끝의 배치 루프를 다음으로 교체:

```python
        prog.step(0, len(batches))
        for index, batch in enumerate(batches, start=1):
            run_batch(batch, call, name=label, stats=split)
            # 원래 배치 번호로만 센다 — 쪼개진 조각은 run_batch 안에서 끝나므로 세지 않는다.
            prog.step(index, len(batches))
```

- [ ] **Step 8: `fact_extractor.py` — F3 배치 진척**

import 블록의 `from ..llm.tracing import substage` **위**에:

```python
from .. import progress as prog
```

`_facts_from_blocks` 의 배치 루프를 다음으로 교체:

```python
    prog.step(0, len(batches))
    for index, batch in enumerate(batches, start=1):
        run_batch(batch, call, name=label, stats=split)
        prog.step(index, len(batches))
```

- [ ] **Step 9: 테스트 통과 확인**

Run: `python -m pytest tests/test_fact_pipeline_progress.py -q`
Expected: 6 passed, 2 failed.
- PASS: `test_plan_lists_every_unit_up_front`, `test_document_parts_follow_the_document_type`, `test_f2_batches_report_inside_the_records_part`, `test_f3_block_batches_end_at_their_total`, `test_cache_hit_rerun_is_monotonic_and_completes`, `test_no_targets_skips_concept_unit`(대상이 없으면 F7 이 원래 안 돈다)
- FAIL(정상): `test_every_unit_finishes_and_run_ends_at_one`, `test_failed_document_is_closed_as_failed` — F7·F5 단위가 아직 없어 concept/compare 가 SKIPPED 로 닫힌다. Task 3 에서 통과한다.

- [ ] **Step 10: 기존 테스트 무변화 확인**

Run: `python -m pytest tests/test_fact_pipeline_smoke.py tests/test_fact_pipeline_concept.py tests/test_fact_gate_pipeline.py -q`
Expected: 전부 PASS(기본 보고기가 Null 이라 동작이 같다)

- [ ] **Step 11: 커밋**

```bash
git add contentcompare/fact/pipeline.py contentcompare/fact/record_normalizer.py contentcompare/fact/fact_extractor.py tests/test_fact_pipeline_progress.py
git commit -m "feat(progress): fact 문서 처리 단위 — 하위 단계와 F2·F3 배치 진척

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015WPCPHZGMqNLc7QQAv1ako"
```

---

### Task 3: fact 엔진 — F7 개념 판정·F5 값 대조 단위

**Files:**
- Modify: `contentcompare/fact/pipeline.py` (`_compare_from_store()` 의 F5 루프, `_build_graph()` 의 `with stage("F7 개념 판정"):`)
- Modify: `contentcompare/fact/concept_builder.py` (import, `judge_pairs` 배치 루프)
- Modify: `tests/test_fact_pipeline_progress.py` (테스트 2건 추가)
- Modify: `tests/test_concept_builder_llm.py` (테스트 1건 추가)

**Interfaces:**
- Consumes: Task 1 `prog.unit`, `prog.step`, `prog.unit_start`, `prog.MemoryProgress`, `prog.summarize`; Task 2 의 단위 키 `concept`, `compare:<basename>`.
- Produces: 없음(계획 1 의 마지막 fact 훅).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_fact_pipeline_progress.py` 끝에 추가

```python
def test_f5_steps_count_reference_facts(tmp_path, mem):
    _pipe(tmp_path, extractor=_excel_or_ppt, chat=_ppt_chat()).run("기준.xlsx", ["발표.pptx"])
    steps = [(e["done"], e["total"]) for e in _events(mem, "step", "compare:발표.pptx")]
    assert steps == [(0, 1), (1, 1)]


def test_concept_failure_closes_every_unit(tmp_path, mem, monkeypatch):
    from contentcompare.fact import concept_builder

    def boom(*args, **kwargs):
        raise RuntimeError("F7 폭발")

    monkeypatch.setattr(concept_builder, "build_concept_graph", boom)
    with pytest.raises(RuntimeError):
        _pipe(tmp_path, extractor=_excel_or_ppt, chat=_ppt_chat()).run(
            "기준.xlsx", ["발표.pptx"])
    snap = prog.summarize(mem.events)
    states = {u.key: (u.state, u.error) for u in snap.units}
    assert states["concept"] == (prog.FAILED, "RuntimeError")
    assert states["compare:발표.pptx"] == (prog.SKIPPED, "건너뜀")
    assert snap.finished == snap.total and snap.fraction == 1.0
```

`tests/test_concept_builder_llm.py` 끝에 추가:

```python
def test_f7_reports_batch_progress():
    """F7 배치마다 진척을 알린다 — 분할이 일어나도 원래 배치 번호로만 센다."""
    from contentcompare import progress as prog

    mem = prog.MemoryProgress()
    prog.set_reporter(mem)
    try:
        prog.unit_start("concept")
        judge_pairs(_TruncatingConceptRunner(limit=99), _pairs(3), batch_size=1)
    finally:
        prog.reset_reporter()
    steps = [(e["done"], e["total"]) for e in mem.events if e["ev"] == "step"]
    assert steps == [(0, 3), (1, 3), (2, 3), (3, 3)]
    assert all(e["key"] == "concept" for e in mem.events if e["ev"] == "step")
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_fact_pipeline_progress.py tests/test_concept_builder_llm.py::test_f7_reports_batch_progress -q`
Expected: FAIL — `test_f5_steps_count_reference_facts` 는 steps 가 `[]`, `test_concept_failure_closes_every_unit` 는 concept 가 `(failed, "중단")`, `test_f7_reports_batch_progress` 는 steps 가 `[]`

- [ ] **Step 3: `concept_builder.py` — F7 배치 진척**

import 블록의 `from ..llm.truncation import LengthLimitError` **위**에:

```python
from .. import progress as prog
```

`judge_pairs` 에서 `batches = [...]` 줄 **아래**, `for index, batch in enumerate(batches, start=1):` **위**에:

```python
    prog.step(0, len(batches))
```

같은 루프 본문의 `exhausted += batch_exhausted` **아래**(루프 안, 같은 들여쓰기)에:

```python
        prog.step(index, len(batches))
```

- [ ] **Step 4: `fact/pipeline.py` — F7 단위**

`_build_graph()` 의

```python
        with stage("F7 개념 판정"):
```

를 다음으로 교체(본문은 그대로):

```python
        with prog.unit("concept"), stage("F7 개념 판정"):
```

- [ ] **Step 5: `fact/pipeline.py` — F5 단위**

`_compare_from_store()` 의 `for target in store.targets:` 루프 안에서

```python
            with stage(f"F5 값 대조 · {target.doc_name}"):
                for ref_fact in ref_doc.facts.facts:
```

를 다음으로 교체:

```python
            ref_facts = ref_doc.facts.facts
            with prog.unit(f"compare:{target.doc_name}"), \
                    stage(f"F5 값 대조 · {target.doc_name}"):
                prog.step(0, len(ref_facts))
                for n, ref_fact in enumerate(ref_facts, start=1):
```

그리고 같은 루프 본문의 마지막 줄 `result.comparisons.append(comparison)` **아래**(같은 들여쓰기)에:

```python
                    prog.step(n, len(ref_facts))
```

루프 본문의 나머지 줄은 한 글자도 바꾸지 않는다.

- [ ] **Step 6: 테스트 통과 확인**

Run: `python -m pytest tests/test_fact_pipeline_progress.py tests/test_concept_builder_llm.py -q`
Expected: 전부 PASS(Task 2 에서 FAIL 이던 3건 포함)

- [ ] **Step 7: 커밋**

```bash
git add contentcompare/fact/pipeline.py contentcompare/fact/concept_builder.py tests/test_fact_pipeline_progress.py tests/test_concept_builder_llm.py
git commit -m "feat(progress): F7 개념 판정·F5 값 대조 단위와 배치/항목 진척

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015WPCPHZGMqNLc7QQAv1ako"
```

---

### Task 4: RAG 엔진 — 준비·판정 단위

**Files:**
- Modify: `contentcompare/pipeline.py` (import, `run()`, `_run()`)
- Create: `tests/test_pipeline_progress.py`

**Interfaces:**
- Consumes: Task 1 `prog.plan`, `prog.Unit`, `prog.RAG_PREPARE`, `prog.RAG_JUDGE`, `prog.unit`, `prog.part`, `prog.step`, `prog.finish_remaining`.
- Produces: 단위 키 규약 `rag:prepare`(하위 단계 = 문서 수 + 1, 이름은 문서 basename 들과 `"인덱싱"`), `rag:judge`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_pipeline_progress.py`

```python
"""RAG 파이프라인 진행률 — 준비(읽기·인덱싱) 1 + 판정 1."""

from __future__ import annotations

import pytest

from contentcompare import pipeline as rag_mod
from contentcompare import progress as prog
from contentcompare.config import AppConfig
from contentcompare.models import DocItem, DocType


@pytest.fixture(autouse=True)
def mem():
    reporter = prog.MemoryProgress()
    prog.set_reporter(reporter)
    yield reporter
    prog.reset_reporter()


class _Embedder:
    def embed(self, texts, *, kind="passage"):
        return [[1.0, float(len(t) % 3)] for t in texts]


def _items(doc: str, n: int) -> list[DocItem]:
    return [DocItem(item_id=f"{doc}#{i}", doc_id=doc, doc_type=DocType.WORD,
                    text=f"{doc} 내용 {i}", source_label=f"{doc}#{i}") for i in range(n)]


class _Reader:
    def __init__(self, fail: str = ""):
        self.fail = fail

    def read(self, path):
        if path == self.fail:
            raise OSError("열 수 없음")
        return _items(path, 3)


class _StubComparator:
    def compare(self, ref, candidates):
        return ref.item_id

    def compare_record(self, ref, candidates):
        return ref.item_id


def _pipe(tmp_path, monkeypatch, reader):
    monkeypatch.setattr(rag_mod, "build_clients", lambda cfg: (object(), _Embedder()))
    monkeypatch.setattr(rag_mod, "get_reader", lambda path, config, llm=None: reader)
    monkeypatch.setattr(rag_mod, "close_all_office", lambda: None)
    cfg = AppConfig()
    cfg.similarity.cache_dir = str(tmp_path / "emb")
    cfg.knowledge.enabled = False
    pipe = rag_mod.ComparePipeline(cfg)
    pipe.comparator = _StubComparator()
    return pipe


def test_rag_plan_parts_and_steps(tmp_path, monkeypatch, mem):
    _pipe(tmp_path, monkeypatch, _Reader()).run("기준.xlsx", ["A.docx"])
    plan = [e for e in mem.events if e["ev"] == "plan"][0]
    assert [u["key"] for u in plan["units"]] == ["rag:prepare", "rag:judge"]
    parts = [e["name"] for e in mem.events if e["ev"] == "part"]
    assert parts == ["기준.xlsx", "A.docx", "인덱싱"]
    steps = [(e["done"], e["total"]) for e in mem.events if e["ev"] == "step"]
    assert steps == [(0, 3), (1, 3), (2, 3), (3, 3)]
    snap = prog.summarize(mem.events)
    assert snap.fraction == 1.0 and snap.finished == 2


def test_rag_read_failure_closes_both_units(tmp_path, monkeypatch, mem):
    with pytest.raises(OSError):
        _pipe(tmp_path, monkeypatch, _Reader(fail="A.docx")).run("기준.xlsx", ["A.docx"])
    snap = prog.summarize(mem.events)
    states = {u.key: (u.state, u.error) for u in snap.units}
    assert states == {"rag:prepare": (prog.FAILED, "OSError"),
                      "rag:judge": (prog.SKIPPED, "건너뜀")}
    assert snap.fraction == 1.0
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_pipeline_progress.py -q`
Expected: FAIL — plan 이벤트가 없어 `IndexError: list index out of range`

- [ ] **Step 3: `pipeline.py` — import**

`import logging` 줄 **아래**에:

```python
import os
```

`from .comparison import Comparator` 줄 **위**에:

```python
from . import progress as prog
```

- [ ] **Step 4: `pipeline.py` — `run()` 과 `_run()` 을 다음으로 교체**

```python
    def run(
        self,
        reference_path: str,
        target_paths: list[str],
        *,
        progress: Optional[ProgressFn] = None,
    ) -> list[CompareResult]:
        # 진행률 단계 N = 준비 1 + 판정 1 (설계 §7.1). ``progress`` 콜백과는 별개다.
        prog.plan([
            prog.Unit("rag:prepare", "문서 읽기·인덱싱", prog.RAG_PREPARE),
            prog.Unit("rag:judge", "기준 항목 판정", prog.RAG_JUDGE),
        ])
        try:
            return self._run(reference_path, target_paths, progress=progress)
        finally:
            prog.finish_remaining("건너뜀")
            # 오류/정상 종료 어느 경우든, 열린 채 남은 Office 문서를 완전히 종료한다.
            # (각 리더가 자체 finally 로 닫지만, 예기치 못한 경로를 대비한 안전망.)
            close_all_office()

    def _run(
        self,
        reference_path: str,
        target_paths: list[str],
        *,
        progress: Optional[ProgressFn] = None,
    ) -> list[CompareResult]:
        docs = [reference_path, *target_paths]
        # 준비 단위의 하위 단계 = 문서마다 1 + 인덱싱 1.
        with prog.unit("rag:prepare", parts=len(docs) + 1):
            # 1) 문서 읽기
            prog.part(1, os.path.basename(reference_path))
            reference_items = self._read(reference_path)
            logger.info("기준 항목 %d개 추출: %s", len(reference_items), reference_path)

            target_items: list[DocItem] = []
            for n, path in enumerate(target_paths, start=2):
                prog.part(n, os.path.basename(path))
                items = self._read(path)
                logger.info("대상 항목 %d개 추출: %s", len(items), path)
                target_items.extend(items)

            # 2) 하이브리드 인덱스 구축 (청킹 후, 임베딩 캐시 적용)
            prog.part(len(docs) + 1, "인덱싱")
            sim = self.config.similarity
            chunks = chunk_items(target_items, sim.chunk_chars)
            embedder = CachedEmbedder(
                self.embedder, sim.cache_dir, model_name=self.config.llm.embed_model
            )
            index = HybridIndex(
                embedder,
                fusion=sim.fusion,
                rrf_k=sim.rrf_k,
                mmr_lambda=sim.mmr_lambda,
                per_doc_cap=sim.per_doc_cap,
                min_score=sim.min_score,
            )
            index.add(chunks)
            logger.info("하이브리드 인덱스 구축 완료: 벡터 %d개", len(index))

        # 3~4) 기준 항목 순차 비교
        results: list[CompareResult] = []
        total = len(reference_items)
        with prog.unit("rag:judge"):
            prog.step(0, total)
            for i, ref in enumerate(reference_items, start=1):
                if ref.is_empty():
                    prog.step(i, total)  # 빈 항목도 센다 — 안 세면 막대가 끝에 못 닿는다
                    continue
                candidates = index.search(
                    ref.text,
                    recall_k=sim.recall_k,
                    top_k=sim.top_k,
                )
                # 엑셀 hybrid/field: 필드를 가진 RecordItem 이면 필드별 판정.
                if isinstance(ref, RecordItem) and ref.fields:
                    result: CompareResult = self.comparator.compare_record(ref, candidates)
                else:
                    result = self.comparator.compare(ref, candidates)
                results.append(result)
                if progress:
                    progress(i, total, result)
                prog.step(i, total)
        return results
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_pipeline_progress.py tests/test_pipeline_smoke.py -q`
Expected: 전부 PASS

- [ ] **Step 6: 전체 테스트**

Run: `python -m pytest -q`
Expected: 새 테스트 전부 PASS. 실패는 `.venv` 의 `httpx` 부재로 인한 `tests/test_timeline_wiring.py` 5건뿐(선행 조건의 기준선과 같아야 한다).

- [ ] **Step 7: 커밋**

```bash
git add contentcompare/pipeline.py tests/test_pipeline_progress.py
git commit -m "feat(progress): RAG 준비·판정 단위 — 문서 읽기·인덱싱을 하위 단계로

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015WPCPHZGMqNLc7QQAv1ako"
```
