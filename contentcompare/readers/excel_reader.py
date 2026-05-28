"""엑셀 리더 (xlwings).

헤더 1행을 식별한 뒤 이후 모든 row 를 **순차적으로** :class:`DocItem` 으로 변환한다(기획 2번).
각 row 는 ``헤더=값`` 조각들을 합쳐 하나의 비교 단위로 본다.

xlwings 는 Windows + Excel 설치가 필요하다. 미설치 환경을 위해 import 는 ``read()``
호출 시점으로 지연한다.
"""

from __future__ import annotations

import os

from ..config import ExcelConfig
from ..models import DocItem, DocType


class ExcelReader:
    def __init__(self, config: ExcelConfig) -> None:
        self.config = config

    def read(self, path: str) -> list[DocItem]:
        try:
            import xlwings as xw
        except ImportError as exc:  # pragma: no cover - 환경 의존
            raise RuntimeError(
                "xlwings 가 필요합니다(Windows + Excel). pip install xlwings"
            ) from exc

        doc_id = os.path.basename(path)
        items: list[DocItem] = []

        # 화면에 띄우지 않고 백그라운드로 연다.
        app = xw.App(visible=False, add_book=False)
        try:
            book = app.books.open(path)
            try:
                for sheet in book.sheets:
                    items.extend(self._read_sheet(sheet, doc_id))
            finally:
                book.close()
        finally:
            app.quit()
        return items

    def _read_sheet(self, sheet, doc_id: str) -> list[DocItem]:
        """한 시트를 읽어 row 단위 DocItem 리스트로 변환."""
        used = sheet.used_range
        values = used.value  # list[list] (또는 단일 값)
        if not isinstance(values, list):
            values = [[values]]
        if values and not isinstance(values[0], list):
            values = [values]

        header_idx = self.config.header_row - 1
        if len(values) <= header_idx:
            return []

        headers = [self._cell_text(v) for v in values[header_idx]]
        body = values[header_idx + 1 :]
        if self.config.max_rows is not None:
            body = body[: self.config.max_rows]

        items: list[DocItem] = []
        for r_offset, row in enumerate(body):
            excel_row = self.config.header_row + 1 + r_offset  # 1-based 실제 행번호
            parts: list[str] = []
            for col_idx, cell in enumerate(row):
                cell_text = self._cell_text(cell)
                if not cell_text:
                    continue
                header = headers[col_idx] if col_idx < len(headers) else f"col{col_idx + 1}"
                parts.append(f"{header}={cell_text}" if header else cell_text)
            text = " | ".join(parts)
            if not text.strip():
                continue
            items.append(
                DocItem(
                    item_id=f"{doc_id}#{sheet.name}!{excel_row}",
                    doc_id=doc_id,
                    doc_type=DocType.EXCEL,
                    text=text,
                    source_label=f"{doc_id} > [{sheet.name}] {excel_row}행",
                    locator={"sheet": sheet.name, "row": excel_row},
                    raw={"headers": headers, "values": list(row)},
                )
            )
        return items

    @staticmethod
    def _cell_text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()
