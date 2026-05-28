"""문서 리더 공통 인터페이스 및 확장자 기반 디스패처."""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from ..config import AppConfig
from ..models import DocItem


@runtime_checkable
class DocumentReader(Protocol):
    """문서를 :class:`DocItem` 리스트로 변환하는 리더."""

    def read(self, path: str) -> list[DocItem]:
        ...


_EXT_MAP = {
    ".xlsx": "excel",
    ".xls": "excel",
    ".xlsm": "excel",
    ".docx": "word",
    ".doc": "word",
    ".pptx": "ppt",
    ".ppt": "ppt",
}


def get_reader(path: str, config: AppConfig) -> DocumentReader:
    """파일 확장자에 맞는 리더를 생성한다."""
    # 순환 import 방지를 위해 지연 import.
    from .excel_reader import ExcelReader
    from .ppt_reader import PptReader
    from .word_reader import WordReader

    ext = os.path.splitext(path)[1].lower()
    kind = _EXT_MAP.get(ext)
    if kind == "excel":
        return ExcelReader(config.excel)
    if kind == "word":
        return WordReader()
    if kind == "ppt":
        return PptReader()
    raise ValueError(f"지원하지 않는 문서 형식입니다: {ext} ({path})")
