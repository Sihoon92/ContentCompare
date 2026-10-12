"""한 jobs 폴더에 서버 하나 — ``<jobs 폴더>/.server.lock`` 배타 잠금.

uvicorn 은 lifespan 시작(여기서 ``recover()``)을 **포트를 잡기 전에** 돈다. 그래서 start.bat 을
두 번 누르면 두 번째 서버가 포트 충돌로 죽기 전에 첫 서버의 실행 중 작업을 ``interrupted`` 로
덮어쓰고 대기 작업을 잠깐 띄웠다 죽인다. 포트가 다르면 스케줄러 둘이 대기열을 **동시에** 돌리고,
한쪽의 Office 정리가 다른 쪽의 Office 를 죽인다. 잠금을 ``recover()`` 보다 먼저 잡아 막는다.

잠금은 프로세스가 끝나면 OS 가 풀어 준다 — 서버가 강제로 죽어도 다음 기동을 막지 않는다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

__all__ = ["InstanceLock", "acquire_instance_lock"]


class InstanceLock:
    def __init__(self, path: Path, fd: int) -> None:
        self.path = path
        self._fd: Optional[int] = fd

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            _unlock(fd)
        except OSError:
            pass  # 닫으면 어차피 풀린다
        finally:
            os.close(fd)


def acquire_instance_lock(path: Path) -> InstanceLock:
    """잠금을 잡는다. 이미 누가 잡고 있으면 ``RuntimeError`` — 호출자는 기동을 멈춘다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        _lock(fd)
    except OSError:
        os.close(fd)
        raise RuntimeError(f"이미 같은 jobs 폴더로 실행 중인 서버가 있습니다: {path}") from None
    return InstanceLock(path, fd)


if os.name == "nt":
    import msvcrt

    def _lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
