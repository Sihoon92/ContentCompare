"""리포트 저장/조회 유틸 테스트(요청 2번 — Streamlit '리포트 보기')."""

from __future__ import annotations

import os
import time

from contentcompare.report import list_reports, read_report, save_report


def test_save_and_read_report(tmp_path):
    base = str(tmp_path / "reports")
    path = save_report("# 리포트\n내용", base=base, name="r1")
    assert path.endswith("r1.md")
    assert read_report(path) == "# 리포트\n내용"


def test_list_reports_newest_first(tmp_path):
    base = str(tmp_path / "reports")
    p1 = save_report("a", base=base, name="old")
    time.sleep(0.01)
    p2 = save_report("b", base=base, name="new")
    # mtime 으로 최신순 정렬되도록 두 번째를 더 최근으로 만든다.
    os.utime(p2, (time.time() + 10, time.time() + 10))
    listed = list_reports(base)
    assert listed[0] == p2 and p1 in listed


def test_list_reports_missing_dir_empty():
    assert list_reports("/nope/does/not/exist") == []


def test_read_report_missing_returns_blank():
    assert read_report("/nope/x.md") == ""
