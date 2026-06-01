"""실행 로그를 파일로 저장하는 유틸.

Streamlit/CLI 가 시작 시 한 번 :func:`setup_logging` 을 부르면, 모든 모듈의
로그(readers 의 COM 단계 포함)가 ``logs/contentcompare_<시각>.log`` 에 쌓인다.
Streamlit 의 잦은 rerun 에서도 핸들러가 중복되지 않도록 멱등적으로 동작한다.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

_HANDLER: logging.Handler | None = None
_PATH: str | None = None


def setup_logging(log_dir: str = "logs", level: int = logging.INFO, *, force_new: bool = False) -> str:
    """루트 로거에 파일 핸들러를 붙이고 로그 파일 경로를 반환한다(멱등적)."""
    global _HANDLER, _PATH
    if _HANDLER is not None and not force_new:
        return _PATH or ""

    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, f"contentcompare_{datetime.now():%Y%m%d_%H%M%S}.log")
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(level)
    if _HANDLER is not None:
        root.removeHandler(_HANDLER)
    root.addHandler(handler)
    _HANDLER, _PATH = handler, path
    logging.getLogger("contentcompare").info("로그 시작 → %s", path)
    return path


def current_log_path() -> str:
    return _PATH or ""


def read_log_text(max_chars: int = 20000) -> str:
    """현재 로그 파일의 끝부분을 읽어 반환(없으면 빈 문자열)."""
    if not _PATH or not os.path.exists(_PATH):
        return ""
    try:
        with open(_PATH, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    return text[-max_chars:]
