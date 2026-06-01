"""PowerPoint 리더 (win32com).

사내 nasca 이슈로 python-pptx 대신 win32com(설치된 PowerPoint) 을 사용한다.
슬라이드별 도형(텍스트 프레임)·표 셀의 텍스트를 추출한다.

win32com 은 Windows + PowerPoint 설치가 필요하므로 import 를 ``read()`` 시점으로 지연한다.
정리(Close/Quit)는 try/except 로 감싸 진짜 오류가 가려지지 않게 하고, 단계는 로그로 남긴다.
"""

from __future__ import annotations

import logging
import os

from ..models import DocItem, DocType

logger = logging.getLogger(__name__)

# PowerPoint msoTriState 상수.
_MSO_TRUE = -1
_MSO_FALSE = 0


class PptReader:
    def read(self, path: str) -> list[DocItem]:
        try:
            import pythoncom  # noqa: F401
            import win32com.client as win32
        except ImportError as exc:  # pragma: no cover - 환경 의존
            raise RuntimeError(
                "pywin32 가 필요합니다(Windows + PowerPoint). pip install pywin32"
            ) from exc

        doc_id = os.path.basename(path)
        abspath = os.path.abspath(path)
        items: list[DocItem] = []

        logger.info("[PPT] 열기 시작: %s", abspath)
        # 워커 스레드(예: Streamlit)에서 COM 을 쓰려면 스레드별 초기화가 필요하다.
        pythoncom.CoInitialize()
        ppt = None
        pres = None
        try:
            ppt = win32.DispatchEx("PowerPoint.Application")
            # Open(FileName, ReadOnly=msoTrue, Untitled=msoFalse, WithWindow=msoFalse)
            pres = ppt.Presentations.Open(abspath, _MSO_TRUE, _MSO_FALSE, _MSO_FALSE)
            total = pres.Slides.Count
            logger.info("[PPT] 열림: 슬라이드 %s개", total)

            for slide_idx, slide in enumerate(pres.Slides, start=1):
                for shape_idx, shape in enumerate(slide.Shapes, start=1):
                    text = self._shape_text(shape)
                    if not text:
                        continue
                    items.append(
                        DocItem(
                            item_id=f"{doc_id}#s{slide_idx}_sh{shape_idx}",
                            doc_id=doc_id,
                            doc_type=DocType.PPT,
                            text=text,
                            source_label=(
                                f"{doc_id} > {slide_idx}번 슬라이드 > "
                                f"{shape_idx}번째 도형"
                            ),
                            locator={"slide": slide_idx, "shape": shape_idx},
                        )
                    )
            logger.info("[PPT] 추출 완료: %d개 항목", len(items))
        except Exception:
            logger.exception("[PPT] 처리 실패: %s", abspath)
            raise
        finally:
            if pres is not None:
                try:
                    pres.Close()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[PPT] pres.Close 실패(무시): %s", exc)
            if ppt is not None:
                try:
                    ppt.Quit()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[PPT] ppt.Quit 실패(무시): %s", exc)
            try:
                pythoncom.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass
        return items

    @staticmethod
    def _shape_text(shape) -> str:
        try:
            if shape.HasTextFrame and shape.TextFrame.HasText:
                return (shape.TextFrame.TextRange.Text or "").strip()
        except Exception:  # noqa: BLE001 - COM 변동성
            pass
        return ""
