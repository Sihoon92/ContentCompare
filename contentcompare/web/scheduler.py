"""작업 대기열 — **한 번에 1건**(설계 §2·§6).

동시 실행이 1건인 이유는 둘이다. PowerPoint COM 은 ``DispatchEx`` 로도 PC 전체에 하나뿐이라
한 작업의 ``Quit`` 이 남의 PPT 를 닫고, LLM 요청 한도는 키 하나를 공유하는데 ``RateLimiter``
는 프로세스 안에만 있다. 1건이면 두 문제가 모두 사라진다.

상태의 원본은 디스크(``job.json``)다. 여기서 들고 있는 것은 "지금 도는 프로세스 핸들" 하나뿐이다.
:meth:`tick` 은 한 번의 결정적 스케줄링 단계라 테스트가 스레드 없이 부를 수 있다.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Protocol

from .jobs import CANCELLED, FAILED, INTERRUPTED, QUEUED, RUNNING, SUCCEEDED, Job, JobStore

logger = logging.getLogger(__name__)


class Handle(Protocol):
    def poll(self) -> Optional[int]: ...
    def terminate(self) -> None: ...


Launcher = Callable[[Job, Path], Handle]


class OfficeGuard(Protocol):
    def snapshot(self) -> set[int]: ...
    def cleanup(self, before: set[int]) -> list[int]: ...


class NullOfficeGuard:
    """Office 정리를 하지 않는다(Windows 가 아닌 곳·테스트)."""

    def snapshot(self) -> set[int]:
        return set()

    def cleanup(self, before: set[int]) -> list[int]:
        return []


class JobScheduler:
    def __init__(
        self,
        store: JobStore,
        launcher: Launcher,
        *,
        office: Optional[OfficeGuard] = None,
        clock: Callable[[], float] = time.time,
        poll_interval: float = 0.5,
        housekeeping: Optional[Callable[[], object]] = None,
        housekeeping_interval: float = 3600.0,
    ) -> None:
        self.store = store
        self.launcher = launcher
        self.office = office or NullOfficeGuard()
        self.clock = clock
        self.poll_interval = poll_interval
        self.housekeeping = housekeeping
        self.housekeeping_interval = housekeeping_interval
        self._lock = threading.RLock()
        self._running: Optional[tuple[str, Handle, set[int]]] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_housekeeping = float("-inf")

    # ------------------------------------------------------------------ #
    @property
    def running_id(self) -> Optional[str]:
        with self._lock:
            return self._running[0] if self._running else None

    def queued(self) -> list[Job]:
        jobs = [j for j in self.store.list() if j.state == QUEUED]
        return sorted(jobs, key=lambda j: (j.created_ts, j.id))

    def positions(self) -> dict[str, int]:
        """대기 작업 → 앞에 있는 작업 수(실행 중인 것 포함). 화면의 '앞에 N건'."""
        with self._lock:
            ahead = 1 if self._running else 0
            return {j.id: ahead + i for i, j in enumerate(self.queued())}

    # ------------------------------------------------------------------ #
    def recover(self) -> list[str]:
        """서버 재시작 직후: 실행 중이던 작업은 ``interrupted``. **다시 돌리지 않는다** — LLM 비용."""
        changed = []
        with self._lock:
            for job in self.store.list():
                if job.state == RUNNING:
                    job.state = INTERRUPTED
                    job.error = job.error or "서버가 재시작되어 중단되었습니다."
                    job.finished_ts = self.clock()
                    self.store.save(job)
                    changed.append(job.id)
        return changed

    def submit(self, job: Job) -> Job:
        with self._lock:
            job.state = QUEUED
            job.created_ts = job.created_ts or self.clock()
            self.store.save(job)
        return job

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self.store.load(job_id)
            if job is None or job.is_final:
                return False
            if job.state == QUEUED:
                self._close(job, CANCELLED, "사용자가 취소했습니다.")
                return True
            if self._running and self._running[0] == job_id:
                _, handle, before = self._running
                try:
                    try:
                        handle.terminate()
                    except Exception:  # noqa: BLE001
                        logger.exception("작업 종료 중 오류(계속 진행): %s", job_id)
                    try:
                        self.office.cleanup(before)
                    except Exception:  # noqa: BLE001
                        logger.exception("Office 정리 중 오류(계속 진행): %s", job_id)
                    self._close(job, CANCELLED, "사용자가 취소했습니다.")
                finally:
                    self._running = None
                return True
            return False

    def tick(self) -> None:
        with self._lock:
            if self._running:
                job_id, handle, before = self._running
                code = handle.poll()
                if code is None:
                    return
                self._running = None
                try:
                    self._finalize(job_id, code, before)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("작업 마무리 중 오류(계속 진행): %s", job_id)
                    # job 을 반드시 FAILED 로 표시하되, 마무리 실패도 기록
                    job = self.store.load(job_id)
                    if job is not None:
                        try:
                            self._close(job, FAILED, f"작업 마무리 중 오류: {type(exc).__name__}: {exc}")
                        except Exception:  # noqa: BLE001
                            logger.exception("job 상태 저장 실패: %s", job_id)
            waiting = self.queued()
            if not waiting:
                return
            job = waiting[0]
            before = self.office.snapshot()
            job.state = RUNNING
            job.started_ts = self.clock()
            self.store.save(job)
            try:
                handle = self.launcher(job, self.store.dir(job.id))
            except Exception as exc:  # noqa: BLE001 — 한 작업의 기동 실패가 대기열을 막으면 안 된다
                logger.exception("작업 기동 실패: %s", job.id)
                self._close(job, FAILED, f"작업을 시작하지 못했습니다: {type(exc).__name__}: {exc}")
                return
            self._running = (job.id, handle, before)
            logger.info("작업 시작: %s (%s)", job.id, job.engine)

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="job-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """스레드를 멈추고, 실행 중인 작업은 종료해 ``interrupted`` 로 남긴다."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
        with self._lock:
            if self._running:
                job_id, handle, before = self._running
                try:
                    try:
                        handle.terminate()
                    except Exception:  # noqa: BLE001
                        logger.exception("작업 종료 중 오류(계속 진행): %s", job_id)
                    try:
                        self.office.cleanup(before)
                    except Exception:  # noqa: BLE001
                        logger.exception("Office 정리 중 오류(계속 진행): %s", job_id)
                    job = self.store.load(job_id)
                    if job is not None:
                        self._close(job, INTERRUPTED, "서버가 종료되어 중단되었습니다.")
                finally:
                    self._running = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
                now = self.clock()
                if self.housekeeping and now - self._last_housekeeping >= self.housekeeping_interval:
                    self._last_housekeeping = now
                    self.housekeeping()
            except Exception:  # noqa: BLE001 — 스케줄러 스레드는 죽으면 안 된다
                logger.exception("스케줄러 오류(계속 진행)")
            self._stop.wait(self.poll_interval)

    # ------------------------------------------------------------------ #
    def _finalize(self, job_id: str, code: int, before: set[int]) -> None:
        job = self.store.load(job_id)
        if job is None:
            return
        if code == 0:
            self._close(job, SUCCEEDED, "")
            logger.info("작업 완료: %s", job_id)
            return
        # 비정상 종료면 Office 가 남았을 수 있다(정상 종료는 파이프라인이 finally 에서 닫는다).
        try:
            self.office.cleanup(before)
        except Exception:  # noqa: BLE001
            logger.exception("Office 정리 중 오류(계속 진행): %s", job_id)
        error = _read_error(self.store.dir(job_id)) or f"worker 종료코드 {code}"
        self._close(job, FAILED, error)
        logger.warning("작업 실패: %s — %s", job_id, error)

    def _close(self, job: Job, state: str, error: str) -> None:
        job.state = state
        job.error = error
        job.finished_ts = self.clock()
        self.store.save(job)


def _read_error(job_dir: Path) -> str:
    try:
        return str(json.loads((job_dir / "error.json").read_text(encoding="utf-8")).get("error") or "")
    except (OSError, ValueError, AttributeError):
        return ""
