"""COM(Office) 리소스 정리 안전망.

각 리더(Excel/Word/PPT)는 생성한 Application 인스턴스를 :func:`track` 으로 등록하고,
정상/예외 종료 시 :func:`close_app` 로 **완전히 종료**한다. 프로그램이 끝나거나 예기치
못한 오류로 빠져나갈 때도 :func:`close_all`(``atexit`` 등록)이 남은 인스턴스를 강제
종료해, 기준/대상 문서가 잠긴 채(고아 프로세스) 남지 않도록 한다.

- Excel(xlwings): ``app.quit()`` 후에도 COM 참조가 남으면 ``EXCEL.EXE`` 가 살아남아
  파일을 잠그는 일이 잦으므로, 이어서 ``app.kill()`` 로 프로세스까지 강제 종료한다.
- Word/PPT(win32com): ``Application.Quit()`` 으로 종료한다(DispatchEx 전용 인스턴스).
"""

from __future__ import annotations

import atexit
import logging
import threading

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_OPEN: list[tuple[str, object]] = []  # (kind, app) — 현재 열려있는 Office 인스턴스


def track(kind: str, app: object) -> object:
    """생성한 Application 인스턴스를 등록한다(kind: ``excel`` | ``word`` | ``ppt``)."""
    with _LOCK:
        _OPEN.append((kind, app))
    return app


def untrack(app: object) -> None:
    with _LOCK:
        _OPEN[:] = [(k, a) for (k, a) in _OPEN if a is not app]


def close_app(kind: str, app: object | None) -> None:
    """앱 인스턴스를 완전히 종료하고 등록 해제한다(정리 실패는 경고만).

    Excel 은 ``quit`` 후 ``kill`` 까지 시도해 고아 프로세스를 남기지 않는다.
    """
    if app is None:
        return
    try:
        if kind == "excel":
            # xlwings App: quit 후 프로세스 kill 로 확실히 종료(고아 EXCEL.EXE 방지).
            try:
                app.quit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[Excel] app.quit 실패(무시): %s", exc)
            try:
                app.kill()
            except Exception:  # noqa: BLE001 - 이미 종료됐으면 실패할 수 있음
                pass
        else:
            # win32com Word/PPT Application.
            try:
                app.Quit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] Quit 실패(무시): %s", kind, exc)
    finally:
        untrack(app)


def close_all() -> None:
    """등록된(아직 안 닫힌) 모든 Office 인스턴스를 강제 종료한다."""
    with _LOCK:
        pending = list(_OPEN)
    if pending:
        logger.info("[COM] 남은 Office 인스턴스 %d개 강제 정리", len(pending))
    for kind, app in pending:
        close_app(kind, app)


# 프로그램 종료 시 마지막 안전망(정상 종료/미처리 예외 경로에서 동작).
atexit.register(close_all)
