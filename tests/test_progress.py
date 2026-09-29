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
