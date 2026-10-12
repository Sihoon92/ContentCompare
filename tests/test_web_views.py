"""조회 API — 목록에 있는 것만 연다, 지식 저장은 충돌을 알린다."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("python_multipart")
from fastapi.testclient import TestClient  # noqa: E402

from contentcompare.config import AppConfig  # noqa: E402
from contentcompare.web import app as web_app  # noqa: E402
from contentcompare.web import jobs as J  # noqa: E402
from contentcompare.web.admin import AdminAuth  # noqa: E402
from contentcompare.web.scheduler import JobScheduler  # noqa: E402
from contentcompare.web.settings import WebSettings  # noqa: E402

JOB_ID = "20260929-140211-a3f9"


def _app(tmp_path):
    settings = WebSettings(jobs_dir=str(tmp_path / "jobs"))
    config = AppConfig()
    config.report.output_dir = str(tmp_path / "reports")
    config.fact.artifacts_dir = str(tmp_path / "artifacts")
    config.knowledge.dir = str(tmp_path / "knowledge")
    store = J.JobStore(settings.jobs_dir)
    store.save(J.Job(id=JOB_ID, engine="fact", requester="홍길동", state=J.SUCCEEDED))
    app = web_app.create_app(settings, config=config, store=store,
                             scheduler=JobScheduler(store, lambda j, d: None),
                             checker=object(), auth=AdminAuth("pw"), background=False,
                             static_dir=tmp_path / "없는dist")
    return TestClient(app), store, config


def test_reports_list_job_and_legacy_and_read(tmp_path):
    client, store, config = _app(tmp_path)
    (store.dir(JOB_ID) / "report.md").write_text("# 작업 리포트", encoding="utf-8")
    legacy = tmp_path / "reports"
    legacy.mkdir()
    (legacy / "report_old.md").write_text("# 옛 리포트", encoding="utf-8")
    ids = [r["id"] for r in client.get("/api/reports").json()["reports"]]
    assert set(ids) == {f"job/{JOB_ID}", "reports/report_old.md"}
    assert client.get("/api/reports/content", params={"id": f"job/{JOB_ID}"}).json()[
        "markdown"] == "# 작업 리포트"
    for bad in ("reports/../x.md", "job/../../x", "reports/없음.md", "etc/passwd"):
        assert client.get("/api/reports/content", params={"id": bad}).status_code == 404


def _make_fact_run(root):
    """fact 파이프라인 스모크로 실제 산출물(comparison_result.json 포함)을 만든다."""
    from test_fact_pipeline_smoke import _excel_or_ppt, _pipe, _ppt_chat
    _pipe(root, extractor=_excel_or_ppt, chat=_ppt_chat()).run("기준.xlsx", ["발표.pptx"])


def test_micro_runs_options_and_html(tmp_path):
    client, store, _ = _app(tmp_path)
    _make_fact_run(store.dir(JOB_ID) / "artifacts")
    runs = client.get("/api/micro/runs").json()["runs"]
    run_id = next(r["id"] for r in runs if r["id"].startswith(f"job/{JOB_ID}/"))
    options = client.get("/api/micro/options", params={"run": run_id}).json()
    assert options["reference_doc"] == "기준.xlsx"
    assert options["target_docs"] == ["발표.pptx"]
    assert options["default_results"] == ["mismatch", "unknown", "missing"]
    debug = client.get("/api/micro/html", params={
        "run": run_id, "mode": "debug", "results": "mismatch,unknown,missing"}).json()
    assert "<" in debug["html"] and debug["height"] > 0
    learn = client.get("/api/micro/html", params={
        "run": run_id, "mode": "learn", "doc": "기준.xlsx"}).json()
    assert learn["html"] or learn["unavailable"]
    assert client.get("/api/micro/options", params={"run": "artifacts/../x"}).status_code == 404


def test_timelines_list_and_render(tmp_path):
    client, store, _ = _app(tmp_path)
    tl = store.dir(JOB_ID) / "artifacts" / "_timeline"
    tl.mkdir(parents=True)
    (tl / "timeline.jsonl").write_text(
        json.dumps({"ts": 1.0, "kind": "stage_start", "name": "F2 records · 기준.xlsx"}) + "\n",
        encoding="utf-8")
    items = client.get("/api/timelines").json()["timelines"]
    assert items[0]["id"] == f"job/{JOB_ID}/timeline"
    html = client.get("/api/timelines/html",
                      params={"run": items[0]["id"], "errors_only": "false"}).json()["html"]
    assert "F2 records" in html
    assert client.get("/api/timelines/html", params={"run": "legacy/없음"}).status_code == 404


def test_knowledge_create_read_conflict_update(tmp_path):
    client, _, _ = _app(tmp_path)
    listing = client.get("/api/knowledge/files").json()
    assert listing["files"] == [] and listing["template"]
    created = client.put("/api/knowledge/files/용어.md",
                         json={"content": "formation = 화성", "base_mtime": None}).json()
    got = client.get("/api/knowledge/files/용어.md").json()
    assert got["content"] == "formation = 화성" and got["mtime"] == created["mtime"]
    stale = client.put("/api/knowledge/files/용어.md",
                       json={"content": "덮어쓰기", "base_mtime": created["mtime"] - 100})
    assert stale.status_code == 409
    assert stale.json()["detail"]["current"]["content"] == "formation = 화성"
    ok = client.put("/api/knowledge/files/용어.md",
                    json={"content": "formation = 배터리 화성 공정",
                          "base_mtime": created["mtime"]})
    assert ok.status_code == 200
    assert "배터리 화성 공정" in client.get("/api/knowledge/merged").json()["text"]


def test_knowledge_new_file_with_existing_name_conflicts(tmp_path):
    client, _, _ = _app(tmp_path)
    client.put("/api/knowledge/files/a.md", json={"content": "1", "base_mtime": None})
    assert client.put("/api/knowledge/files/a.md",
                      json={"content": "2", "base_mtime": None}).status_code == 409


def test_knowledge_name_gets_md_and_rejects_paths(tmp_path):
    client, _, _ = _app(tmp_path)
    saved = client.put("/api/knowledge/files/노트", json={"content": "x", "base_mtime": None})
    assert saved.json()["name"] == "노트.md"
    assert client.get("/api/knowledge/files/..%2Fx.md").status_code in (400, 404)
    assert client.get("/api/knowledge/files/.hidden.md").status_code == 400


@pytest.mark.parametrize("bad", ["a.md:evil", "CON", "a*b", "a<b"])
def test_knowledge_rejects_windows_special_names_without_touching_disk(tmp_path, bad):
    client, _, config = _app(tmp_path)
    before = client.get("/api/knowledge/files").json()["files"]
    resp = client.put(f"/api/knowledge/files/{bad}", json={"content": "x", "base_mtime": None})
    assert resp.status_code == 400
    assert client.get("/api/knowledge/files").json()["files"] == before
    kdir = Path(config.knowledge.dir)
    assert not kdir.exists() or list(kdir.iterdir()) == []
    assert client.get(f"/api/knowledge/files/{bad}").status_code == 400
