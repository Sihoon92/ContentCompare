"""서버 종료 — SSE 가 열려 있어도 Ctrl+C 한 번으로 끝나고 작업은 ``interrupted`` 가 된다.

uvicorn 은 기본(``timeout_graceful_shutdown=None``)이면 열린 연결이 닫힐 때까지 영원히 기다린다.
SSE 는 작업이 끝나야 닫히므로 서버가 안 꺼지고, 사람이 Ctrl+C 를 두 번 누르면 lifespan 종료를
건너뛰어 worker 가 살아남는다. 실제 서버를 띄워 그 경로를 확인한다.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("python_multipart")
httpx = pytest.importorskip("httpx")
uvicorn = pytest.importorskip("uvicorn")

from contentcompare.web import __main__ as web_main  # noqa: E402
from contentcompare.web import jobs as J  # noqa: E402
from contentcompare.web.admin import AdminAuth  # noqa: E402
from contentcompare.web.app import create_app  # noqa: E402
from contentcompare.web.scheduler import JobScheduler  # noqa: E402
from contentcompare.web.settings import WebSettings  # noqa: E402


class _NeverEnds:
    def __init__(self):
        self.terminated = threading.Event()

    def poll(self):
        return None

    def terminate(self):
        self.terminated.set()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(cond, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def test_shutdown_with_open_sse_interrupts_the_running_job(tmp_path):
    settings = WebSettings(jobs_dir=str(tmp_path / "jobs"))
    store = J.JobStore(settings.jobs_dir)
    handle = _NeverEnds()
    scheduler = JobScheduler(store, lambda job, d: handle, poll_interval=0.05)
    job = scheduler.submit(J.Job(id="20260929-140211-a3f9", engine="fact"))
    app = create_app(settings, store=store, scheduler=scheduler, checker=object(),
                     auth=AdminAuth(""), background=True, static_dir=tmp_path / "없는dist")

    port = _free_port()
    options = {**web_main.UVICORN_OPTIONS, "host": "127.0.0.1", "port": port, "ws": "none"}
    server = uvicorn.Server(uvicorn.Config(app, **options))
    server_thread = threading.Thread(target=server.run, daemon=True)
    connected = threading.Event()
    client = httpx.Client(timeout=httpx.Timeout(30.0))

    def listen():
        try:
            with client.stream("GET", f"http://127.0.0.1:{port}/api/jobs/{job.id}/events") as res:
                for line in res.iter_lines():
                    if line.startswith("event:"):
                        connected.set()
        except Exception:  # noqa: BLE001 — 서버가 연결을 끊으면 예외로 끝난다(정상)
            pass

    listener = threading.Thread(target=listen, daemon=True)
    server_thread.start()
    try:
        assert _wait(lambda: server.started, 15), "서버가 뜨지 않았다"
        assert _wait(lambda: (store.load(job.id) or job).state == J.RUNNING, 10)
        listener.start()
        assert connected.wait(10), "SSE 가 연결되지 않았다"

        server.should_exit = True                  # Ctrl+C 한 번과 같다
        server_thread.join(15)

        assert not server_thread.is_alive(), "SSE 가 열려 있으면 서버가 꺼지지 않는다"
        assert handle.terminated.is_set()
        assert store.load(job.id).state == J.INTERRUPTED
    finally:
        server.should_exit = True
        server.force_exit = True
        server_thread.join(10)
        scheduler.stop()                           # force_exit 는 lifespan 종료를 건너뛴다
        client.close()
        listener.join(5)
