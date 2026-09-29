"""업로드 이름 검증과 저장(설계 §6 #9).

- 경로 조작 차단: ``..`` 금지, 드라이브·선행 ``/`` 제거. 폴더 업로드의 **하위 경로는 보존**한다.
- Office 잠금 파일(``~$``)과 지원하지 않는 확장자는 **건너뛰고 알린다**(폴더째 올리면 섞여
  들어오는 것이 정상이라 거절하면 불편만 크다).
- **이름이 같은 문서는 거절한다.** 산출물 폴더가 문서 이름으로 정해져(``artifacts/<문서명>/``)
  같은 이름 둘이면 결과가 조용히 섞인다 — 실패보다 나쁘다. Windows 처럼 대소문자를 무시한다.
"""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from ..ui.runner import SUPPORTED_EXTS

REFERENCE_EXTS = (".xlsx", ".xls", ".xlsm")


class UploadError(ValueError):
    """사용자에게 그대로 보여 줄 수 있는 업로드 거절 사유."""


@dataclass
class UploadPlan:
    reference: str
    targets: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def safe_relpath(name: str) -> str:
    text = (name or "").replace("\\", "/")
    text = re.sub(r"^[A-Za-z]:", "", text.strip())
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if not parts:
        raise UploadError(f"파일 이름이 비어 있습니다: {name!r}")
    if any(p == ".." for p in parts):
        raise UploadError(f"경로에 '..' 를 쓸 수 없습니다: {name!r}")

    # Windows 보안: 각 부분이 유효한지 검증
    reserved_names = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    }
    invalid_chars = set("<>:\"|?*") | set(chr(i) for i in range(0x00, 0x20))

    for part in parts:
        # 유효하지 않은 문자 검사
        if any(c in invalid_chars for c in part):
            raise UploadError(f"파일 이름에 유효하지 않은 문자가 있습니다: {part!r}")
        # 뒤에 공백이나 점이 오는 경우 거절
        if part.endswith(" ") or part.endswith("."):
            raise UploadError(f"파일 이름이 공백이나 점으로 끝날 수 없습니다: {part!r}")
        # 예약된 기기 이름 검사 (확장자 앞 부분만)
        stem = part.split(".")[0].upper()
        if stem in reserved_names:
            raise UploadError(f"Windows 예약 이름을 쓸 수 없습니다: {part!r}")

    return "/".join(parts)


def resolve_under(base: Path, rel: str) -> Path:
    """경로가 base 아래에 있는지 검증하고 절대 경로를 반환한다.

    :param base: 기준 디렉토리
    :param rel: 상대 경로 문자열 (posix 구분자, safe_relpath 를 거친 것이어야 함)
    :raises UploadError: 경로가 base 를 벗어나면
    :return: 절대 경로
    """
    # rel은 posix 문자열이므로 Path 생성 시 변환 필요 (Windows에서도 `/` 처리)
    result = (base / rel).resolve()
    if not result.is_relative_to(base.resolve()):
        raise UploadError(f"경로가 업로드 디렉토리를 벗어났습니다: {rel}")
    return result


def _ext(rel: str) -> str:
    return os.path.splitext(posixpath.basename(rel))[1].lower()


def plan_upload(reference_name: str, target_names: list[str]) -> UploadPlan:
    ref = safe_relpath(reference_name)
    ref_base = posixpath.basename(ref)
    if ref_base.startswith("~$"):
        raise UploadError(f"기준 문서가 Office 잠금 파일입니다: {ref_base}")
    if _ext(ref) not in REFERENCE_EXTS:
        raise UploadError(
            f"기준 문서는 Excel({', '.join(REFERENCE_EXTS)})이어야 합니다: {ref_base}")

    plan = UploadPlan(reference=ref)
    for raw in target_names:
        rel = safe_relpath(raw)
        if posixpath.basename(rel).startswith("~$") or _ext(rel) not in SUPPORTED_EXTS:
            plan.skipped.append(rel)
        else:
            plan.targets.append(rel)
    if not plan.targets:
        raise UploadError(f"대상 문서가 없습니다(지원 확장자: {', '.join(SUPPORTED_EXTS)}).")

    seen = {ref_base.lower(): ref}
    clashes = []
    for rel in plan.targets:
        key = posixpath.basename(rel).lower()
        if key in seen:
            clashes.append(f"{seen[key]} ↔ {rel}")
        else:
            seen[key] = rel
    if clashes:
        raise UploadError(
            "같은 이름의 문서가 있습니다 — 산출물 폴더가 겹쳐 결과가 섞이므로 이름을 바꿔 "
            "다시 올리세요: " + "; ".join(clashes))
    return plan


def save_stream(src: BinaryIO, dest: Path, max_bytes: int, *,
                chunk_size: int = 1 << 20) -> int:
    """``src`` 를 ``dest`` 로 복사한다. 한도를 넘으면 쓰던 파일을 지우고 :class:`UploadError`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = src.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise UploadError(
                        f"파일이 너무 큽니다(한도 {max_bytes / (1024 * 1024):.0f}MB): {dest.name}")
                out.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return total
