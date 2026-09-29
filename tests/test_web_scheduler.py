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


def test_cancel_tolerates_handle_terminate_exception(tmp_path):
    """handle.terminate() 예외 후에도 cancel()은 성공하고 job은 CANCELLED·_running은 None."""
    store, launcher, _, sched = _setup(tmp_path)
    a, b = _submit(sched, 1), _submit(sched, 2)
    sched.tick()

    class _FailingHandle(_Handle):
        def terminate(self):
            self.code = -1
            raise OSError("프로세스 종료 실패")

    # _running의 handle을 교체
    job_id, _, before = sched._running
    sched._running = (job_id, _FailingHandle(), before)
    assert sched.cancel(a.id)
    assert store.load(a.id).state == J.CANCELLED
    sched.tick()
    assert store.load(b.id).state == J.RUNNING


def test_stop_tolerates_office_cleanup_exception(tmp_path):
    """office.cleanup() 예외 후에도 stop()은 job을 INTERRUPTED로 표시."""
    store, launcher, _, sched = _setup(tmp_path)
    a = _submit(sched, 1)
    sched.tick()

    class _FailingOffice(_Office):
        def cleanup(self, before):
            raise RuntimeError("Office 정리 실패")

    sched.office = _FailingOffice()
    sched.stop()
    job = store.load(a.id)
    assert job.state == J.INTERRUPTED


def test_failure_with_office_cleanup_exception_marks_failed(tmp_path):
    """실패한 job에서 office.cleanup() 예외가 나도 job은 FAILED 상태로 정착."""
    store, launcher, _, sched = _setup(tmp_path)
    a, b = _submit(sched, 1), _submit(sched, 2)
    sched.tick()
    launcher.handles[a.id].code = 1  # 실패

    class _FailingOffice(_Office):
        def cleanup(self, before):
            raise RuntimeError("Office 정리 실패")

    sched.office = _FailingOffice()
    sched.tick()
    assert store.load(a.id).state == J.FAILED
    sched.tick()
    assert store.load(b.id).state == J.RUNNING


# --------------------------------------------------------------------------- #
# 서버가 강제로 죽은 뒤 — 살아남은 worker·Office 정리(runner.json)
class _Probe:
    def __init__(self, created=None, fail_kill=False):
        self.created = created
        self.fail_kill = fail_kill
        self.asked: list[int] = []
        self.killed: list[int] = []

    def creation_time(self, pid):
        self.asked.append(pid)
        return self.created

    def kill_tree(self, pid):
        self.killed.append(pid)
        if self.fail_kill:
            raise OSError("taskkill 실패")


class _PidHandle(_Handle):
    pid = 4321


RUNNING_ID = "20260929-140201-0001"


def _crashed(tmp_path, probe, runner=None):
    """서버가 죽은 뒤 상태: job.json 은 RUNNING, (선택) runner.json 이 남아 있다."""
    store = J.JobStore(tmp_path)
    store.save(J.Job(id=RUNNING_ID, engine="fact", state=J.RUNNING))
    if runner is not None:
        (store.dir(RUNNING_ID) / "runner.json").write_text(json.dumps(runner), encoding="utf-8")
    office = _Office()
    sched = JobScheduler(store, _Launcher(), office=office, probe=probe, clock=lambda: 50.0)
    return store, office, sched


def test_tick_records_runner_json_with_worker_pid(tmp_path):
    store = J.JobStore(tmp_path)
    probe = _Probe(created=111.25)
    office = _Office()
    sched = JobScheduler(store, lambda job, d: _PidHandle(), office=office, probe=probe)
    a = _submit(sched, 1)
    sched.tick()
    runner = json.loads((store.dir(a.id) / "runner.json").read_text(encoding="utf-8"))
    assert runner == {"pid": 4321, "created": 111.25, "before": [100]}
    assert not (store.dir(a.id) / "runner.json.tmp").exists()


def test_tick_without_pid_records_nothing(tmp_path):
    store, _, _, sched = _setup(tmp_path)
    a = _submit(sched, 1)
    sched.tick()
    assert store.load(a.id).state == J.RUNNING
    assert not (store.dir(a.id) / "runner.json").exists()


def test_recover_kills_surviving_worker_and_cleans_office(tmp_path):
    probe = _Probe(created=111.6)                  # 기록과 1초 이내 → 같은 프로세스
    store, office, sched = _crashed(
        tmp_path, probe, {"pid": 4321, "created": 111.0, "before": [100, 200]})
    assert sched.recover() == [RUNNING_ID]
    assert probe.killed == [4321]
    assert office.cleaned == [{100, 200}]
    assert store.load(RUNNING_ID).state == J.INTERRUPTED


def test_recover_skips_reused_pid(tmp_path):
    probe = _Probe(created=500.0)                  # 같은 PID 인데 생성 시각이 다르다 = 남의 프로세스
    store, office, sched = _crashed(
        tmp_path, probe, {"pid": 4321, "created": 111.0, "before": [100]})
    sched.recover()
    assert probe.killed == [] and office.cleaned == []
    assert store.load(RUNNING_ID).state == J.INTERRUPTED


def test_recover_skips_dead_worker(tmp_path):
    probe = _Probe(created=None)                   # 이미 끝난 프로세스
    store, office, sched = _crashed(
        tmp_path, probe, {"pid": 4321, "created": 111.0, "before": [100]})
    sched.recover()
    assert probe.killed == [] and office.cleaned == []
    assert store.load(RUNNING_ID).state == J.INTERRUPTED


def test_recover_never_kills_without_recorded_creation_time(tmp_path):
    probe = _Probe(created=111.0)
    store, office, sched = _crashed(
        tmp_path, probe, {"pid": 4321, "created": None, "before": [100]})
    sched.recover()
    assert probe.killed == [] and office.cleaned == []
    assert store.load(RUNNING_ID).state == J.INTERRUPTED


def test_recover_without_runner_json_behaves_as_before(tmp_path):
    probe = _Probe(created=111.0)
    store, office, sched = _crashed(tmp_path, probe)
    assert sched.recover() == [RUNNING_ID]
    assert probe.asked == [] and office.cleaned == []
    assert store.load(RUNNING_ID).state == J.INTERRUPTED


def test_recover_tolerates_kill_failure_and_broken_runner_json(tmp_path):
    probe = _Probe(created=111.0, fail_kill=True)
    store, office, sched = _crashed(
        tmp_path, probe, {"pid": 4321, "created": 111.0, "before": [100]})
    sched.recover()
    assert office.cleaned == [{100}]               # 종료가 실패해도 Office 정리는 계속한다
    assert store.load(RUNNING_ID).state == J.INTERRUPTED

    store2, office2, sched2 = _crashed(tmp_path / "b", _Probe(created=1.0))
    (store2.dir(RUNNING_ID) / "runner.json").write_text("{깨짐", encoding="utf-8")
    sched2.recover()
    assert store2.load(RUNNING_ID).state == J.INTERRUPTED
