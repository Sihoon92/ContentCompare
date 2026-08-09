"""실행 로그를 파일로 저장하는 유틸.

Streamlit/CLI 가 시작 시 한 번 :func:`setup_logging` 을 부르면, 모든 모듈의
로그(readers 의 COM 단계 포함)가 ``logs/contentcompare_<시각>.log`` 에 쌓인다.
Streamlit 의 잦은 rerun 에서도 핸들러가 중복되지 않도록 멱등적으로 동작한다.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Iterable

_HANDLER: logging.Handler | None = None
_PATH: str | None = None

#: 저수준 잡음을 쏟아내는 서드파티 로거들(접두어 매칭 — ``urllib3`` 은 하위
#: ``urllib3.connectionpool`` 까지 함께 조용해진다). 소켓 연결·재시도·폰트 캐시
#: 같은 내용이라 오판 추적에 쓸모가 없으면서 로그 파일의 대부분을 차지한다.
#: 우리 코드(``contentcompare.*``)의 DEBUG(프롬프트·LLM 원문 응답)는 건드리지 않는다.
NOISY_LOGGERS: tuple[str, ...] = (
    "urllib3",
    "httpcore",
    "httpx",
    "openai",
    "langchain",
    "langchain_core",
    "langchain_openai",
    "langfuse",
    "asyncio",
    "matplotlib",
    "PIL",
    "numexpr",
    "filelock",
    "fsspec",
    "charset_normalizer",
    "comtypes",
)

#: 이 환경변수를 참으로 두면 잡음 필터를 끄고 서드파티 로그까지 전부 남긴다
#: (HTTP 계층 자체를 의심할 때만 쓰는 탈출구).
NOISE_ENV = "CONTENTCOMPARE_LOG_NOISY"


def quiet_noisy_loggers(level: int = logging.WARNING, names: tuple[str, ...] = NOISY_LOGGERS) -> None:
    """서드파티 로거의 하한을 올려 저수준 잡음을 걸러낸다.

    로거 레벨을 올리는 방식이라 파일·콘솔 등 **모든 핸들러**에 동시에 적용된다.
    ``level`` 이상(기본 WARNING)은 그대로 통과하므로 진짜 오류는 잃지 않는다.
    ``logging.NOTSET`` 을 주면 필터를 걷어내고 다시 루트 레벨을 따르게 한다.
    """
    for name in names:
        logging.getLogger(name).setLevel(level)


def apply_logger_overrides(
    quiet: Iterable[str] = (),
    verbose: Iterable[str] = (),
    *,
    quiet_level: int = logging.WARNING,
    verbose_level: int = logging.DEBUG,
) -> None:
    """설정 파일이 지정한 조정을 기본 잡음 필터 **위에** 얹는다.

    :func:`setup_logging` 이 :data:`NOISY_LOGGERS` 로 큰 그림을 잡은 뒤, 프로젝트마다
    다른 사정(특정 라이브러리가 시끄럽다 / 이 라이브러리만은 다 보고 싶다)을 반영한다.
    ``verbose`` 를 나중에 적용해 같은 이름이 양쪽에 있으면 "보이게" 쪽이 이긴다 —
    디버깅하려고 연 것을 조용히 덮는 편보다 낫다.
    """
    for name in quiet:
        logging.getLogger(str(name)).setLevel(quiet_level)
    for name in verbose:
        logging.getLogger(str(name)).setLevel(verbose_level)


def setup_logging(
    log_dir: str = "logs",
    level: int = logging.DEBUG,
    *,
    force_new: bool = False,
    quiet_third_party: bool = True,
) -> str:
    """루트 로거에 파일 핸들러를 붙이고 로그 파일 경로를 반환한다(멱등적).

    파일에는 기본적으로 DEBUG 까지(프롬프트·LLM 원문 응답·HTTP 요청 포함) 모두 남긴다.
    화면(콘솔)에 무엇을 보일지는 호출 측의 ``basicConfig`` 등 별도 핸들러가 정한다.

    ``quiet_third_party`` 가 참이면 :data:`NOISY_LOGGERS` 를 WARNING 으로 올려
    ``urllib3``/``httpcore``/``openai._base_client`` 같은 저수준 로그를 숨긴다.
    환경변수 :data:`NOISE_ENV` 로 실행 단위 해제가 가능하다.
    """
    global _HANDLER, _PATH
    keep_noise = os.environ.get(NOISE_ENV, "").strip().lower() in ("1", "true", "yes", "on")
    # NOTSET 을 주는 쪽은 "필터 해제" — 앞선 실행이 올려 둔 하한을 되돌린다.
    quiet_noisy_loggers(logging.WARNING if (quiet_third_party and not keep_noise) else logging.NOTSET)
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
