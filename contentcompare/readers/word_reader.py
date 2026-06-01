"""Word 리더 (win32com).

사내 nasca 이슈로 python-docx 대신 win32com(설치된 Word) 을 사용한다.
단락과 표 셀을 텍스트 단위로 추출한다.

win32com 은 Windows + Word 설치가 필요하므로 import 를 ``read()`` 시점으로 지연한다.
COM 정리(Close/Quit)는 try/except 로 감싸 **진짜 오류(문서 열기 실패 등)가 정리 단계
오류에 가려지지 않도록** 한다. 각 단계는 로그로 남긴다.
"""

from __future__ import annotations

import logging
import os

from ..models import DocItem, DocType

logger = logging.getLogger(__name__)


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
        abspath = os.path.abspath(path)
        items: list[DocItem] = []

        logger.info("[Word] 열기 시작: %s", abspath)
        # Streamlit 등 워커 스레드에서 COM 을 쓰려면 스레드별 초기화가 필요하다.
        pythoncom.CoInitialize()
        word = None
        doc = None
        try:
            # 독립 인스턴스(DispatchEx)로 다른 Word 세션과 충돌 회피.
            word = win32.DispatchEx("Word.Application")
            word.Visible = False
            try:
                word.DisplayAlerts = False  # 일부 환경에서 속성 없음 가능
            except Exception:  # noqa: BLE001
                pass

            # Open(FileName, ConfirmConversions=False, ReadOnly=True) — 위치 인자.
            doc = word.Documents.Open(abspath, False, True)
            total = doc.Paragraphs.Count
            logger.info("[Word] 문서 열림: 단락 %s개", total)

            for idx, para in enumerate(doc.Paragraphs, start=1):
                text = (para.Range.Text or "").strip()
                if not text:
                    continue
                try:
                    page = para.Range.Information(3)  # wdActiveEndPageNumber
                except Exception:  # noqa: BLE001 - COM 변동성
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
            logger.info("[Word] 추출 완료: %d개 항목", len(items))
        except Exception:
            logger.exception("[Word] 처리 실패: %s", abspath)
            raise
        finally:
            if doc is not None:
                try:
                    doc.Close(False)  # SaveChanges=False
                except Exception as exc:  # noqa: BLE001 - 정리 실패는 경고만
                    logger.warning("[Word] doc.Close 실패(무시): %s", exc)
            if word is not None:
                try:
                    word.Quit()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[Word] word.Quit 실패(무시): %s", exc)
            try:
                pythoncom.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass
        return items
