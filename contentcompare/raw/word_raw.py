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


# wdHorizontalPositionRelativeToPage — 셀 가로 위치(pt)를 얻는 Information 상수.
_WD_HPOS_REL_PAGE = 5


def _read_table(table) -> list[list[str]]:  # pragma: no cover - COM 의존
    """Word 표 → 셀 텍스트 2D(병합 셀은 병합된 모든 칸에 동일 값을 채움).

    ``table.Rows`` 로 순회하면 **세로 병합 셀이 있는 표에서** Word 가
    ``wdCannotAccessIndividualRows`` (\"셀이 세로로 병합되어 있기 때문에 컬렉션에서
    개별 행을 액세스할 수 없습니다\") 에러를 던진다. 그래서 행 컬렉션을 건드리지
    않는 ``table.Range.Cells`` 로 전체 셀을 훑는다.

    각 셀에서 다음을 읽는다(셀 끝 제어문자 ``\\r\\x07`` 제거):

    - ``RowIndex`` : 행 위치(가로 병합에 영향받지 않아 신뢰 가능)
    - 가로 위치(``Information(5)``) + ``Width`` : 컬럼 위치/너비. 병합 셀의 Width 는
      합쳐진 값이라 **가로 span** 을 정확히 알 수 있다.

    기하 정보로 컬럼 경계를 재구성하면(:func:`_grid_from_geometry`) 가로 병합은
    span 만큼 같은 값을 채우고, 세로 병합은 빈 칸(구멍)을 위 값으로 채운다. 기하
    정보를 못 읽으면 인덱스 기반(:func:`_grid_from_cells`, 세로만)으로 폴백한다.
    """
    geom: list[tuple[int, float, float, str]] = []  # (row, left, width, text)
    placed: list[tuple[int, int, str]] = []  # (row, col, text) — 폴백용
    geom_ok = True
    for cell in table.Range.Cells:
        r = _safe(lambda: int(cell.RowIndex))
        c = _safe(lambda: int(cell.ColumnIndex))
        text = _safe(lambda: cell.Range.Text) or ""
        text = text.replace("\r", " ").replace("\x07", "").strip()
        left = _safe(lambda: float(cell.Range.Information(_WD_HPOS_REL_PAGE)))
        width = _safe(lambda: float(cell.Width))
        if r is not None and c is not None:
            placed.append((r, c, text))
        if r is None or left is None or width is None or width <= 0:
            geom_ok = False
        else:
            geom.append((r, left, width, text))

    if geom_ok and geom:
        return _grid_from_geometry(geom)
    return _grid_from_cells(placed)


# --------------------------------------------------------------------------- #
# 순수 격자 빌더 (Word 불필요 — 테스트 진입점)
# --------------------------------------------------------------------------- #
def _fill_vertical_holes(grid: list[list[str]], present: list[list[bool]]) -> None:
    """세로 병합 처리: '구멍'(셀이 없던 칸)을 바로 위 칸의 값으로 채운다(in-place).

    위→아래로 처리해 3행 이상 병합도 연쇄 전파된다. 실제 빈 셀(present=True, 값
    ``""``)은 구멍이 아니므로 건드리지 않는다.
    """
    for r in range(1, len(grid)):
        for c in range(len(grid[r])):
            if not present[r][c] and grid[r - 1][c]:
                grid[r][c] = grid[r - 1][c]


def _column_edges(lefts: list[float], tol: float) -> list[float]:
    """셀들의 가로 위치 목록 → 오름차순 컬럼 시작 경계(tol 이내는 같은 컬럼)."""
    edges: list[float] = []
    for x in sorted(lefts):
        if not edges or x - edges[-1] > tol:
            edges.append(x)
    return edges


def _grid_from_geometry(
    geom: list[tuple[int, float, float, str]], *, tol: float = 3.0
) -> list[list[str]]:
    """(row, left, width, text) 목록 → 2D 격자. 가로/세로 병합을 모두 채운다.

    1. 셀들의 ``left`` 로 컬럼 경계를 만든다(:func:`_column_edges`).
    2. 각 셀의 컬럼 시작 = left 에 가장 가까운 경계, **가로 span** = ``left+width``
       범위에 들어오는 경계 수 → span 만큼 같은 값을 가로로 채운다(가로 병합).
    3. 남은 '구멍' 을 위 값으로 채운다(:func:`_fill_vertical_holes`, 세로 병합).

    Word 없이 단위테스트 가능한 순수 로직.
    """
    if not geom:
        return []
    edges = _column_edges([g[1] for g in geom], tol)
    n_cols = len(edges)
    n_rows = max(g[0] for g in geom)
    grid = [["" for _ in range(n_cols)] for _ in range(n_rows)]
    present = [[False] * n_cols for _ in range(n_rows)]

    for row, left, width, text in geom:
        r = row - 1
        if not (0 <= r < n_rows):
            continue
        # 컬럼 시작: left 에 가장 가까운 경계 인덱스.
        cstart = min(range(n_cols), key=lambda i: abs(edges[i] - left))
        # 가로 span: left+width 안에 들어오는 경계 수(최소 1).
        right = left + width
        span = 0
        for i in range(cstart, n_cols):
            if edges[i] < right - tol:
                span += 1
            else:
                break
        span = max(1, span)
        for j in range(cstart, min(cstart + span, n_cols)):
            grid[r][j] = text
            present[r][j] = True

    _fill_vertical_holes(grid, present)
    return grid


def _grid_from_cells(
    placed: list[tuple[int, int, str]], *, fill_merged: bool = True
) -> list[list[str]]:
    """(row_index, col_index, text) 목록 → 2D 격자(1-based 인덱스). 세로 병합만 채움.

    기하 정보를 못 읽었을 때의 폴백. ``table.Range.Cells`` 는 병합으로 가려진
    위치에 셀을 주지 않으므로(= '구멍'), ``fill_merged`` 가 참이면 구멍을 위 값으로
    채운다(세로 병합 전파). 실제 빈 셀(``""``)은 구멍이 아니라 건드리지 않는다.

    Word 없이 단위테스트 가능한 순수 로직.

    한계: 이 폴백 경로는 span 정보가 없어 **가로 병합은 채우지 못한다**(가로로
    가려진 구멍이 위 값으로 잘못 채워질 수도 있음). 정상 경로는 기하 기반이다.
    """
    if not placed:
        return []
    max_r = max(r for r, _, _ in placed)
    max_c = max(c for _, c, _ in placed)
    grid = [["" for _ in range(max_c)] for _ in range(max_r)]
    present = [[False] * max_c for _ in range(max_r)]
    for r, c, text in placed:
        if 1 <= r <= max_r and 1 <= c <= max_c:
            grid[r - 1][c - 1] = text
            present[r - 1][c - 1] = True

    if fill_merged:
        _fill_vertical_holes(grid, present)
    return grid


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
