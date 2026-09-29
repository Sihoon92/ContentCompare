"""작업(Job) 모델과 디스크 저장소.

**디스크의 `job.json` 이 상태의 원본이다**(설계 §4). 서버 메모리는 그 사본일 뿐이라,
서버가 재시작돼도 대기열·결과가 그대로 남는다. 작업 ID 는 정규식으로만 받아 경로 조작을
원천 차단한다 — ID 가 곧 폴더 이름이기 때문이다.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
INTERRUPTED = "interrupted"
FINAL_STATES = (SUCCEEDED, FAILED, CANCELLED, INTERRUPTED)

ENGINES = ("rag", "fact")

_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")


def new_job_id(now: Optional[datetime] = None, rand: Optional[str] = None) -> str:
    """``YYYYMMDD-HHMMSS-xxxx`` — 정렬하면 접수 순서가 된다."""
    now = now or datetime.now()
    return f"{now:%Y%m%d-%H%M%S}-{rand or secrets.token_hex(2)}"


def is_job_id(value: str) -> bool:
    return bool(_ID.match(value or ""))


@dataclass
class Job:
    id: str
    engine: str
    requester: str = ""
    client_id: str = ""
    reference: str = ""
    """``inputs/`` 기준 상대 경로. 예: ``reference/기준.xlsx``."""
    targets: list[str] = field(default_factory=list)
    """``inputs/`` 기준 상대 경로. 예: ``targets/자료/규격서.docx``(폴더 구조 보존)."""
    state: str = QUEUED
    created_ts: float = 0.0
    started_ts: float = 0.0
    finished_ts: float = 0.0
    error: str = ""
    llm: dict = field(default_factory=dict)
    """:func:`~contentcompare.web.settings.public_llm_summary` — API 키 없음."""

    @property
    def is_final(self) -> bool:
        return self.state in FINAL_STATES

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


class JobStore:
    def __init__(self, root: Union[str, Path]) -> None:
        self.root = Path(root)

    def dir(self, job_id: str) -> Path:
        if not is_job_id(job_id):
            raise ValueError(f"잘못된 작업 ID: {job_id!r}")
        return self.root / job_id

    def save(self, job: Job) -> None:
        """임시 파일에 쓴 뒤 교체한다 — 읽는 쪽이 반쯤 쓴 파일을 보지 않게."""
        d = self.dir(job.id)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "job.json.tmp"
        tmp.write_text(json.dumps(job.to_dict(), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, d / "job.json")

    def load(self, job_id: str) -> Optional[Job]:
        try:
            path = self.dir(job_id) / "job.json"
        except ValueError:
            return None
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            return Job.from_dict(data)
        except (OSError, ValueError, TypeError):
            return None

    def list(self) -> list[Job]:
        if not self.root.is_dir():
            return []
        found = []
        for p in sorted(self.root.iterdir()):
            if p.is_dir() and is_job_id(p.name):
                job = self.load(p.name)
                if job is not None:
                    found.append(job)
        return found

    def delete(self, job_id: str) -> None:
        shutil.rmtree(self.dir(job_id), ignore_errors=True)


def purge_expired(store: JobStore, *, now: float, days: int) -> list[str]:
    """끝난 지 ``days`` 일이 지난 작업 폴더를 지운다(사내 문서 원본이 쌓이지 않게, 설계 §6 #8).

    대기·실행 중인 작업은 건드리지 않는다. ``days <= 0`` 이면 아무것도 지우지 않는다.
    """
    if days <= 0:
        return []
    cutoff = now - days * 86400
    removed = []
    for job in store.list():
        if job.is_final and job.finished_ts and job.finished_ts < cutoff:
            store.delete(job.id)
            removed.append(job.id)
    return removed
