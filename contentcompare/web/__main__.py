"""``python -m contentcompare.web`` — 웹 서버 기동(설계 §11).

uvicorn 은 **worker 1개**로 띄운다. 대기열 스케줄러가 서버 프로세스 안에 있어서 여럿 띄우면
"한 번에 1건"이 깨진다. Ctrl+C 로 끄면 열린 연결을 최대 :data:`GRACEFUL_SHUTDOWN_S` 초 기다린 뒤
lifespan 종료에서 실행 중인 작업을 ``interrupted`` 로 남긴다.

⚠️ Office 자동화는 로그인된 데스크톱 세션이 필요하다 — Windows 서비스로 등록하지 말고
서버 PC 에 로그인한 계정에서 실행한다(설계 §6 #1).
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
from pathlib import Path
from typing import Optional

from ..logging_setup import log_print, setup_console
from .app import create_app
from .settings import load_settings
from .worker import DOTENV_ENV

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

GRACEFUL_SHUTDOWN_S = 5
"""종료 시 열린 연결을 기다리는 상한(초). 기본(None)이면 uvicorn 이 **영원히** 기다린다.

SSE 는 작업이 끝나야 닫히므로 창이 하나만 열려 있어도 Ctrl+C 가 끝나지 않는다. 사람이 두 번 누르면
uvicorn 이 lifespan 종료를 건너뛰어 worker(별도 프로세스 그룹)가 살아남고, 재시작하면 다음 작업과
**동시에** 돈다. 상한이 지나면 남은 연결을 끊고 lifespan 종료(작업 ``interrupted``)로 넘어간다.
"""

UVICORN_OPTIONS = {"workers": 1, "log_config": None,
                   "timeout_graceful_shutdown": GRACEFUL_SHUTDOWN_S}
"""``uvicorn.run`` 에 넘기는 고정 옵션. 테스트가 같은 값으로 실제 서버를 띄워 종료를 확인한다."""


def configure_server_logging(logs_dir: Path) -> Path:
    """서버 로그는 `logs/server.log` 하나에 이어 쓴다(관리자 페이지의 첫 번째 소스)."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "server.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    setup_console(level=logging.INFO)
    return path


def access_urls(host: str, port: int) -> list[str]:
    if host not in ("0.0.0.0", "::", ""):
        return [f"http://{host}:{port}"]
    names = {socket.gethostname()}
    try:
        names.update(ip for ip in socket.gethostbyname_ex(socket.gethostname())[2]
                     if not ip.startswith("127."))
    except OSError:
        pass
    return [f"http://localhost:{port}"] + [f"http://{n}:{port}" for n in sorted(names)]


def dev_app():
    """``start.bat dev`` 용 — ``uvicorn --factory --reload`` 가 부르는 인자 없는 팩토리.

    ``--reload`` 는 앱을 import 문자열로만 받아서 ``main()`` 의 인자 처리를 거치지 않는다.
    같은 일을 여기서 한다: `.env` 경로를 worker 에 물려주고 서버 로그를 켠 뒤 앱을 만든다.
    """
    dotenv = os.environ.get(DOTENV_ENV, ".env")
    settings = load_settings(dotenv)
    os.environ[DOTENV_ENV] = str(Path(dotenv).resolve())
    configure_server_logging(Path(settings.logs_dir))
    return create_app(settings)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m contentcompare.web",
                                     description="ContentCompare 웹 서버")
    parser.add_argument("--env", default=".env", help=".env 경로(기본: .env)")
    parser.add_argument("--host", default="", help="바인드 주소(기본: CC_HOST)")
    parser.add_argument("--port", type=int, default=0, help="포트(기본: CC_PORT)")
    args = parser.parse_args(argv)

    settings = load_settings(args.env)
    # worker 는 별도 프로세스라 같은 .env 를 읽도록 경로를 물려준다(launcher 가 os.environ 을 복사).
    os.environ[DOTENV_ENV] = str(Path(args.env).resolve())
    settings.host = args.host or settings.host
    settings.port = args.port or settings.port
    log_path = configure_server_logging(Path(settings.logs_dir))
    app = create_app(settings)

    log_print(f"서버 로그: {log_path}")
    for url in access_urls(settings.host, settings.port):
        log_print(f"접속 주소: {url}")
    if not settings.admin_enabled:
        log_print("관리자 기능 꺼짐: .env 의 CC_ADMIN_PASSWORD 를 설정하세요.")

    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port, **UVICORN_OPTIONS)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
