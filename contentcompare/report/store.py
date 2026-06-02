"""생성된 리포트(.md)를 저장/조회하는 유틸.

CLI/Streamlit 이 비교를 끝내면 리포트를 ``reports/`` 에 타임스탬프 파일로 남긴다.
Streamlit 의 '리포트 보기'는 이 디렉터리를 읽어 최신 리포트를 자동으로 보여준다(요청 2번).
"""

from __future__ import annotations

import os
from datetime import datetime

DEFAULT_REPORT_DIR = "reports"


def reports_dir(base: str = DEFAULT_REPORT_DIR) -> str:
    os.makedirs(base, exist_ok=True)
    return base


def save_report(markdown: str, *, base: str = DEFAULT_REPORT_DIR, name: str | None = None) -> str:
    """리포트 markdown 을 파일로 저장하고 경로를 반환한다.

    ``name`` 을 주지 않으면 ``report_<시각>.md`` 로 저장한다.
    """
    directory = reports_dir(base)
    fname = name or f"report_{datetime.now():%Y%m%d_%H%M%S}.md"
    if not fname.endswith(".md"):
        fname += ".md"
    path = os.path.join(directory, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(markdown)
    return path


def list_reports(base: str = DEFAULT_REPORT_DIR) -> list[str]:
    """저장된 리포트 경로를 최신순으로 반환(없으면 빈 리스트)."""
    if not os.path.isdir(base):
        return []
    paths = [
        os.path.join(base, n) for n in os.listdir(base) if n.lower().endswith(".md")
    ]
    return sorted(paths, key=lambda p: os.path.getmtime(p), reverse=True)


def read_report(path: str) -> str:
    """리포트 파일 내용을 읽어 반환(없으면 빈 문자열)."""
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""
