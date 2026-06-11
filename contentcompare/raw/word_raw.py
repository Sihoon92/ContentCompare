"""Word Raw Extractor — win32com(COM) 로 .docx 를 physical_raw 로 변환.

회사 환경 제약으로 python-docx 대신 **win32com(설치된 Word)** 를 사용한다. 기존
``readers/word_reader.py`` 와 동일한 COM 패턴을 따르되, raw 추출은 문단/표를
**문서 등장 순서대로** 담는다(해석은 LLM 단계의 몫).

기존 readers 와 마찬가지로 **COM I/O 와 순수 빌더를 분리** 한다:

- COM 계층(:func:`_probe_blocks`): Word 문서를 훑어 문단/표를 순서대로
  :class:`ParaProbe`/:class:`TableProbe` 로 만든다. Word 설치 필요 → 테스트 불가.
- 순수 계층(:func:`build_word_doc`): probe 리스트 → :class:`RawWordDocument`.
  block_id/order 부여, 빈 문단 제외. Word 없이 probe 주입해 테스트 가능.

win32com 은 Windows + Word 설치가 필요하므로 import 를 :func:`extract_word_raw`
시점으로 지연한다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from ..readers import com_util
from .models import RawWordBlock, RawWordDocument

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# COM I/O 와 순수 빌더의 경계 (테스트 시 직접 주입)
# --------------------------------------------------------------------------- #
@dataclass
class ParaProbe:
    """COM 으로 읽은 문단 1개."""

    text: str
    style_name: Optional[str] = None
    bold: Optional[bool] = None
    font_size: Optional[float] = None


@dataclass
class TableProbe:
    """COM 으로 읽은 표 1개(셀 텍스트 2D)."""

    rows: list[list[str]] = field(default_factory=list)


BlockProbe = Union[ParaProbe, TableProbe]


# --------------------------------------------------------------------------- #
# 순수 빌더 (Word 불필요 — 테스트 진입점)
# --------------------------------------------------------------------------- #
def build_word_doc(file_name: str, probes: list[BlockProbe]) -> RawWordDocument:
    """probe 리스트 → :class:`RawWordDocument`. 빈 문단/빈 표는 제외."""
    doc = RawWordDocument(file_name=file_name)
    order = 0
    for p in probes:
        if isinstance(p, ParaProbe):
            text = " ".join((p.text or "").split())
            if not text:
                continue
            order += 1
            doc.blocks.append(
                RawWordBlock(
                    block_id=f"w_b{order:03d}",
                    order=order,
                    type="paragraph",
                    text=text,
                    style=_style_dict(p),
                )
            )
        elif isinstance(p, TableProbe):
            rows = [[" ".join((c or "").split()) for c in row] for row in p.rows]
            if not rows or all(not any(r) for r in rows):
                continue
            order += 1
            doc.blocks.append(
                RawWordBlock(
                    block_id=f"w_b{order:03d}",
                    order=order,
                    type="table",
                    rows=rows,
                )
            )
    return doc


def _style_dict(p: ParaProbe) -> Optional[dict[str, Any]]:
    """문단 스타일 정보. 알 수 있는 값만 담고, 전부 없으면 None."""
    info: dict[str, Any] = {}
    if p.style_name:
        info["style_name"] = p.style_name
    if p.bold is not None:
        info["bold"] = p.bold
    if p.font_size is not None:
        info["font_size"] = p.font_size
    return info or None


# --------------------------------------------------------------------------- #
# COM 진입점
# --------------------------------------------------------------------------- #
def extract_word_raw(path: str) -> RawWordDocument:
    """docx 파일 경로 → :class:`RawWordDocument` (win32com/COM, 문서 순서 보존)."""
    try:
        import pythoncom  # noqa: F401
        import win32com.client as win32
    except ImportError as exc:  # pragma: no cover - 환경 의존
        raise RuntimeError(
            "pywin32 가 필요합니다(Windows + Word). pip install pywin32"
        ) from exc

    file_name = os.path.basename(path)
    abspath = os.path.abspath(path)

    logger.info("[RawWord] 열기: %s", abspath)
    pythoncom.CoInitialize()
    word = None
    com_doc = None
    try:
        word = win32.DispatchEx("Word.Application")
        com_util.track("word", word)
        word.Visible = False
        try:
            word.DisplayAlerts = False
        except Exception:  # noqa: BLE001
            pass

        com_doc = word.Documents.Open(abspath, False, True)  # ReadOnly
        probes = _probe_blocks(com_doc)
        doc = build_word_doc(file_name, probes)
        logger.info("[RawWord] 완료: 블록 %d개", len(doc.blocks))
        return doc
    except Exception:
        logger.exception("[RawWord] 처리 실패: %s", abspath)
        raise
    finally:
        if com_doc is not None:
            try:
                com_doc.Close(False)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[RawWord] doc.Close 실패(무시): %s", exc)
        com_util.close_app("word", word)
        try:
            pythoncom.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass


def _probe_blocks(com_doc) -> list[BlockProbe]:  # pragma: no cover - COM 의존
    """Word 문서를 훑어 문단/표를 등장 순서대로 probe 로 만든다.

    문단을 순회하되, 표 안의 문단을 만나면 그 표를 (처음 만난 위치에서) 한 번만
    :class:`TableProbe` 로 내보내고 같은 표의 이후 문단은 건너뛴다 → 본문 흐름 보존.
    """
    probes: list[BlockProbe] = []
    seen_tables: set[int] = set()

    _WD_WITHIN_TABLE = 12  # wdWithInTable

    for para in com_doc.Paragraphs:
        rng = para.Range
        in_table = False
        try:
            in_table = bool(rng.Information(_WD_WITHIN_TABLE))
        except Exception:  # noqa: BLE001
            in_table = False

        if in_table:
            table = _safe(lambda: rng.Tables(1))
            if table is None:
                continue
            key = _safe(lambda: int(table.Range.Start))
            if key in seen_tables:
                continue
            seen_tables.add(key)
            probes.append(TableProbe(rows=_read_table(table)))
            continue

        text = (rng.Text or "").strip()
        if not text:
            continue
        probes.append(
            ParaProbe(
                text=text,
                style_name=_safe(lambda: para.Style.NameLocal),
                bold=_coerce_bool(_safe(lambda: rng.Bold)),
                font_size=_coerce_size(_safe(lambda: rng.Font.Size)),
            )
        )
    return probes


def _read_table(table) -> list[list[str]]:  # pragma: no cover - COM 의존
    """Word 표 → 셀 텍스트 2D. 셀 끝의 제어문자(\\r\\x07)를 제거한다."""
    rows: list[list[str]] = []
    for row in table.Rows:
        cells: list[str] = []
        for cell in row.Cells:
            text = _safe(lambda: cell.Range.Text) or ""
            cells.append(text.replace("\r", " ").replace("\x07", "").strip())
        rows.append(cells)
    return rows


def _coerce_bool(v: Any) -> Optional[bool]:
    """COM Bold(-1/0/혼합) → True/False/None."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if v in (-1, 1, True):
        return True
    if v in (0, False):
        return False
    return None  # 혼합(wdUndefined)


def _coerce_size(v: Any) -> Optional[float]:
    """COM Font.Size → float. 혼합값(9999999/wdUndefined)은 None."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f <= 0 or f > 1638:  # Word 글꼴 최대 1638pt; 그 이상은 wdUndefined 표식
        return None
    return f


def _safe(fn):  # pragma: no cover - COM 의존
    """COM 속성 접근 중 예외를 흡수해 None 으로 폴백."""
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return None
