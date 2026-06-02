"""COM(Office) 인스턴스 정리 안전망 테스트 — 실제 Office 불필요(가짜 앱 사용)."""

from __future__ import annotations

import pytest

from contentcompare.readers import com_util


@pytest.fixture(autouse=True)
def _clear_registry():
    """각 테스트 전후로 전역 레지스트리를 비운다."""
    com_util._OPEN.clear()
    yield
    com_util._OPEN.clear()


class FakeExcelApp:
    def __init__(self, quit_raises=False):
        self.quit_called = False
        self.kill_called = False
        self._quit_raises = quit_raises

    def quit(self):
        self.quit_called = True
        if self._quit_raises:
            raise RuntimeError("quit 실패")

    def kill(self):
        self.kill_called = True


class FakeOfficeApp:
    """Word/PPT win32com 스타일(대문자 Quit)."""

    def __init__(self, quit_raises=False):
        self.quit_called = False
        self._quit_raises = quit_raises

    def Quit(self):  # noqa: N802 - COM 명명 모사
        self.quit_called = True
        if self._quit_raises:
            raise RuntimeError("Quit 실패")


def test_close_app_excel_quits_and_kills():
    app = FakeExcelApp()
    com_util.track("excel", app)
    com_util.close_app("excel", app)
    assert app.quit_called and app.kill_called   # quit 후 kill 까지
    assert app not in [a for _, a in com_util._OPEN]  # 등록 해제


def test_close_app_excel_kills_even_if_quit_raises():
    app = FakeExcelApp(quit_raises=True)
    com_util.track("excel", app)
    com_util.close_app("excel", app)  # 예외를 삼키고 kill 까지 진행
    assert app.kill_called


def test_close_app_word_quit():
    app = FakeOfficeApp()
    com_util.track("word", app)
    com_util.close_app("word", app)
    assert app.quit_called
    assert com_util._OPEN == []


def test_close_app_swallows_quit_error():
    app = FakeOfficeApp(quit_raises=True)
    com_util.track("ppt", app)
    com_util.close_app("ppt", app)  # 예외 없이 통과해야 함
    assert com_util._OPEN == []


def test_close_app_none_is_noop():
    com_util.close_app("excel", None)  # 예외 없이 통과


def test_close_all_closes_every_tracked_app():
    a = com_util.track("excel", FakeExcelApp())
    b = com_util.track("word", FakeOfficeApp())
    com_util.close_all()
    assert a.quit_called and a.kill_called
    assert b.quit_called
    assert com_util._OPEN == []  # 레지스트리 비워짐
