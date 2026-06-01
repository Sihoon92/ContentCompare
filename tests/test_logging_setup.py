"""로그 파일 저장 유틸 테스트."""

from __future__ import annotations

import logging
import os

from contentcompare import logging_setup


def test_setup_creates_file_and_captures_logs(tmp_path):
    path = logging_setup.setup_logging(str(tmp_path), force_new=True)
    assert os.path.exists(path)
    logging.getLogger("contentcompare.test").info("테스트 메시지 ABC")
    for h in logging.getLogger().handlers:
        h.flush()
    text = logging_setup.read_log_text()
    assert "테스트 메시지 ABC" in text


def test_setup_is_idempotent(tmp_path):
    p1 = logging_setup.setup_logging(str(tmp_path), force_new=True)
    p2 = logging_setup.setup_logging(str(tmp_path))  # force_new 아님 → 같은 경로
    assert p1 == p2


def test_force_new_rotates_file(tmp_path):
    import time

    p1 = logging_setup.setup_logging(str(tmp_path), force_new=True)
    time.sleep(1.1)  # 파일명이 초 단위라 1초 이상 대기
    p2 = logging_setup.setup_logging(str(tmp_path), force_new=True)
    assert p1 != p2
