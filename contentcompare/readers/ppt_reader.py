"""PowerPoint 리더 (win32com).

사내 nasca 이슈로 python-pptx 대신 win32com(설치된 PowerPoint) 을 사용한다.
슬라이드별 도형(텍스트 프레임)·표 셀의 텍스트를 추출한다.

win32com 은 Windows + PowerPoint 설치가 필요하므로 import 를 ``read()`` 시점으로 지연한다.
"""

from __future__ import annotations

import os

from ..models import DocItem, DocType


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
        items: list[DocItem] = []

        ppt = win32.Dispatch("PowerPoint.Application")
        try:
            # WithWindow=False 로 백그라운드 오픈.
            pres = ppt.Presentations.Open(
                os.path.abspath(path), ReadOnly=True, WithWindow=False
            )
            try:
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
            finally:
                pres.Close()
        finally:
            ppt.Quit()
        return items

    @staticmethod
    def _shape_text(shape) -> str:
        try:
            if shape.HasTextFrame and shape.TextFrame.HasText:
                return (shape.TextFrame.TextRange.Text or "").strip()
        except Exception:  # pragma: no cover - COM 변동성
            pass
        return ""
