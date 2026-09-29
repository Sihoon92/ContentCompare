"""worker 프로세스 — 실제 서브프로세스로 돌려 console.log·결과 파일·종료를 본다."""

from __future__ import annotations

import os
import time
from pathlib import Path

from contentcompare.web import launcher as L
from contentcompare.web.jobs import Job, JobStore

TESTS_DIR = str(Path(__file__).resolve().parent)


def _job(tmp_path):
    store = JobStore(tmp_path / "jobs")
    job = Job(id="20260929-140211-a3f9", engine="fact",
              reference="reference/기준.xlsx", targets=["targets/a.docx"])
    store.save(job)
    return job, store.dir(job.id)


def _launch(tmp_path, factory):
    job, job_dir = _job(tmp_path)
    env = {L.FACTORY_ENV: f"web_fake_factory:{factory}",
           "PYTHONPATH": TESTS_DIR + os.pathsep + os.environ.get("PYTHONPATH", "")}
    # cwd 를 비운 폴더로 — 저장소의 .env·knowledge 를 읽지 않게.
    handle = L.process_launcher(extra_env=env, cwd=str(tmp_path))(job, job_dir)
    return handle, job_dir


def _wait(handle, timeout=90.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        code = handle.poll()
        if code is not None:
            return code
        time.sleep(0.2)
    raise AssertionError("worker 가 끝나지 않았다")


def test_worker_process_writes_console_log_and_results(tmp_path):
    handle, job_dir = _launch(tmp_path, "make")
    assert _wait(handle) == 0
    console = (job_dir / "console.log").read_text(encoding="utf-8")
    assert "가짜 파이프라인 실행 중" in console
    assert (job_dir / "result.json").is_file()
    assert (job_dir / "report.md").read_text(encoding="utf-8") == "# 가짜 리포트"
    assert (job_dir / "progress.jsonl").is_file()


def test_terminate_stops_a_running_worker(tmp_path):
    handle, job_dir = _launch(tmp_path, "make_slow")
    deadline = time.time() + 60
    while not (job_dir / "progress.jsonl").exists() and time.time() < deadline:
        time.sleep(0.2)
    handle.terminate()
    assert handle.poll() is not None
    assert not (job_dir / "result.json").exists()


def test_parse_tasklist_csv_picks_office_images_only():
    text = ('"EXCEL.EXE","1234","Console","1","50,000 K"\n'
            '"notepad.exe","99","Console","1","1 K"\n'
            '"POWERPNT.EXE","5678","Console","1","9 K"\n'
            '"WINWORD.EXE","abc","Console","1","9 K"\n')
    assert L.parse_tasklist_csv(text) == {1234, 5678}


def test_windows_office_guard_kills_only_new_processes():
    calls = []

    class _Result:
        def __init__(self, stdout=""):
            self.stdout = stdout

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "tasklist":
            return _Result('"EXCEL.EXE","10","C","1","1 K"\n"POWERPNT.EXE","20","C","1","1 K"\n')
        return _Result()

    guard = L.WindowsOfficeGuard(run=run)
    assert guard.snapshot() == {10, 20}
    assert guard.cleanup({10}) == [20]
    assert ["taskkill", "/PID", "20", "/F"] in calls
    assert not any(c[:3] == ["taskkill", "/PID", "10"] for c in calls)


def test_office_guard_survives_missing_tasklist():
    def run(cmd, **kw):
        raise OSError("tasklist 없음")

    guard = L.WindowsOfficeGuard(run=run)
    assert guard.snapshot() == set()
    assert guard.cleanup(set()) == []
