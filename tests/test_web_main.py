"""start.bat dev 가 쓰는 dev_app 팩토리 — .env 경로를 worker 에 물려주고 앱을 만든다."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("fastapi")
from contentcompare.web import __main__ as web_main  # noqa: E402


def test_dev_app_reads_env_file_and_passes_it_down(tmp_path, monkeypatch):
    pytest.importorskip("dotenv")
    env_file = tmp_path / "dev.env"
    env_file.write_text(f"CC_JOBS_DIR={tmp_path / 'jobs'}\n", encoding="utf-8")
    monkeypatch.setenv(web_main.DOTENV_ENV, str(env_file))
    seen = {}

    def fake_logging(logs_dir):
        seen["logs_dir"] = logs_dir
        return logs_dir / "server.log"

    def fake_create_app(settings):
        seen["settings"] = settings
        return "APP"

    monkeypatch.setattr(web_main, "configure_server_logging", fake_logging)
    monkeypatch.setattr(web_main, "create_app", fake_create_app)

    assert web_main.dev_app() == "APP"
    assert seen["settings"].jobs_dir == str(tmp_path / "jobs")
    assert os.environ[web_main.DOTENV_ENV] == str(env_file.resolve())
