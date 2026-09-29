"""worker — 파이프라인을 CLI 처럼 부르고 결과를 작업 폴더에 파일로 남긴다.

로깅·타임라인은 프로세스 전역이라 여기서는 가짜로 막는다(실제 프로세스 격리는
test_web_launcher.py 의 통합 테스트가 본다).
"""

from __future__ import annotations

import json

import pytest

from contentcompare import progress as prog
from contentcompare import timeline
from contentcompare.fact.pipeline import FactRunResult
from contentcompare.web import worker
from contentcompare.web.jobs import Job, JobStore
from contentcompare.web.settings import WebSettings


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(worker, "setup_console", lambda **kw: None)
    monkeypatch.setattr(worker, "setup_logging", lambda **kw: "")
    yield
    timeline.reset_timeline()
    prog.reset_reporter()


def _job_dir(tmp_path, engine="fact"):
    store = JobStore(tmp_path / "jobs")
    job = Job(id="20260929-140211-a3f9", engine=engine,
              reference="reference/기준.xlsx", targets=["targets/자료/a.docx"])
    store.save(job)
    return store.dir(job.id)


class _FakeFact:
    def __init__(self, markdown="# 리포트"):
        self.markdown = markdown
        self.seen = None

    def run(self, reference, targets):
        self.seen = (reference, targets)
        prog.plan([prog.Unit("doc:0", "기준.xlsx", prog.DOC)])
        return FactRunResult(
            summaries=[{"path": reference, "status": "ok"}],
            comparisons=[{"result": "match", "entity_name": "충전온도",
                          "target_doc": "a.docx", "reason": "일치"}],
            markdown=self.markdown)


def test_fact_job_writes_result_report_and_progress(tmp_path):
    job_dir = _job_dir(tmp_path)
    fake = _FakeFact()
    code = worker.run_job(job_dir, WebSettings(), factory=lambda config, engine: fake)
    assert code == 0
    ref, targets = fake.seen
    assert ref.endswith("inputs\\reference\\기준.xlsx") or ref.endswith("inputs/reference/기준.xlsx")
    assert targets[0].replace("\\", "/").endswith("inputs/targets/자료/a.docx")
    payload = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
    assert payload["engine"] == "fact" and payload["summary"]
    assert (job_dir / "report.md").read_text(encoding="utf-8") == "# 리포트"
    assert prog.load_events(job_dir / "progress.jsonl")[0]["ev"] == "plan"
    assert not (job_dir / "error.json").exists()


def test_worker_points_artifacts_into_the_job_folder(tmp_path):
    job_dir = _job_dir(tmp_path)
    seen = {}

    def factory(config, engine):
        seen["artifacts"] = config.fact.artifacts_dir
        seen["report"] = config.report.output_dir
        seen["engine"] = engine
        return _FakeFact()

    worker.run_job(job_dir, WebSettings(), factory=factory)
    assert seen == {"artifacts": str(job_dir / "artifacts"), "report": str(job_dir),
                    "engine": "fact"}


def test_fact_without_report_is_a_failure(tmp_path):
    job_dir = _job_dir(tmp_path)
    code = worker.run_job(job_dir, WebSettings(),
                          factory=lambda config, engine: _FakeFact(markdown=""))
    assert code == 1
    assert "리포트" in json.loads((job_dir / "error.json").read_text(encoding="utf-8"))["error"]
    assert (job_dir / "result.json").is_file()   # 실패한 문서 목록은 보여 줘야 한다


def test_pipeline_exception_is_recorded(tmp_path):
    job_dir = _job_dir(tmp_path)

    def boom(config, engine):
        raise RuntimeError("설정 폭발")

    assert worker.run_job(job_dir, WebSettings(), factory=boom) == 1
    error = json.loads((job_dir / "error.json").read_text(encoding="utf-8"))["error"]
    assert error == "RuntimeError: 설정 폭발"


def test_rag_job_renders_markdown(tmp_path):
    job_dir = _job_dir(tmp_path, engine="rag")

    class _FakeRag:
        def run(self, reference, targets):
            return []

    assert worker.run_job(job_dir, WebSettings(), factory=lambda c, e: _FakeRag()) == 0
    assert json.loads((job_dir / "result.json").read_text(encoding="utf-8"))["engine"] == "rag"
    assert "기준.xlsx" in (job_dir / "report.md").read_text(encoding="utf-8")


def test_main_requires_exactly_one_argument():
    assert worker.main([]) == 2


def test_setup_phase_exception_writes_error_json(tmp_path, monkeypatch):
    """Setup failures (build_app_config, logging, etc.) must write error.json."""
    job_dir = _job_dir(tmp_path)
    def boom(*args, **kwargs):
        raise FileNotFoundError("config/config.yaml")
    monkeypatch.setattr(worker, "build_app_config", boom)
    code = worker.run_job(job_dir, WebSettings())
    assert code == 1
    error = json.loads((job_dir / "error.json").read_text(encoding="utf-8"))["error"]
    assert error == "FileNotFoundError: config/config.yaml"
