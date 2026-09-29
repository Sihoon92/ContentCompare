"""프로세스 확인·종료 — Windows 실제 구현(ctypes·taskkill). 다른 OS 는 아무것도 하지 않는다."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from contentcompare.web import process_probe as P


def test_null_probe_never_reports_or_kills():
    probe = P.NullProcessProbe()
    assert probe.creation_time(os.getpid()) is None
    probe.kill_tree(os.getpid())                   # 아무 일도 안 한다


def test_default_probe_follows_platform():
    expected = P.WindowsProcessProbe if os.name == "nt" else P.NullProcessProbe
    assert isinstance(P.default_process_probe(), expected)


@pytest.mark.skipif(os.name != "nt", reason="Windows 전용")
def test_windows_probe_reads_creation_time_and_kills_tree():
    probe = P.WindowsProcessProbe()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    try:
        created = probe.creation_time(proc.pid)
        assert created is not None and abs(created - time.time()) < 60
        assert probe.creation_time(proc.pid) == created   # 같은 프로세스면 매번 같다
        probe.kill_tree(proc.pid)
        proc.wait(timeout=15)
        assert probe.creation_time(proc.pid) is None      # 끝난 프로세스는 '없음'
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Windows 전용")
def test_windows_probe_unknown_pid_is_none():
    assert P.WindowsProcessProbe().creation_time(0x7FFFFFF0) is None
