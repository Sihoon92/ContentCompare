"""프로세스 확인·종료 — 서버가 강제로 죽은 뒤 살아남은 worker 를 찾아 끝내기 위한 것.

서버가 정상 종료하면 lifespan 이 worker 를 끝낸다. 작업 관리자 강제 종료·크래시·Ctrl+C 두 번이면
그 단계를 건너뛰어 worker(별도 프로세스 그룹)와 Office 가 남는다. 재시작 때 ``runner.json`` 의
PID 로 찾아 끝내는데, **PID 는 재사용된다** — 그래서 PID 만 보지 않고 **프로세스 생성 시각**까지
맞아야 같은 프로세스로 본다(엉뚱한 프로세스를 죽이지 않게).

의존성을 늘리지 않으려고(psutil 없음) Windows 는 ``ctypes`` 로 ``GetProcessTimes`` 를 부르고
종료는 ``taskkill /T``(자식까지)로 한다. 다른 OS 는 아무것도 하지 않는다 — 운영 대상이 Windows 다.
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional

__all__ = ["NullProcessProbe", "WindowsProcessProbe", "default_process_probe"]

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_EPOCH_DIFF_S = 11644473600.0
"""FILETIME(1601-01-01 기준 100ns) → Unix 시각 보정."""


class NullProcessProbe:
    """아무 프로세스도 모른다 — 그래서 아무것도 죽이지 않는다(Windows 가 아닌 곳)."""

    def creation_time(self, pid: int) -> Optional[float]:
        return None

    def kill_tree(self, pid: int) -> None:
        return None


class WindowsProcessProbe:
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k32.GetProcessTimes.restype = wintypes.BOOL
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.GetExitCodeProcess.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        self._k32 = k32

    def creation_time(self, pid: int) -> Optional[float]:
        """살아 있으면 생성 시각(Unix 초), 없거나 이미 끝났으면 ``None``."""
        if not isinstance(pid, int) or pid <= 0 or pid > 0xFFFFFFFF:
            return None
        ctypes, wt = self._ctypes, self._wintypes
        handle = self._k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            # 누가 핸들을 쥐고 있으면 끝난 프로세스도 열린다 — 종료코드로 생존을 가린다.
            code = wt.DWORD()
            if not self._k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return None
            if code.value != _STILL_ACTIVE:
                return None
            times = [wt.FILETIME() for _ in range(4)]
            if not self._k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return None
            created = times[0]
            ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
            return ticks / 1e7 - _EPOCH_DIFF_S
        finally:
            self._k32.CloseHandle(handle)

    def kill_tree(self, pid: int) -> None:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=30)


def default_process_probe():
    return WindowsProcessProbe() if os.name == "nt" else NullProcessProbe()
