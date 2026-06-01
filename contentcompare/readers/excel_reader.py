"""엑셀 리더 (xlwings) — 엑셀 hybrid 분해.

기획: 비교는 **사실 검증**이다. 엑셀의 각 행은 하나의 레코드(검색 단위)이고,
각 셀은 하나의 주장(:class:`FieldClaim`, 판정 단위)이다.

- ``granularity=hybrid`` : 행 → :class:`RecordItem` + 셀별 :class:`FieldClaim` (기본)
- ``granularity=field``  : 셀마다 독립 :class:`RecordItem` (키 문맥 포함)
- ``granularity=row``    : 행 전체를 단일 :class:`DocItem` 으로(필드 분해 없음)

설계상 xlwings 호출(COM I/O)과 순수 파싱 로직을 분리한다. COM 으로 시트를
:class:`SheetGrid`(2D 값 + 표시문자 + 시작좌표)로 뽑은 뒤, 파싱은
:meth:`ExcelReader._parse_sheet` 가 담당한다 → Excel 없이 단위테스트 가능.

xlwings 는 Windows + Excel 설치가 필요하므로 import 는 :meth:`read` 시점으로 지연한다.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import ExcelConfig
from ..models import DocItem, DocType, FieldClaim, RecordItem

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 값 정규화 헬퍼
# --------------------------------------------------------------------------- #
_CURRENCY = "₩$€¥£"
# 통화기호(선택) + 숫자(천단위 콤마 허용) + 퍼센트/짧은 단위(선택) 형태를 인식.
_NUM_RE = re.compile(
    r"^\s*[" + _CURRENCY + r"]?\s*"
    r"([+-]?\d[\d,]*(?:\.\d+)?)"
    r"\s*(%|[가-힣A-Za-z]{1,3})?\s*$"
)


def normalize_value(raw: Any) -> str:
    """비교용 정규화 문자열을 만든다.

    - 천단위 콤마/통화기호/주변 공백 제거.
    - 퍼센트와 짧은 단위(억, 만, 명 등)는 **의미를 바꾸므로 보존**한다.
    - 숫자로 인식되지 않으면 공백만 정돈한 원문을 반환한다.

    예) ``"₩1,200,000"`` → ``"1200000"``, ``"12.50%"`` → ``"12.5%"``,
        ``"100 억원"`` → ``"100억원"``, ``"제품 A"`` → ``"제품 A"``.
    """
    s = " ".join(str(raw).split())
    if not s:
        return ""
    m = _NUM_RE.match(s)
    if not m:
        return s
    num = m.group(1).replace(",", "")
    if "." in num:
        num = num.rstrip("0").rstrip(".")
    unit = m.group(2) or ""
    if unit == "%":
        return num + "%"
    return num + unit  # 단위 없으면 unit="" → 순수 숫자


def _is_numeric(text: str) -> bool:
    """천단위 콤마/소수점을 감안해 순수 숫자인지 판단."""
    if not text:
        return False
    try:
        float(text.replace(",", ""))
        return True
    except ValueError:
        return False


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _col_letter(col: int) -> str:
    """1-based 컬럼 번호 → 엑셀 열 문자(A, B, ..., AA)."""
    letters = ""
    while col > 0:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


# --------------------------------------------------------------------------- #
# 시트 그리드 (COM I/O 와 파싱 경계)
# --------------------------------------------------------------------------- #
@dataclass
class SheetGrid:
    """시트 1장의 원시 데이터. 파싱 입력으로 사용(테스트 시 직접 주입)."""

    name: str
    values: list[list[Any]]
    displays: Optional[list[list[Any]]] = None
    """서식 적용 표시문자 그리드(value_as_displayed 용). None 이면 values 사용."""
    first_row: int = 1
    """used_range 의 시작 행(1-based, 절대)."""
    first_col: int = 1
    """used_range 의 시작 열(1-based, 절대)."""

    def display_at(self, r: int, c: int) -> Any:
        """그리드 인덱스(r,c) 의 표시값(없으면 원시값)."""
        if self.displays is not None and r < len(self.displays):
            row = self.displays[r]
            if c < len(row):
                return row[c]
        return self.values[r][c] if r < len(self.values) and c < len(self.values[r]) else None


# --------------------------------------------------------------------------- #
# 리더
# --------------------------------------------------------------------------- #
class ExcelReader:
    def __init__(self, config: ExcelConfig) -> None:
        self.config = config

    # ------------------------------ COM I/O ------------------------------- #
    def read(self, path: str) -> list[DocItem]:
        try:
            import xlwings as xw
        except ImportError as exc:  # pragma: no cover - 환경 의존
            raise RuntimeError(
                "xlwings 가 필요합니다(Windows + Excel). pip install xlwings"
            ) from exc

        doc_id = os.path.basename(path)
        items: list[DocItem] = []

        # 워커 스레드(예: Streamlit)에서 COM 을 쓰려면 스레드별 초기화가 필요하다.
        try:
            import pythoncom

            pythoncom.CoInitialize()
        except Exception:  # noqa: BLE001 - 비윈도우/이미 초기화 등
            pythoncom = None

        logger.info("[Excel] 열기 시작: %s", os.path.abspath(path))
        app = xw.App(visible=False, add_book=False)
        book = None
        try:
            book = app.books.open(path)
            logger.info("[Excel] 열림: 시트 %d개", len(book.sheets))
            for sheet in book.sheets:
                grid = self._extract_grid(sheet)
                if grid is not None:
                    items.extend(self._parse_sheet(grid, doc_id))
            logger.info("[Excel] 추출 완료: %d개 항목", len(items))
        except Exception:
            logger.exception("[Excel] 처리 실패: %s", path)
            raise
        finally:
            # 예외가 나도 COM 리소스가 새지 않도록 close → quit 을 보장(정리 실패는 경고만).
            try:
                if book is not None:
                    book.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[Excel] book.close 실패(무시): %s", exc)
            try:
                app.quit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[Excel] app.quit 실패(무시): %s", exc)
            if pythoncom is not None:
                try:
                    pythoncom.CoUninitialize()
                except Exception:  # noqa: BLE001
                    pass
        return items

    def _extract_grid(self, sheet) -> Optional["SheetGrid"]:  # pragma: no cover - COM 의존
        """xlwings 시트 → SheetGrid. 표시문자/병합셀은 best-effort 로 처리."""
        used = sheet.used_range
        values = self._as_grid(used.value)
        if not values:
            return None
        first_row = int(used.row)
        first_col = int(used.column)

        displays: Optional[list[list[Any]]] = None
        if self.config.value_as_displayed:
            displays = self._read_displays(sheet, first_row, first_col, values)

        self._fill_merged(sheet, values, first_row, first_col)
        return SheetGrid(
            name=sheet.name,
            values=values,
            displays=displays,
            first_row=first_row,
            first_col=first_col,
        )

    @staticmethod
    def _as_grid(value: Any) -> list[list[Any]]:
        """used_range.value(단일/1D/2D) 를 항상 2D 리스트로 정규화."""
        if value is None:
            return []
        if not isinstance(value, list):
            return [[value]]
        if value and not isinstance(value[0], list):
            return [value]
        return value

    @staticmethod
    def _read_displays(sheet, first_row, first_col, values):  # pragma: no cover - COM 의존
        """셀별 서식 표시문자를 그리드로 읽는다(best-effort, 실패 시 None)."""
        try:
            grid: list[list[Any]] = []
            for r, row in enumerate(values):
                out_row: list[Any] = []
                for c in range(len(row)):
                    cell = sheet.range((first_row + r, first_col + c))
                    out_row.append(cell.api.Text)
                grid.append(out_row)
            return grid
        except Exception:  # noqa: BLE001 - 표시문자는 부가기능, 실패시 원시값으로 폴백
            return None

    @staticmethod
    def _fill_merged(sheet, values, first_row, first_col):  # pragma: no cover - COM 의존
        """병합셀의 좌상단 값을 병합영역 전체에 채워 넣는다(best-effort)."""
        try:
            for r, row in enumerate(values):
                for c in range(len(row)):
                    if row[c] is not None:
                        continue
                    cell = sheet.range((first_row + r, first_col + c))
                    if not cell.api.MergeCells:
                        continue
                    area = cell.api.MergeArea
                    tl = sheet.range((area.Row, area.Column))
                    values[r][c] = tl.value
        except Exception:  # noqa: BLE001 - 병합처리는 부가기능
            pass

    # ------------------------------ 파싱 --------------------------------- #
    def _parse_sheet(self, grid: SheetGrid, doc_id: str) -> list[DocItem]:
        """SheetGrid → 항목 리스트. granularity 에 따라 분해 방식이 달라진다."""
        cfg = self.config
        ncols = max((len(r) for r in grid.values), default=0)

        # 상위 행 미리보기 로그 — 엑셀을 어떻게 인식하는지 확인용.
        preview = min(8, len(grid.values))
        logger.info("[Excel] 시트 '%s' 상위 %d행 미리보기 (ncols=%d):", grid.name, preview, ncols)
        for r in range(preview):
            cells = [_cell_text(v) for v in grid.values[r][:12]]
            logger.info("  행 %d: %s", grid.first_row + r, cells)

        # 헤더/본문 인덱스를 그리드 시작행 기준 상대 인덱스로 환산.
        header_start = cfg.header_row - grid.first_row
        if header_start < 0 or len(grid.values) <= header_start:
            logger.warning("[Excel] header_row=%d 가 데이터 범위를 벗어남", cfg.header_row)
            return []

        # '대외비' 같은 전열 통합 배너행은 헤더가 아니므로 건너뛴다.
        if cfg.skip_banner_rows:
            header_start = self._skip_banner_rows(grid, header_start, ncols)

        header_end = header_start + max(1, cfg.header_rows)
        headers = self._build_headers(grid.values[header_start:header_end], ncols)
        logger.info(
            "[Excel] 헤더 행 %d~%d → 결합 헤더: %s",
            grid.first_row + header_start, grid.first_row + header_end - 1, headers,
        )

        body = grid.values[header_end:]
        body_offset = header_end  # 그리드 상대 → 절대행 환산용
        if cfg.max_rows is not None:
            body = body[: cfg.max_rows]

        key_idx, compare_idx = self._resolve_columns(headers, ncols, body)
        logger.info(
            "[Excel] 키 컬럼=%s, 비교 컬럼=%s",
            [headers[i] or f"col{i+1}" for i in key_idx],
            [headers[i] or f"col{i+1}" for i in compare_idx],
        )

        items: list[DocItem] = []
        for r_off, row in enumerate(body):
            grid_r = body_offset + r_off
            abs_row = grid.first_row + grid_r
            item = self._build_record(grid, doc_id, headers, key_idx, compare_idx, row, grid_r, abs_row)
            if item is None:
                continue
            if cfg.granularity == "row":
                items.append(self._as_docitem(item))
            elif cfg.granularity == "field":
                items.extend(self._split_fields(item))
            else:  # hybrid
                items.append(item)
        return items

    def _skip_banner_rows(self, grid: "SheetGrid", start: int, ncols: int) -> int:
        """전열(全列) 통합 배너행(예: '대외비')을 건너뛴 헤더 시작 인덱스를 반환.

        배너 = 모든 열이 채워져 있고 그 값이 전부 동일한 행(통합셀이 전체로 퍼진 경우).
        멀티헤더의 상위행(예: 일부 열만 '정량규격')은 빈 열이 있어 배너로 보지 않는다.
        """
        r = start
        while r < len(grid.values):
            row = grid.values[r]
            texts = [_cell_text(row[c]) if c < len(row) else "" for c in range(ncols)]
            nonempty = [t for t in texts if t]
            if ncols >= 2 and len(nonempty) == ncols and len(set(nonempty)) == 1:
                logger.info(
                    "[Excel] 배너행 건너뜀 (행 %d, 값='%s')",
                    grid.first_row + r, nonempty[0],
                )
                r += 1
                continue
            break
        return r

    def _build_headers(self, header_rows: list[list[Any]], ncols: int) -> list[str]:
        """다단 헤더를 컬럼별 단일 라벨로 결합. 가로 병합은 좌측값으로 전파."""
        if not header_rows:
            return ["" for _ in range(ncols)]
        # 각 헤더 행을 가로 forward-fill(병합 라벨 전파) — 다단 헤더일 때만.
        multi = len(header_rows) > 1
        filled: list[list[str]] = []
        for hr in header_rows:
            row_out: list[str] = []
            last = ""
            for c in range(ncols):
                text = _cell_text(hr[c]) if c < len(hr) else ""
                if multi and not text:
                    text = last
                else:
                    last = text or last
                row_out.append(text)
            filled.append(row_out)

        headers: list[str] = []
        for c in range(ncols):
            parts = [filled[r][c] for r in range(len(filled)) if filled[r][c]]
            # 상하 중복 제거하며 ">" 결합.
            seen: list[str] = []
            for p in parts:
                if not seen or seen[-1] != p:
                    seen.append(p)
            headers.append(">".join(seen))
        return headers

    def _resolve_columns(self, headers, ncols, body):
        """키/비교 컬럼 인덱스(0-based)를 확정."""
        cfg = self.config
        skip = set(self._to_indices(cfg.skip_columns, headers, ncols))

        key_idx = self._to_indices(cfg.key_columns, headers, ncols)
        if not key_idx:
            key_idx = self._infer_key(headers, body, ncols, skip)
        key_set = set(key_idx)

        if cfg.compare_columns is None:
            compare_idx = [c for c in range(ncols) if c not in key_set and c not in skip]
        else:
            compare_idx = [
                c for c in self._to_indices(cfg.compare_columns, headers, ncols)
                if c not in skip
            ]
        return key_idx, compare_idx

    @staticmethod
    def _to_indices(spec, headers, ncols) -> list[int]:
        """헤더명 또는 1-based 인덱스 목록 → 0-based 인덱스 목록."""
        out: list[int] = []
        for s in spec or []:
            if isinstance(s, bool):  # bool 은 int 의 하위형이므로 먼저 차단
                continue
            if isinstance(s, int):
                i = s - 1
                if 0 <= i < ncols and i not in out:
                    out.append(i)
                continue
            name = str(s).strip().lower()
            for i, h in enumerate(headers):
                if h.strip().lower() == name and i not in out:
                    out.append(i)
                    break
        return out

    @staticmethod
    def _infer_key(headers, body, ncols, skip) -> list[int]:
        """키 미지정 시: 본문이 대체로 '텍스트'인 첫 컬럼을 키로 추정."""
        for c in range(ncols):
            if c in skip:
                continue
            nonempty = textish = 0
            for row in body:
                t = _cell_text(row[c]) if c < len(row) else ""
                if not t:
                    continue
                nonempty += 1
                if not _is_numeric(t):
                    textish += 1
            if nonempty and textish / nonempty > 0.5:
                return [c]
        # 폴백: 스킵되지 않은 첫 컬럼.
        for c in range(ncols):
            if c not in skip:
                return [c]
        return []

    def _build_record(self, grid, doc_id, headers, key_idx, compare_idx, row, grid_r, abs_row) -> Optional[RecordItem]:
        """본문 한 행 → RecordItem(키문맥 + 필드들). 빈 행이면 None."""
        def disp(c: int) -> str:
            return _cell_text(grid.display_at(grid_r, c))

        def header_of(c: int) -> str:
            h = headers[c] if c < len(headers) else ""
            return h or f"col{c + 1}"

        # 키 문맥.
        key_parts = [f"{header_of(c)}={disp(c)}" for c in key_idx if disp(c)]
        key_context = "[" + ", ".join(key_parts) + "]" if key_parts else ""

        # 필드(주장)들.
        fields: list[FieldClaim] = []
        for c in compare_idx:
            text = disp(c)
            if not text:
                continue
            cell_ref = f"{_col_letter(grid.first_col + c)}{abs_row}"
            raw_val = row[c] if c < len(row) else None
            fields.append(
                FieldClaim(
                    field_id=f"{doc_id}#{grid.name}!{cell_ref}",
                    header=header_of(c),
                    value_raw=raw_val,
                    value_norm=normalize_value(text),
                    cell_ref=cell_ref,
                )
            )

        # 검색용 텍스트: 모든 비어있지 않은 셀을 "헤더=값" 으로 결합.
        text_parts: list[str] = []
        for c in range(len(headers)):
            t = disp(c)
            if t:
                text_parts.append(f"{header_of(c)}={t}")
        text = " | ".join(text_parts)
        if not text.strip():
            return None

        sheet = grid.name
        label = f"{doc_id} > [{sheet}] {abs_row}행"
        if key_context:
            label += f" {key_context}"
        return RecordItem(
            item_id=f"{doc_id}#{sheet}!{abs_row}",
            doc_id=doc_id,
            doc_type=DocType.EXCEL,
            text=text,
            source_label=label,
            locator={"sheet": sheet, "row": abs_row},
            raw={"headers": headers, "values": list(row)},
            key_context=key_context,
            fields=fields,
        )

    @staticmethod
    def _as_docitem(rec: RecordItem) -> DocItem:
        """granularity=row 용: 필드 정보를 버린 순수 DocItem."""
        return DocItem(
            item_id=rec.item_id,
            doc_id=rec.doc_id,
            doc_type=rec.doc_type,
            text=rec.text,
            source_label=rec.source_label,
            locator=rec.locator,
            raw=rec.raw,
        )

    @staticmethod
    def _split_fields(rec: RecordItem) -> list[RecordItem]:
        """granularity=field 용: 셀마다 독립 항목(키 문맥 포함)으로 분리."""
        out: list[RecordItem] = []
        for fc in rec.fields:
            ctx = f"{rec.key_context} " if rec.key_context else ""
            out.append(
                RecordItem(
                    item_id=fc.field_id,
                    doc_id=rec.doc_id,
                    doc_type=rec.doc_type,
                    text=f"{ctx}{fc.header}={_cell_text(fc.value_raw)}".strip(),
                    source_label=f"{rec.source_label} · {fc.header}",
                    locator={**rec.locator, "cell": fc.cell_ref},
                    raw={"header": fc.header, "value": fc.value_raw},
                    key_context=rec.key_context,
                    fields=[fc],
                )
            )
        return out
