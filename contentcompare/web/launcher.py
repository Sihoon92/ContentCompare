"""worker 프로세스 실행·종료와 Office 프로세스 정리.

- worker 의 stdout/stderr 를 작업 폴더의 ``console.log`` 로 받는다 — 이것이 실행 창의 로그다.
  ``PYTHONIOENCODING=utf-8`` 로 cp949 콘솔에서 한글·기호 줄이 사라지는 문제(CLAUDE.md
  「콘솔 인코딩」)를 피하고, ``PYTHONUNBUFFERED=1`` 로 줄이 바로 파일에 닿게 한다.
- 종료는 **프로세스 트리**째(``taskkill /T``) — worker 가 띄운 Office 자식까지.
- Office 정리(설계 §5.4): 작업 시작 전 목록에 **없던** ``EXCEL/WINWORD/POWERPNT`` 만 죽인다.
  ⚠️ 작업 중 서버 PC 에서 사람이 새로 연 Office 도 "없던 것"이라 함께 닫힌다 — 그래서
  서버 PC 에서 사람이 Office 를 같이 쓰지 않는 것이 운영 전제다(설계 §6 #2).
"""

from __future__ import annotations

import csv
import io
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from .jobs import Job
from .scheduler import Launcher, NullOfficeGuard
from .worker import FACTORY_ENV

logger = logging.getLogger(__name__)

OFFICE_IMAGES = ("EXCEL.EXE", "WINWORD.EXE", "POWERPNT.EXE")

__all__ = ["FACTORY_ENV", "OFFICE_IMAGES", "ProcessHandle", "process_launcher",
           "parse_tasklist_csv", "WindowsOfficeGuard", "default_office_guard"]


class ProcessHandle:
    def __init__(self, proc: subprocess.Popen, log_file) -> None:
        self.proc = proc
        self._log = log_file

    @property
    def pid(self) -> int:
        return self.proc.pid

    def poll(self) -> Optional[int]:
        code = self.proc.poll()
        if code is not None:
            self._close()
        return code

    def terminate(self) -> None:
        """프로세스 트리를 끝낸다. **멈추거나 예외를 올리지 않는다** — 취소·서버 종료가 여기서 막히면 안 된다."""
        if self.proc.poll() is None:
            if os.name == "nt":
                try:
                    subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                                   capture_output=True, timeout=30)
                except subprocess.TimeoutExpired:
                    logger.warning("taskkill 이 응답하지 않아 직접 종료합니다: PID %s", self.proc.pid)
                    self.proc.kill()
            else:
                self.proc.kill()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning("worker 가 종료되지 않았습니다: PID %s", self.proc.pid)
        self._close()

    def _close(self) -> None:
        if not self._log.closed:
            self._log.close()


def process_launcher(python: str = sys.executable, *, extra_env: Optional[dict] = None,
                     cwd: Optional[str] = None) -> Launcher:
    def launch(job: Job, job_dir: Path) -> ProcessHandle:
        log = open(job_dir / "console.log", "ab")
        env = dict(os.environ)
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
        env.update(extra_env or {})
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(
                [python, "-m", "contentcompare.web.worker", str(Path(job_dir).resolve())],
                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                cwd=cwd, env=env, creationflags=flags,
            )
        except Exception:
            log.close()
            raise
        return ProcessHandle(proc, log)

    return launch


def parse_tasklist_csv(text: str) -> set[int]:
    pids: set[int] = set()
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 2 and row[0].upper() in OFFICE_IMAGES:
            try:
                pids.add(int(row[1]))
            except ValueError:
                continue
    return pids


class WindowsOfficeGuard:
    def __init__(self, run: Callable = subprocess.run) -> None:
        self._run = run

    def snapshot(self) -> set[int]:
        try:
            out = self._run(["tasklist", "/FO", "CSV", "/NH"],
                            capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError):
            return set()
        return parse_tasklist_csv(out or "")

    def cleanup(self, before: set[int]) -> list[int]:
        killed = []
        for pid in sorted(self.snapshot() - set(before)):
            try:
                self._run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=15)
            except (OSError, subprocess.SubprocessError):
                continue
            killed.append(pid)
        if killed:
            logger.warning("남은 Office 프로세스 정리: %s", killed)
        return killed


def default_office_guard():
    return WindowsOfficeGuard() if os.name == "nt" else NullOfficeGuard()
