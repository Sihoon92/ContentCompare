"""스케줄러 — 한 번에 1건, 접수 순서, 취소, 재시작 복구."""

from __future__ import annotations

import json
import time

from contentcompare.web import jobs as J
from contentcompare.web.scheduler import JobScheduler


class _Handle:
    def __init__(self):
        self.code = None
        self.terminated = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = -1


class _Launcher:
    def __init__(self):
        self.handles: dict[str, _Handle] = {}

    def __call__(self, job, job_dir):
        handle = _Handle()
        self.handles[job.id] = handle
        return handle


class _Office:
    def __init__(self):
        self.snapshots = 0
        self.cleaned: list[set] = []

    def snapshot(self):
        self.snapshots += 1
        return {100}

    def cleanup(self, before):
        self.cleaned.append(set(before))
        return []


def _setup(tmp_path, clock=lambda: 1000.0):
    store = J.JobStore(tmp_path)
    launcher = _Launcher()
    office = _Office()
    sched = JobScheduler(store, launcher, office=office, clock=clock)
    return store, launcher, office, sched


def _submit(sched, n):
    job = J.Job(id=f"20260929-1402{n:02d}-000{n}", engine="fact")
    return sched.submit(job)


def test_one_at_a_time_in_submission_order(tmp_path):
    store, launcher, _, sched = _setup(tmp_path)
    a, b = _submit(sched, 1), _submit(sched, 2)
    sched.tick()
    assert list(launcher.handles) == [a.id]
    assert store.load(a.id).state == J.RUNNING
    assert sched.positions() == {b.id: 1}
    sched.tick()                                   # 아직 안 끝남 → 다음 것을 띄우지 않는다
    assert list(launcher.handles) == [a.id]


def test_success_starts_the_next_job_in_the_same_tick(tmp_path):
    store, launcher, office, sched = _setup(tmp_path)
    a, b = _submit(sched, 1), _submit(sched, 2)
    sched.tick()
    launcher.handles[a.id].code = 0
    sched.tick()
    assert store.load(a.id).state == J.SUCCEEDED
    assert store.load(b.id).state == J.RUNNING
    assert office.cleaned == []                    # 정상 종료면 Office 를 건드리지 않는다


def test_failure_reads_error_json_and_cleans_office(tmp_path):
    store, launcher, office, sched = _setup(tmp_path)
    a = _submit(sched, 1)
    sched.tick()
    (store.dir(a.id) / "error.json").write_text(
        json.dumps({"error": "ValueError: 파싱 실패"}), encoding="utf-8")
    launcher.handles[a.id].code = 1
    sched.tick()
    job = store.load(a.id)
    assert (job.state, job.error) == (J.FAILED, "ValueError: 파싱 실패")
    assert office.cleaned == [{100}]


def test_failure_without_error_json_reports_exit_code(tmp_path):
    store, launcher, _, sched = _setup(tmp_path)
    a = _submit(sched, 1)
    sched.tick()
    launcher.handles[a.id].code = 3
    sched.tick()
    assert store.load(a.id).error == "worker 종료코드 3"


def test_cancel_queued_never_launches(tmp_path):
    store, launcher, _, sched = _setup(tmp_path)
    a, b = _submit(sched, 1), _submit(sched, 2)
    assert sched.cancel(b.id)
    sched.tick()
    launcher.handles[a.id].code = 0
    sched.tick()
    assert store.load(b.id).state == J.CANCELLED
    assert list(launcher.handles) == [a.id]


def test_cancel_running_terminates_cleans_and_moves_on(tmp_path):
    store, launcher, office, sched = _setup(tmp_path)
    a, b = _submit(sched, 1), _submit(sched, 2)
    sched.tick()
    assert sched.cancel(a.id)
    assert launcher.handles[a.id].terminated
    assert store.load(a.id).state == J.CANCELLED
    assert office.cleaned == [{100}]
    sched.tick()
    assert store.load(b.id).state == J.RUNNING


def test_cancel_finished_is_refused(tmp_path):
    store, launcher, _, sched = _setup(tmp_path)
    a = _submit(sched, 1)
    sched.tick()
    launcher.handles[a.id].code = 0
    sched.tick()
    assert not sched.cancel(a.id)
    assert not sched.cancel("20260929-000000-ffff")


def test_recover_marks_running_interrupted_and_keeps_queue_order(tmp_path):
    store = J.JobStore(tmp_path)
    store.save(J.Job(id="20260929-140201-0001", engine="fact", state=J.RUNNING))
    store.save(J.Job(id="20260929-140203-0003", engine="fact", state=J.QUEUED, created_ts=3))
    store.save(J.Job(id="20260929-140202-0002", engine="fact", state=J.QUEUED, created_ts=2))
    launcher = _Launcher()
    sched = JobScheduler(store, launcher, office=_Office(), clock=lambda: 50.0)
    assert sched.recover() == ["20260929-140201-0001"]
    job = store.load("20260929-140201-0001")
    assert job.state == J.INTERRUPTED and "재시작" in job.error and job.finished_ts == 50.0
    sched.tick()
    assert list(launcher.handles) == ["20260929-140202-0002"]


def test_launcher_error_fails_the_job_and_keeps_going(tmp_path):
    store = J.JobStore(tmp_path)
    calls = []

    def launcher(job, job_dir):
        calls.append(job.id)
        if len(calls) == 1:
            raise OSError("python 을 찾을 수 없음")
        return _Handle()

    sched = JobScheduler(store, launcher, office=_Office(), clock=lambda: 1.0)
    a, b = _submit(sched, 1), _submit(sched, 2)
    sched.tick()
    assert store.load(a.id).state == J.FAILED
    assert "OSError" in store.load(a.id).error
    sched.tick()
    assert store.load(b.id).state == J.RUNNING


def test_stop_interrupts_the_running_job(tmp_path):
    store, launcher, _, sched = _setup(tmp_path)
    a = _submit(sched, 1)
    sched.tick()
    sched.stop()
    assert launcher.handles[a.id].terminated
    assert store.load(a.id).state == J.INTERRUPTED


def test_background_thread_runs_jobs_and_housekeeping(tmp_path):
    store = J.JobStore(tmp_path)
    launcher = _Launcher()
    swept = []
    sched = JobScheduler(store, launcher, office=_Office(), poll_interval=0.01,
                         housekeeping=lambda: swept.append(1), housekeeping_interval=0.0)
    a = _submit(sched, 1)
    sched.start()
    try:
        deadline = time.time() + 5
        while a.id not in launcher.handles and time.time() < deadline:
            time.sleep(0.01)
        launcher.handles[a.id].code = 0
        while store.load(a.id).state != J.SUCCEEDED and time.time() < deadline:
            time.sleep(0.01)
    finally:
        sched.stop()
    assert store.load(a.id).state == J.SUCCEEDED
    assert swept
