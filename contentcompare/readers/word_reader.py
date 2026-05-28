"""Word 리더 (win32com).

사내 nasca 이슈로 python-docx 대신 win32com(설치된 Word) 을 사용한다.
단락과 표 셀을 텍스트 단위로 추출한다.

win32com 은 Windows + Word 설치가 필요하므로 import 를 ``read()`` 시점으로 지연한다.
"""

from __future__ import annotations

import os

from ..models import DocItem, DocType


class WordReader:
    def read(self, path: str) -> list[DocItem]:
        try:
            import pythoncom  # noqa: F401
            import win32com.client as win32
        except ImportError as exc:  # pragma: no cover - 환경 의존
            raise RuntimeError(
                "pywin32 가 필요합니다(Windows + Word). pip install pywin32"
            ) from exc

        doc_id = os.path.basename(path)
        items: list[DocItem] = []

        word = win32.Dispatch("Word.Application")
        word.Visible = False
        try:
            doc = word.Documents.Open(os.path.abspath(path), ReadOnly=True)
            try:
                for idx, para in enumerate(doc.Paragraphs, start=1):
                    text = (para.Range.Text or "").strip()
                    if not text:
                        continue
                    # 페이지 번호는 wdActiveEndPageNumber(=3) 정보로 얻는다.
                    try:
                        page = para.Range.Information(3)
                    except Exception:  # pragma: no cover - COM 변동성
                        page = None
                    page_label = f"{page}페이지 > " if page else ""
                    items.append(
                        DocItem(
                            item_id=f"{doc_id}#p{idx}",
                            doc_id=doc_id,
                            doc_type=DocType.WORD,
                            text=text,
                            source_label=f"{doc_id} > {page_label}{idx}번째 단락",
                            locator={"paragraph": idx, "page": page},
                        )
                    )
            finally:
                doc.Close(SaveChanges=False)
        finally:
            word.Quit()
        return items
