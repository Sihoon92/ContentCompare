"""실행 로그를 파일로 저장하는 유틸.

Streamlit/CLI 가 시작 시 한 번 :func:`setup_logging` 을 부르면, 모든 모듈의
로그(readers 의 COM 단계 포함)가 ``logs/contentcompare_<시각>.log`` 에 쌓인다.
Streamlit 의 잦은 rerun 에서도 핸들러가 중복되지 않도록 멱등적으로 동작한다.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Iterable

_HANDLER: logging.Handler | None = None
_PATH: str | None = None
_CONSOLE: logging.Handler | None = None

#: 이 표시가 붙은 레코드는 **콘솔로 내보내지 않는다**(파일에는 그대로 남는다).
#: :func:`log_print` 처럼 이미 ``print`` 로 화면에 나간 메시지가 콘솔 핸들러를
#: 통해 한 번 더 찍히는 것을 막는다.
NO_CONSOLE = "no_console"

#: 콘솔 한 줄의 모양. 파일(``%(asctime)s`` 포함)보다 짧다 — 화면은 흐름을 보는
#: 곳이고 시각은 타임라인이 이미 줄 앞에 찍는다.
CONSOLE_FORMAT = "%(levelname)s %(name)s: %(message)s"

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


class ConsoleFilter(logging.Filter):
    """``NO_CONSOLE`` 표시가 붙은 레코드를 콘솔에서 걸러낸다.

    **레벨로는 못 막는다.** ``log_print`` 는 "사람은 이미 화면에서 봤으니 파일에만
    남겨라"는 뜻인데, 그것을 "INFO 로 두면 WARNING 콘솔이 걸러 주겠지"로 표현하면
    ``--verbose``/``--check`` 처럼 콘솔이 INFO 로 내려가는 순간 그대로 무너진다
    (실측: 화면에 모든 줄이 두 번씩 나왔다). 그래서 **레벨과 무관한 표시**로 막는다.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        return not getattr(record, NO_CONSOLE, False)


def setup_console(
    level: int = logging.WARNING, fmt: str = CONSOLE_FORMAT, stream: Any = None
) -> logging.Handler:
    """화면용 핸들러를 **자기 레벨을 가진 채로** 루트에 붙인다(멱등).

    ``logging.basicConfig`` 를 쓰면 안 되는 이유가 여기 있다 — 그쪽은 **루트 로거**의
    레벨만 정하고 자신이 만든 ``StreamHandler`` 는 ``NOTSET``(=필터 안 함)으로 둔다.
    그러면 콘솔의 유일한 문턱이 루트 레벨인데, :func:`setup_logging` 이 파일에 DEBUG 를
    담으려고 그 루트를 낮추는 순간 **화면이 통째로 열린다**(프롬프트·HTTP 페이로드까지).
    핸들러가 자기 레벨을 가지면 그 순서 의존이 사라진다.

    두 번 불러도 핸들러는 하나다 — 두 개면 그 자체로 중복 출력이 된다. 대신 레벨과
    형식은 갱신되므로 나중 호출이 이긴다.

    ``stream`` 은 테스트용 주입구다(기본 ``sys.stderr``) — "화면에 몇 번 나왔나"를 세려면
    출력을 붙잡을 수 있어야 하는데, 이 결함이 오래 살아남은 이유가 바로 그것을 세는
    테스트가 없었다는 데 있다. ``poster``/``sleep``/``clock`` 주입과 같은 규약이다.
    """
    global _CONSOLE
    if _CONSOLE is None:
        _CONSOLE = logging.StreamHandler(stream) if stream is not None else logging.StreamHandler()
        _CONSOLE.addFilter(ConsoleFilter())
        logging.getLogger().addHandler(_CONSOLE)
    _CONSOLE.setLevel(level)
    _CONSOLE.setFormatter(logging.Formatter(fmt))
    return _CONSOLE


def setup_logging(
    log_dir: str = "logs",
    level: int = logging.DEBUG,
    *,
    force_new: bool = False,
    quiet_third_party: bool = True,
) -> str:
    """루트 로거에 파일 핸들러를 붙이고 로그 파일 경로를 반환한다(멱등적).

    파일에는 기본적으로 DEBUG 까지(프롬프트·LLM 원문 응답·HTTP 요청 포함) 모두 남긴다.
    그러기 위해 **루트 레벨을 DEBUG 로 낮추므로**, 화면에 무엇을 보일지는 콘솔 핸들러가
    자기 레벨로 정해야 한다 — :func:`setup_console` 을 쓸 것. ``basicConfig`` 로 붙인
    핸들러는 레벨이 ``NOTSET`` 이라 이 함수가 루트를 낮추는 순간 화면이 통째로 열린다.

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

    **화면 한 번, 파일 한 번**이 이 함수의 계약이고, 그것을 :data:`NO_CONSOLE` 표시로
    지킨다 — 레코드에 그 표시가 붙어 있으면 :class:`ConsoleFilter` 가 콘솔 핸들러에서
    걸러내므로, 화면에는 아래 ``print`` 한 번만 나간다.

    ⚠️ **레벨로 막으려 하지 말 것.** 예전에는 "``level`` 을 INFO 로 두면 WARNING 인
    콘솔 핸들러가 걸러 준다"고 적혀 있었는데, 콘솔 핸들러는 애초에 WARNING 이었던 적이
    없었고(``basicConfig`` 는 핸들러를 ``NOTSET`` 으로 만든다) ``--verbose`` 로 콘솔이
    INFO 가 되면 어차피 무너지는 방식이었다. 실측에서 모든 줄이 두 번씩 나왔다.
    """
    sep = kwargs.get("sep", " ")
    message = sep.join(str(a) for a in args)
    print(*args, **kwargs)
    logging.getLogger(logger_name).log(level, message, extra={NO_CONSOLE: True})


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
