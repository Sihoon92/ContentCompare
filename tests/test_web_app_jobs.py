"""작업 API — 업로드·대기열·내 작업·취소 권한·SSE·결과."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("python_multipart")
from fastapi.testclient import TestClient  # noqa: E402

from contentcompare.web import app as web_app  # noqa: E402
from contentcompare.web import jobs as J  # noqa: E402
from contentcompare.web.admin import AdminAuth  # noqa: E402
from contentcompare.web.scheduler import JobScheduler  # noqa: E402
from contentcompare.web.settings import WebSettings  # noqa: E402


class _Checker:
    def __init__(self):
        self.calls = 0

    def run(self):
        self.calls += 1
        return {"ok": True, "results": [], "cached": self.calls > 1}


def _app(tmp_path, **settings_kw):
    settings = WebSettings(jobs_dir=str(tmp_path / "jobs"), **settings_kw)
    store = J.JobStore(settings.jobs_dir)
    scheduler = JobScheduler(store, lambda job, d: None)
    app = web_app.create_app(settings, store=store, scheduler=scheduler, checker=_Checker(),
                             auth=AdminAuth("pw"), background=False,
                             static_dir=tmp_path / "없는dist")
    return app, store


def _files(ref=("ref.xlsx", b"R"), targets=(("a.docx", b"A"),)):
    files = [("reference", (ref[0], ref[1], "application/octet-stream"))]
    files += [("targets", (name, data, "application/octet-stream")) for name, data in targets]
    return files


def _submit(client, **kw):
    data = {"engine": "fact", "requester": "홍길동", **kw.pop("data", {})}
    return client.post("/api/jobs", data=data, files=kw.pop("files", _files()))


def test_client_cookie_is_issued_once(tmp_path):
    app, _ = _app(tmp_path)
    client = TestClient(app)
    client.get("/api/jobs")
    first = client.cookies.get(web_app.CLIENT_COOKIE)
    client.get("/api/jobs")
    assert first and client.cookies.get(web_app.CLIENT_COOKIE) == first


def test_llm_check_goes_through_the_checker(tmp_path):
    app, _ = _app(tmp_path)
    client = TestClient(app)
    assert client.post("/api/llm/check").json() == {"ok": True, "results": [], "cached": False}
    assert client.post("/api/llm/check").json()["cached"] is True


def test_llm_checker_caches_and_serializes(tmp_path):
    calls = []

    class _R:
        name, ok, detail = "chat", True, "ok"

        def line(self):
            return "✅ chat: ok"

    clock = [0.0]
    checker = web_app.LlmChecker(lambda: calls.append(1) or [_R()], clock=lambda: clock[0])
    first = checker.run()
    assert first["ok"] and first["results"][0]["line"] == "✅ chat: ok" and not first["cached"]
    assert checker.run()["cached"] and len(calls) == 1
    clock[0] = 31.0
    assert not checker.run()["cached"] and len(calls) == 2


def test_submit_saves_files_and_queues(tmp_path):
    app, store = _app(tmp_path)
    client = TestClient(app)
    res = _submit(client, files=_files(targets=(("a.docx", b"A"), ("~$a.docx", b"L"))))
    assert res.status_code == 200, res.text
    body = res.json()
    job = store.load(body["job"]["id"])
    assert job.state == J.QUEUED and job.requester == "홍길동"
    assert job.reference == "reference/ref.xlsx" and job.targets == ["targets/a.docx"]
    assert (store.dir(job.id) / "inputs" / "targets" / "a.docx").read_bytes() == b"A"
    assert body["skipped"] == ["~$a.docx"]
    assert body["job"]["mine"] is True and "client_id" not in body["job"]


def test_submit_uses_explicit_relative_paths_for_korean_names(tmp_path):
    app, store = _app(tmp_path)
    client = TestClient(app)
    res = _submit(client, data={"reference_path": "기준.xlsx",
                                "target_paths": ["자료/규격서 v2.docx"]},
                  files=_files(ref=("x.xlsx", b"R"), targets=(("y.docx", b"A"),)))
    assert res.status_code == 200, res.text
    job = store.load(res.json()["job"]["id"])
    assert job.reference == "reference/기준.xlsx"
    assert job.targets == ["targets/자료/규격서 v2.docx"]
    assert (store.dir(job.id) / "inputs" / "targets" / "자료" / "규격서 v2.docx").is_file()


def test_path_count_mismatch_is_rejected(tmp_path):
    app, _ = _app(tmp_path)
    res = _submit(TestClient(app), data={"target_paths": ["a.docx", "b.docx"]})
    assert res.status_code == 400


@pytest.mark.parametrize("data, files, fragment", [
    ({"engine": "gpt"}, None, "엔진"),
    ({}, _files(ref=("ref.docx", b"R")), "Excel"),
    ({}, _files(targets=(("a/규격.docx", b"1"), ("b/규격.docx", b"2"))), "같은 이름"),
])
def test_bad_submissions_leave_nothing_behind(tmp_path, data, files, fragment):
    app, store = _app(tmp_path)
    kw = {"data": data}
    if files:
        kw["files"] = files
    res = _submit(TestClient(app), **kw)
    assert res.status_code == 400 and fragment in res.json()["detail"]
    assert store.list() == []
    assert not any((tmp_path / "jobs").glob("*")) if (tmp_path / "jobs").exists() else True


def test_oversized_upload_is_rejected_and_cleaned(tmp_path):
    app, store = _app(tmp_path, max_upload_mb=1)
    big = b"x" * (1024 * 1024 + 1)
    res = _submit(TestClient(app), files=_files(targets=(("a.docx", big),)))
    assert res.status_code == 400 and "큽니다" in res.json()["detail"]
    assert store.list() == []


def test_job_id_collision_is_retried(tmp_path, monkeypatch):
    app, store = _app(tmp_path)
    ids = iter(["20260929-140211-aaaa", "20260929-140211-aaaa", "20260929-140211-bbbb"])
    monkeypatch.setattr(web_app, "new_job_id", lambda: next(ids))
    client = TestClient(app)
    first = _submit(client).json()["job"]["id"]
    second = _submit(client).json()["job"]["id"]
    assert (first, second) == ("20260929-140211-aaaa", "20260929-140211-bbbb")


def test_list_marks_mine_and_positions(tmp_path):
    app, _ = _app(tmp_path)
    me, other = TestClient(app), TestClient(app)
    a = _submit(me).json()["job"]["id"]
    b = _submit(other).json()["job"]["id"]
    listing = me.get("/api/jobs").json()
    views = {j["id"]: j for j in listing["jobs"]}
    assert views[a]["mine"] and not views[b]["mine"]
    assert views[a]["position"] == 0 and views[b]["position"] == 1
    assert listing["running"] is None


def test_cancel_is_owner_or_admin_only(tmp_path):
    app, store = _app(tmp_path)
    owner, stranger = TestClient(app), TestClient(app)
    job_id = _submit(owner).json()["job"]["id"]
    assert stranger.post(f"/api/jobs/{job_id}/cancel").status_code == 403
    assert owner.post(f"/api/jobs/{job_id}/cancel").json()["job"]["state"] == J.CANCELLED
    assert owner.post(f"/api/jobs/{job_id}/cancel").status_code == 409


def test_get_job_includes_progress(tmp_path):
    app, store = _app(tmp_path)
    client = TestClient(app)
    job_id = _submit(client).json()["job"]["id"]
    (store.dir(job_id) / "progress.jsonl").write_text(
        json.dumps({"seq": 1, "ts": 1.0, "ev": "plan",
                    "units": [{"key": "doc:0", "label": "ref.xlsx", "kind": "doc"}]}) + "\n",
        encoding="utf-8")
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["progress"]["total"] == 1 and body["state"] == J.QUEUED


def test_unknown_or_malformed_job_is_404(tmp_path):
    app, _ = _app(tmp_path)
    client = TestClient(app)
    for bad in ("20260929-000000-ffff", "bad-id"):
        assert client.get(f"/api/jobs/{bad}").status_code == 404
        assert client.get(f"/api/jobs/{bad}/result").status_code == 404


def test_events_stream_for_a_finished_job(tmp_path):
    app, store = _app(tmp_path)
    client = TestClient(app)
    job_id = _submit(client).json()["job"]["id"]
    job = store.load(job_id)
    job.state = J.SUCCEEDED
    store.save(job)
    (store.dir(job_id) / "console.log").write_text("완료\n", encoding="utf-8")
    res = client.get(f"/api/jobs/{job_id}/events")
    assert res.headers["content-type"].startswith("text/event-stream")
    assert "event: log" in res.text and "event: end" in res.text


def test_result_and_report(tmp_path):
    app, store = _app(tmp_path)
    client = TestClient(app)
    job_id = _submit(client).json()["job"]["id"]
    assert client.get(f"/api/jobs/{job_id}/result").status_code == 404
    (store.dir(job_id) / "result.json").write_text('{"engine": "fact"}', encoding="utf-8")
    assert client.get(f"/api/jobs/{job_id}/result").json() == {
        "result": {"engine": "fact"}, "report": False}
    (store.dir(job_id) / "report.md").write_text("# 리포트", encoding="utf-8")
    res = client.get(f"/api/jobs/{job_id}/report")
    assert res.text == "# 리포트" and "attachment" in res.headers["content-disposition"]


def test_upload_paths_are_built_with_resolve_under(tmp_path, monkeypatch):
    app, store = _app(tmp_path)

    def _escape(base, rel):
        raise web_app.UploadError("경계 밖")

    monkeypatch.setattr(web_app, "resolve_under", _escape)
    res = _submit(TestClient(app))
    assert res.status_code == 400 and res.json()["detail"] == "경계 밖"
    assert store.list() == []
