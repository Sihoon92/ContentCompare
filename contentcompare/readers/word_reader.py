"""Word 리더 (win32com).

사내 nasca 이슈로 python-docx 대신 win32com(설치된 Word) 을 사용한다.

청킹 단위(의미 단위):
  - **표(table)** : 한 row = 한 항목(셀들을 ``a | b | c`` 로 결합). 단일 의미 단위.
    세로 병합 셀이 있어 ``Table.Rows`` 접근이 막히면 ``Range.Cells`` 의 행/열 인덱스로
    격자를 복원하고, 병합으로 빠진 칸은 위 값으로 전파한다.
  - **산문(prose)** : ¶(Enter) 단위가 아니라 **제목(Heading/개요수준) 섹션** 단위로 묶는다.
    한 제목 아래 본문 전체가 하나의 항목이 된다(제목 없으면 문서 전체가 한 섹션 → 길면
    chunker 가 문장 경계로 분할). 이렇게 해야 유사 내용이 한 청크로 모여 top-k 가 의미 있다.

설계상 COM I/O 와 순수 파싱을 분리한다(엑셀 리더와 동일 방침): COM 으로 문단/표를
:class:`_Para`/:class:`_Table` 로 뽑은 뒤, :meth:`_build_items` 가 의미 단위로 조립한다
→ Word 없이 단위테스트 가능.

win32com 은 Windows + Word 설치가 필요하므로 import 를 ``read()`` 시점으로 지연한다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from ..models import DocItem, DocType
from . import com_util

logger = logging.getLogger(__name__)

# Word 의 OutlineLevel: 1~9 = 제목 수준, 10 = 본문(wdOutlineLevelBodyText).
_BODY_LEVEL = 10


@dataclass
class _Para:
    """문단 하나(표 밖). 파싱 입력으로 사용(테스트 시 직접 주입)."""

    text: str
    outline_level: int = _BODY_LEVEL  # 1~9 제목, 10 본문
    page: Optional[int] = None


@dataclass
class _Table:
    """표 하나. rows 는 행별 셀 텍스트 리스트."""

    rows: list[list[str]] = field(default_factory=list)
    page: Optional[int] = None


def _is_heading(level: int) -> bool:
    return 1 <= level <= 9


def _clean_cell_text(raw: str) -> str:
    """Word 셀 텍스트의 끝 표식(\\r, \\x07=cell mark)을 정리한다."""
    return (raw or "").replace("\r", " ").replace("\x07", " ").strip()


def _rows_from_indexed_cells(indexed: list[tuple[int, int, str]]) -> list[list[str]]:
    """(행번호, 열번호, 텍스트) 셀 목록을 격자(행렬)로 복원한다.

    병합으로 어떤 칸의 셀이 빠져 있으면(=그 좌표가 목록에 없으면) 세로 병합으로 보고
    **같은 열의 바로 위 값**을 전파한다(엑셀 병합 채우기와 동일). 가로 병합은 시작 열에만
    값이 있고 나머지 열은 빈칸으로 둔다(과도한 복제 방지).
    """
    if not indexed:
        return []
    by_rc: dict[tuple[int, int], str] = {}
    rows: set[int] = set()
    cols: set[int] = set()
    for r, c, t in indexed:
        by_rc[(r, c)] = t
        rows.add(r)
        cols.add(c)

    grid: list[list[str]] = []
    last_by_col: dict[int, str] = {}
    for r in sorted(rows):
        row_cells: list[str] = []
        for c in sorted(cols):
            if (r, c) in by_rc:
                val = by_rc[(r, c)]
                last_by_col[c] = val          # 이후 행의 세로 병합 전파용
            else:
                val = last_by_col.get(c, "")  # 세로 병합으로 빠진 칸 → 위 값 전파
            row_cells.append(val)
        grid.append(row_cells)
    return grid


class WordReader:
    # ------------------------------ COM I/O ------------------------------- #
    def read(self, path: str) -> list[DocItem]:
        try:
            import pythoncom  # noqa: F401
            import win32com.client as win32
        except ImportError as exc:  # pragma: no cover - 환경 의존
            raise RuntimeError(
                "pywin32 가 필요합니다(Windows + Word). pip install pywin32"
            ) from exc

        doc_id = os.path.basename(path)
        abspath = os.path.abspath(path)

        logger.info("[Word] 열기 시작: %s", abspath)
        # Streamlit 등 워커 스레드에서 COM 을 쓰려면 스레드별 초기화가 필요하다.
        pythoncom.CoInitialize()
        word = None
        doc = None
        try:
            word = win32.DispatchEx("Word.Application")
            com_util.track("word", word)
            word.Visible = False
            try:
                word.DisplayAlerts = False  # 일부 환경에서 속성 없음 가능
            except Exception:  # noqa: BLE001
                pass

            # Open(FileName, ConfirmConversions=False, ReadOnly=True) — 위치 인자.
            doc = word.Documents.Open(abspath, False, True)
            logger.info("[Word] 문서 열림: 단락 %s개, 표 %s개", doc.Paragraphs.Count, doc.Tables.Count)

            paras = self._extract_paras(doc)
            tables = self._extract_tables(doc)
            items = self._build_items(doc_id, paras, tables)
            logger.info(
                "[Word] 추출 완료: %d개 항목(섹션/표행). 문단 %d, 표 %d",
                len(items), len(paras), len(tables),
            )
            return items
        except Exception:
            logger.exception("[Word] 처리 실패: %s", abspath)
            raise
        finally:
            if doc is not None:
                try:
                    doc.Close(False)  # SaveChanges=False
                except Exception as exc:  # noqa: BLE001 - 정리 실패는 경고만
                    logger.warning("[Word] doc.Close 실패(무시): %s", exc)
            com_util.close_app("word", word)
            try:
                pythoncom.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _extract_paras(doc) -> list["_Para"]:  # pragma: no cover - COM 의존
        """표 밖 문단들을 (텍스트/개요수준/페이지)로 추출."""
        out: list[_Para] = []
        for para in doc.Paragraphs:
            rng = para.Range
            try:
                if rng.Information(12):  # wdWithInTable → 표는 _extract_tables 가 담당
                    continue
            except Exception:  # noqa: BLE001
                pass
            text = (rng.Text or "").strip()
            if not text:
                continue
            try:
                level = int(para.OutlineLevel)
            except Exception:  # noqa: BLE001
                level = _BODY_LEVEL
            try:
                page = rng.Information(3)  # wdActiveEndPageNumber
            except Exception:  # noqa: BLE001
                page = None
            out.append(_Para(text=text, outline_level=level, page=page))
        return out

    @staticmethod
    def _extract_tables(doc) -> list["_Table"]:  # pragma: no cover - COM 의존
        """표를 행별 셀 텍스트로 추출. 세로 병합 표는 셀 인덱스로 격자를 복원."""
        out: list[_Table] = []
        for table in doc.Tables:
            rows = WordReader._extract_table_rows(table)
            try:
                page = table.Range.Information(3)
            except Exception:  # noqa: BLE001
                page = None
            out.append(_Table(rows=rows, page=page))
        return out

    @staticmethod
    def _extract_table_rows(table) -> list[list[str]]:  # pragma: no cover - COM 의존
        """한 표의 행렬 텍스트를 얻는다. Rows 접근이 막히면 Range.Cells 로 복원."""
        # 1) 일반 경로: 행→셀. 세로 병합이 없으면 구조 그대로 가장 정확.
        try:
            rows: list[list[str]] = []
            for row in table.Rows:
                rows.append([WordReader._cell_text(cell) for cell in row.Cells])
            return rows
        except Exception as exc:  # noqa: BLE001 - 세로 병합 시 Rows 접근 불가
            logger.info("[Word] 표 행 접근 불가(병합셀 추정) → 셀 인덱스로 재구성: %s", exc)

        # 2) 폴백: 병합이 있어도 Range.Cells 는 순회 가능. RowIndex/ColumnIndex 로 복원.
        indexed: list[tuple[int, int, str]] = []
        try:
            for cell in table.Range.Cells:
                try:
                    r = int(cell.RowIndex)
                    c = int(cell.ColumnIndex)
                except Exception:  # noqa: BLE001 - 인덱스 접근 실패 셀은 건너뜀
                    continue
                indexed.append((r, c, WordReader._cell_text(cell)))
        except Exception as exc:  # noqa: BLE001 - 그래도 실패하면 이 표만 포기
            logger.warning("[Word] 표 셀 재구성 실패(이 표 건너뜀): %s", exc)
            return []
        return _rows_from_indexed_cells(indexed)

    @staticmethod
    def _cell_text(cell) -> str:  # pragma: no cover - COM 의존
        try:
            raw = cell.Range.Text or ""
        except Exception:  # noqa: BLE001 - 병합셀 접근 오류 등
            return ""
        return _clean_cell_text(raw)

    # ------------------------------ 파싱(순수) ---------------------------- #
    @staticmethod
    def _build_items(doc_id: str, paras: list["_Para"], tables: list["_Table"]) -> list[DocItem]:
        """문단(→제목 섹션)과 표(→row)를 의미 단위 DocItem 으로 조립."""
        items = WordReader._section_items(doc_id, paras)
        items.extend(WordReader._table_items(doc_id, tables))
        return items

    @staticmethod
    def _section_items(doc_id: str, paras: list["_Para"]) -> list[DocItem]:
        """문단들을 제목(Heading) 경계로 묶어 섹션 단위 항목으로 만든다."""
        items: list[DocItem] = []
        title = ""
        body: list[_Para] = []
        seq = 0  # 섹션 일련번호

        def flush() -> None:
            nonlocal title, body, seq
            parts = ([title] if title else []) + [p.text for p in body]
            text = "\n".join(parts).strip()
            if text:
                seq += 1
                page = next((p.page for p in body if p.page), None)
                if page is None and body == [] and title:
                    page = None
                page_label = f"{page}페이지 > " if page else ""
                loc = title if title else f"{seq}번째 구역"
                items.append(
                    DocItem(
                        item_id=f"{doc_id}#sec{seq}",
                        doc_id=doc_id,
                        doc_type=DocType.WORD,
                        text=text,
                        source_label=f"{doc_id} > {page_label}{loc}",
                        locator={"section": seq, "heading": title, "page": page},
                    )
                )
            title, body = "", []

        for p in paras:
            if _is_heading(p.outline_level):
                flush()           # 새 제목 → 직전 섹션 마감
                title = p.text
            else:
                body.append(p)
        flush()
        return items

    @staticmethod
    def _table_items(doc_id: str, tables: list["_Table"]) -> list[DocItem]:
        """표의 각 행을 단일 의미 단위(항목)로 만든다."""
        items: list[DocItem] = []
        for ti, table in enumerate(tables, start=1):
            for ri, row in enumerate(table.rows, start=1):
                cells = [c.strip() for c in row if c and c.strip()]
                if not cells:
                    continue
                text = " | ".join(cells)
                page = table.page
                page_label = f"{page}페이지 > " if page else ""
                items.append(
                    DocItem(
                        item_id=f"{doc_id}#t{ti}r{ri}",
                        doc_id=doc_id,
                        doc_type=DocType.WORD,
                        text=text,
                        source_label=f"{doc_id} > {page_label}표{ti} {ri}행",
                        locator={"table": ti, "row": ri, "page": page},
                    )
                )
        return items
