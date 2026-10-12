"""관리자 — 비밀번호 인증(무차별 대입 잠금)과 로그 읽기.

로그인이 없는 서비스에서 관리자 화면만 잠그는 이유: 작업 로그(``contentcompare_*.log``)는 DEBUG
까지 담아 **프롬프트와 LLM 원문 = 다른 사람의 문서 내용**이 있다(설계 §2 접근 제어).
⚠️ HTTPS 가 없어 비밀번호가 평문으로 오간다 — 사내망 전용이라는 전제(설계 §6 #11).

로그는 허용된 두 곳만 연다: ``logs/*.log``(서버 로그·과거 CLI 로그), ``jobs/<ID>/*.log``.
"""

from __future__ import annotations

import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .jobs import is_job_id

DISABLED = "disabled"
LOCKED = "locked"
WRONG = "wrong"


class AuthError(Exception):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class AdminAuth:
    def __init__(self, password: str, *, clock: Callable[[], float] = time.time,
                 session_ttl_s: float = 8 * 3600, max_failures: int = 5,
                 lockout_s: float = 60) -> None:
        self._password = password or ""
        self.clock = clock
        self.session_ttl_s = session_ttl_s
        self.max_failures = max_failures
        self.lockout_s = lockout_s
        self._sessions: dict[str, float] = {}
        self._failures = 0
        self._locked_until = 0.0
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self._password)

    def login(self, password: str) -> str:
        with self._lock:
            if not self.enabled:
                raise AuthError(DISABLED, "관리자 기능이 설정되지 않았습니다(.env 의 CC_ADMIN_PASSWORD).")
            now = self.clock()
            if now < self._locked_until:
                raise AuthError(LOCKED, f"비밀번호를 연속으로 틀려 잠겼습니다. "
                                        f"{int(self._locked_until - now) + 1}초 뒤 다시 시도하세요.")
            if not hmac.compare_digest((password or "").encode("utf-8"),
                                       self._password.encode("utf-8")):
                self._failures += 1
                if self._failures >= self.max_failures:
                    self._failures = 0
                    self._locked_until = now + self.lockout_s
                raise AuthError(WRONG, "비밀번호가 틀렸습니다.")
            self._failures = 0
            token = secrets.token_urlsafe(32)
            self._sessions[token] = now + self.session_ttl_s
            return token

    def verify(self, token: Optional[str]) -> bool:
        if not token:
            return False
        with self._lock:
            expires = self._sessions.get(token)
            if expires is None:
                return False
            if self.clock() >= expires:
                del self._sessions[token]
                return False
            return True

    def logout(self, token: Optional[str]) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)


# --------------------------------------------------------------------------- #
# 로그
# --------------------------------------------------------------------------- #
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
# logging_setup.setup_logging 의 형식: "%(asctime)s %(levelname)s %(name)s: %(message)s"
_HEAD = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} (DEBUG|INFO|WARNING|ERROR|CRITICAL) ")


@dataclass
class LogSource:
    id: str
    label: str
    path: Path
    size: int
    mtime: float

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "size": self.size, "mtime": self.mtime}


def _source(source_id: str, label: str, path: Path) -> LogSource:
    st = path.stat()
    return LogSource(source_id, label, path, st.st_size, st.st_mtime)


def list_log_sources(logs_dir: Path, jobs_root: Path) -> list[LogSource]:
    out: list[LogSource] = []
    if logs_dir.is_dir():
        for p in logs_dir.glob("*.log"):
            out.append(_source(f"logs/{p.name}", p.name, p))
    if jobs_root.is_dir():
        for d in jobs_root.iterdir():
            if d.is_dir() and is_job_id(d.name):
                for p in d.glob("*.log"):
                    out.append(_source(f"job/{d.name}/{p.name}", f"작업 {d.name} · {p.name}", p))
    out.sort(key=lambda s: s.mtime, reverse=True)
    return out


def resolve_source(source_id: str, logs_dir: Path, jobs_root: Path) -> Path:
    parts = (source_id or "").split("/")
    if len(parts) == 2 and parts[0] == "logs":
        base, name = logs_dir, parts[1]
    elif len(parts) == 3 and parts[0] == "job" and is_job_id(parts[1]):
        base, name = jobs_root / parts[1], parts[2]
    else:
        raise KeyError(source_id)
    if name != Path(name).name or name in ("", ".", "..") or not name.endswith(".log"):
        raise KeyError(source_id)
    path = base / name
    if not path.is_file():
        raise KeyError(source_id)
    return path


def read_records(path: Path, *, max_bytes: int = 5_000_000) -> list[dict]:
    """로그 → 레코드. 형식 있는 로그는 이어지는 줄(traceback)을 앞 레코드에 붙인다.

    파일이 크면 **끝부분만** 읽는다(관리자는 대개 최근 것을 본다. 전체는 다운로드로).
    """
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            data = f.read()
            data = data[data.find(b"\n") + 1:]  # 잘린 첫 줄은 버린다
        else:
            data = f.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    structured = any(_HEAD.match(line) for line in lines[:200])
    records: list[dict] = []
    for line in lines:
        m = _HEAD.match(line)
        if not structured or m or not records:
            records.append({"level": m.group(1) if m else "", "text": line})
        else:
            records[-1]["text"] += "\n" + line
    return records


def filter_records(records: list[dict], *, min_level: str = "DEBUG",
                   query: str = "") -> list[dict]:
    """레벨 하한과 검색어. 레벨이 없는 줄(``console.log``)은 INFO 로 본다."""
    floor = LEVELS.index(min_level) if min_level in LEVELS else 0
    q = (query or "").strip().lower()
    out = []
    for r in records:
        level = LEVELS.index(r["level"]) if r["level"] in LEVELS else LEVELS.index("INFO")
        if level < floor:
            continue
        if q and q not in r["text"].lower():
            continue
        out.append(r)
    return out


def page(records: list[dict], *, tail: int = 500, before: Optional[int] = None) -> dict:
    end = len(records) if before is None else max(0, min(before, len(records)))
    start = max(0, end - max(1, tail))
    return {"records": records[start:end], "start": start, "end": end, "total": len(records)}
