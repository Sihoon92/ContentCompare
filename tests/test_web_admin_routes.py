"""관리자 API·정적 화면·접속 주소."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("python_multipart")
from fastapi.testclient import TestClient  # noqa: E402

from contentcompare.web import app as web_app  # noqa: E402
from contentcompare.web import jobs as J  # noqa: E402
from contentcompare.web.__main__ import access_urls  # noqa: E402
from contentcompare.web.admin import AdminAuth  # noqa: E402
from contentcompare.web.scheduler import JobScheduler  # noqa: E402
from contentcompare.web.settings import WebSettings  # noqa: E402

JOB_ID = "20260929-140211-a3f9"
LOG = ("2026-09-29 14:02:11,001 INFO contentcompare: 시작\n"
       "2026-09-29 14:02:12,002 DEBUG contentcompare.llm: 프롬프트 원문\n")


def _app(tmp_path, password="pw", static_dir=None):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "server.log").write_bytes(LOG.encode("utf-8"))
    settings = WebSettings(jobs_dir=str(tmp_path / "jobs"), logs_dir=str(logs),
                           admin_password=password)
    store = J.JobStore(settings.jobs_dir)
    store.save(J.Job(id=JOB_ID, engine="fact", client_id="someone-else-000000",
                     state=J.SUCCEEDED, finished_ts=1.0))
    (store.dir(JOB_ID) / "contentcompare_x.log").write_bytes(LOG.encode("utf-8"))
    app = web_app.create_app(settings, store=store,
                             scheduler=JobScheduler(store, lambda j, d: None),
                             checker=object(), auth=AdminAuth(password), background=False,
                             static_dir=static_dir or tmp_path / "없는dist")
    return TestClient(app), store


def _login(client):
    assert client.post("/api/admin/login", json={"password": "pw"}).status_code == 200


def test_status_and_disabled(tmp_path):
    client, _ = _app(tmp_path, password="")
    assert client.get("/api/admin/status").json() == {"enabled": False, "logged_in": False}
    assert client.post("/api/admin/login", json={"password": ""}).status_code == 403


def test_wrong_password_then_lockout(tmp_path):
    client, _ = _app(tmp_path)
    for _ in range(5):
        assert client.post("/api/admin/login", json={"password": "x"}).status_code == 401
    assert client.post("/api/admin/login", json={"password": "pw"}).status_code == 429


def test_logs_need_login(tmp_path):
    client, _ = _app(tmp_path)
    assert client.get("/api/admin/logs").status_code == 401
    assert client.get("/api/admin/logs/read", params={"source": "logs/server.log"}).status_code == 401


def test_logs_list_read_filter_download(tmp_path):
    client, _ = _app(tmp_path)
    _login(client)
    assert client.get("/api/admin/status").json()["logged_in"] is True
    ids = {s["id"] for s in client.get("/api/admin/logs").json()["sources"]}
    assert ids == {"logs/server.log", f"job/{JOB_ID}/contentcompare_x.log"}
    page = client.get("/api/admin/logs/read",
                      params={"source": f"job/{JOB_ID}/contentcompare_x.log",
                              "level": "INFO"}).json()
    assert [r["level"] for r in page["records"]] == ["INFO"] and page["total"] == 1
    everything = client.get("/api/admin/logs/read",
                            params={"source": "logs/server.log", "q": "프롬프트"}).json()
    assert everything["total"] == 1
    download = client.get("/api/admin/logs/download", params={"source": "logs/server.log"})
    assert download.text == LOG
    assert client.get("/api/admin/logs/read",
                      params={"source": "logs/../x.log"}).status_code == 404


def test_logout_revokes(tmp_path):
    client, _ = _app(tmp_path)
    _login(client)
    client.post("/api/admin/logout")
    assert client.get("/api/admin/logs").status_code == 401


def test_admin_can_cancel_someone_elses_job(tmp_path):
    client, store = _app(tmp_path)
    queued = J.Job(id="20260929-140300-0001", engine="fact", client_id="other-client-0000000")
    store.save(queued)
    assert client.post(f"/api/jobs/{queued.id}/cancel").status_code == 403
    _login(client)
    assert client.post(f"/api/jobs/{queued.id}/cancel").status_code == 200


def test_admin_jobs_list_and_delete(tmp_path):
    client, store = _app(tmp_path)
    _login(client)
    jobs = client.get("/api/admin/jobs").json()["jobs"]
    assert jobs[0]["id"] == JOB_ID and jobs[0]["client_id"] == "someone-else-000000"
    queued = J.Job(id="20260929-140300-0001", engine="fact")
    store.save(queued)
    assert client.delete(f"/api/admin/jobs/{queued.id}").status_code == 409
    assert client.delete(f"/api/admin/jobs/{JOB_ID}").status_code == 200
    assert store.load(JOB_ID) is None


def test_not_built_page_when_dist_is_missing(tmp_path):
    client, _ = _app(tmp_path)
    res = client.get("/")
    assert res.status_code == 200 and "setup.bat" in res.text


def test_spa_serving_and_api_404(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    client, _ = _app(tmp_path, static_dir=dist)
    assert client.get("/").text == "<div id=root></div>"
    assert client.get("/jobs/20260929-140211-a3f9").text == "<div id=root></div>"
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/api/없는경로").status_code == 404
    assert client.get("/..%2F..%2Fpyproject.toml").text == "<div id=root></div>"


def test_access_urls():
    assert access_urls("10.0.0.5", 8000) == ["http://10.0.0.5:8000"]
    urls = access_urls("0.0.0.0", 8123)
    assert urls[0] == "http://localhost:8123"
    assert all(u.endswith(":8123") for u in urls)
