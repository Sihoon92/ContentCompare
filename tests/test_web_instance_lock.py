"""한 jobs 폴더에 서버 하나 — 두 번째 서버는 작업을 건드리기 전에 기동을 멈춘다."""

from __future__ import annotations

import pytest

from contentcompare.web import jobs as J
from contentcompare.web.instance_lock import InstanceLock, acquire_instance_lock


def test_second_acquire_on_the_same_path_fails_until_released(tmp_path):
    path = tmp_path / "jobs" / ".server.lock"
    first = acquire_instance_lock(path)
    try:
        with pytest.raises(RuntimeError, match="이미 같은 jobs 폴더로 실행 중인 서버가 있습니다"):
            acquire_instance_lock(path)
    finally:
        first.release()
    again = acquire_instance_lock(path)            # 놓으면 다시 잡힌다
    again.release()
    again.release()                                # 두 번 놓아도 괜찮다


def test_lock_is_an_instance_lock(tmp_path):
    lock = acquire_instance_lock(tmp_path / ".server.lock")
    try:
        assert isinstance(lock, InstanceLock) and lock.path == tmp_path / ".server.lock"
    finally:
        lock.release()


def test_second_server_on_the_same_jobs_folder_aborts_startup(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("python_multipart")
    from fastapi.testclient import TestClient

    from contentcompare.web.admin import AdminAuth
    from contentcompare.web.app import create_app
    from contentcompare.web.scheduler import JobScheduler
    from contentcompare.web.settings import WebSettings

    settings = WebSettings(jobs_dir=str(tmp_path / "jobs"))

    def make():
        store = J.JobStore(settings.jobs_dir)
        return create_app(settings, store=store,
                          scheduler=JobScheduler(store, lambda job, d: None, poll_interval=0.05),
                          checker=object(), auth=AdminAuth(""), background=True,
                          static_dir=tmp_path / "없는dist")

    store = J.JobStore(settings.jobs_dir)
    with TestClient(make()):
        # 첫 서버가 돌리는 중인 작업 — 두 번째 서버의 recover() 가 건드리면 안 된다.
        store.save(J.Job(id="20260929-140211-a3f9", engine="fact", state=J.RUNNING))
        with pytest.raises(RuntimeError, match="이미 같은 jobs 폴더로 실행 중인 서버가 있습니다"):
            with TestClient(make()):
                pass
        assert store.load("20260929-140211-a3f9").state == J.RUNNING
    with TestClient(make()):                       # 첫 서버가 끝나면 다시 뜬다
        pass
