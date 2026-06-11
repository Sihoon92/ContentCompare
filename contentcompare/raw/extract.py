"""Raw Extractor 디스패처 — 확장자로 적절한 추출기를 고르고 json 으로 직렬화.

    extract_raw("standard.xlsx")  → RawExcelDocument
    extract_raw("desc.docx")      → RawWordDocument
    raw_to_json(doc)              → 들여쓰기된 json 문자열(physical_raw.json)
"""

from __future__ import annotations

import json
import os
from typing import Any

_EXT_MAP = {
    ".xlsx": "excel",
    ".xlsm": "excel",
    ".xls": "excel",
    ".docx": "word",
    ".doc": "word",
}


def extract_raw(path: str):
    """파일 확장자에 맞는 raw 추출기를 호출한다(COM: xlwings/win32com).

    PPT(.pptx)는 추후 추가 예정. 현재는 Excel/Word 만 지원한다. COM(설치된 Office)을
    쓰므로 레거시 바이너리(.xls/.doc)도 Office 가 열 수 있다.
    """
    ext = os.path.splitext(path)[1].lower()
    kind = _EXT_MAP.get(ext)
    if kind == "excel":
        from .excel_raw import extract_excel_raw

        return extract_excel_raw(path)
    if kind == "word":
        from .word_raw import extract_word_raw

        return extract_word_raw(path)
    raise ValueError(
        f"raw 추출을 지원하지 않는 형식입니다: {ext} ({path}). "
        "지원: .xlsx/.xlsm(Excel), .docx(Word)"
    )


def raw_to_dict(doc: Any) -> dict[str, Any]:
    """raw 문서 객체 → 순수 dict."""
    return doc.to_dict()


def raw_to_json(doc: Any, *, indent: int = 2) -> str:
    """raw 문서 객체 → json 문자열(한글 보존, ensure_ascii=False)."""
    return json.dumps(doc.to_dict(), ensure_ascii=False, indent=indent)
