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


def setup_logging(log_dir: str = "logs", level: int = logging.DEBUG, *, force_new: bool = False) -> str:
    """루트 로거에 파일 핸들러를 붙이고 로그 파일 경로를 반환한다(멱등적).

    파일에는 기본적으로 DEBUG 까지(프롬프트·LLM 원문 응답·HTTP 요청 포함) 모두 남긴다.
    화면(콘솔)에 무엇을 보일지는 호출 측의 ``basicConfig`` 등 별도 핸들러가 정한다.
    """
    global _HANDLER, _PATH
    if _HANDLER is not None and not force_new:
        return _PATH or ""

    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, f"contentcompare_{datetime.now():%Y%m%d_%H%M%S}.log")
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    # 루트 레벨은 파일 핸들러가 원하는 만큼(가장 상세하게) 받도록 낮춰 둔다.
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    if _HANDLER is not None:
        root.removeHandler(_HANDLER)
    root.addHandler(handler)
    _HANDLER, _PATH = handler, path
    logging.getLogger("contentcompare").info("로그 시작 → %s", path)
    return path


def current_log_path() -> str:
    return _PATH or ""


def log_print(*args, level: int = logging.INFO, logger_name: str = "contentcompare", **kwargs) -> None:
    """``print`` 처럼 화면에 출력하면서 동시에 같은 내용을 로그 파일에도 남긴다.

    CLI/스크립트에서 ``print(...)`` 대신 쓰면, 사용자가 화면에서 본 모든 메시지가
    로그 파일에도 빠짐없이 기록된다(요청 4번). ``print`` 와 동일한 시그니처를 받되,
    파일에는 한 줄(메시지)로 합쳐 기록한다.
    """
    sep = kwargs.get("sep", " ")
    message = sep.join(str(a) for a in args)
    print(*args, **kwargs)
    logging.getLogger(logger_name).log(level, message)


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
