"""PPT Raw Extractor — 슬라이드의 텍스트박스/표/스피커노트를 physical_raw 로 추출.

설계(기존 ``excel_raw``/``word_raw`` 와 동일 정책):

- COM(win32com ``PowerPoint.Application``)은 :func:`extract_ppt_raw` 에서만 만진다.
  COM 객체는 즉시 순수 dataclass(:class:`SlideProbe`/:class:`ShapeProbe`)로 옮기고,
  빌더(:func:`build_ppt_doc`)는 **순수 함수**라 PowerPoint 없이 단위테스트가 된다.
- 회사 환경 제약상 ``python-pptx`` 를 쓰지 않고 win32com 으로 추출한다(Word 가
  ``python-docx`` 대신 win32com 을 쓰는 것과 동일).
- 차트/이미지/OCR 은 **추출 대상이 아니다**(텍스트박스/표/노트만). 의미 해석도 하지
  않는다 — 슬라이드 번호·도형 종류·위치·텍스트·노트 같은 "보이는 정보"만 담는다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from ..readers import com_util
from .models import RawPptDocument, RawPptShape, RawPptSlide

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Probe (COM/테스트 → 빌더 입력). COM 객체를 들고 있지 않은 순수 데이터.
# --------------------------------------------------------------------------- #
@dataclass
class ShapeProbe:
    """도형 1개. ``kind`` 가 ``text``/``table`` 이 아니면 빌더가 무시(차트/이미지 제외)."""

    kind: str
    name: Optional[str] = None
    text: Optional[str] = None
    rows: Optional[list[list[str]]] = None
    left: Optional[float] = None
    top: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    bold: Optional[bool] = None
    font_size: Optional[float] = None
    placeholder: Optional[str] = None


@dataclass
class SlideProbe:
    """슬라이드 1장."""

    slide_no: int
    layout_name: Optional[str] = None
    shapes: list[ShapeProbe] = field(default_factory=list)
    notes: Optional[str] = None


# --------------------------------------------------------------------------- #
# 순수 빌더 (PowerPoint 불필요 — 테스트 진입점)
# --------------------------------------------------------------------------- #
def build_ppt_doc(file_name: str, slides: list[SlideProbe]) -> RawPptDocument:
    """probe 리스트 → :class:`RawPptDocument`.

    빈 도형/지원외(텍스트·표가 아닌) 도형은 제외하고, 도형도 노트도 없는 슬라이드는
    생략한다. shape_id/slide_id 를 부여한다.
    """
    doc = RawPptDocument(file_name=file_name)
    for sp in slides:
        slide = RawPptSlide(
            slide_id=f"p{sp.slide_no:03d}",
            slide_no=sp.slide_no,
            layout_name=sp.layout_name or None,
            notes=_clean(sp.notes) or None,
        )
        order = 0
        for shp in sp.shapes:
            if shp.kind == "text":
                text = _clean(shp.text)
                if not text:
                    continue
                order += 1
                slide.shapes.append(
                    RawPptShape(
                        shape_id=f"{slide.slide_id}_s{order:03d}",
                        order=order,
                        type="text",
                        name=shp.name or None,
                        text=text,
                        position=_position(shp),
                        style=_style(shp),
                    )
                )
            elif shp.kind == "table":
                rows = [[_clean(c) for c in row] for row in (shp.rows or [])]
                if not rows or all(not any(r) for r in rows):
                    continue
                order += 1
                slide.shapes.append(
                    RawPptShape(
                        shape_id=f"{slide.slide_id}_s{order:03d}",
                        order=order,
                        type="table",
                        name=shp.name or None,
                        rows=rows,
                        position=_position(shp),
                    )
                )
            # 그 외(kind="chart"/"picture" 등)는 침묵 제외.
        if slide.shapes or slide.notes:
            doc.slides.append(slide)
    return doc


def _clean(s: Optional[str]) -> str:
    """공백 정돈(탭/줄바꿈 → 단일 공백). None 은 빈 문자열."""
    return " ".join((s or "").split())


def _position(shp: ShapeProbe) -> Optional[dict[str, float]]:
    """위치/크기 dict. 전부 None 이면 None(생략). 값은 소수 1자리 반올림."""
    info: dict[str, float] = {}
    for key, val in (
        ("left", shp.left),
        ("top", shp.top),
        ("width", shp.width),
        ("height", shp.height),
    ):
        if val is not None:
            info[key] = round(float(val), 1)
    return info or None


def _style(shp: ShapeProbe) -> Optional[dict[str, Any]]:
    """스타일 dict. placeholder/bold/font_size 중 있는 것만. 전부 없으면 None."""
    info: dict[str, Any] = {}
    if shp.placeholder:
        info["placeholder"] = shp.placeholder
    if shp.bold is not None:
        info["bold"] = shp.bold
    if shp.font_size is not None:
        info["font_size"] = shp.font_size
    return info or None


# --------------------------------------------------------------------------- #
# COM 진입점 (win32com PowerPoint). 텍스트박스/표/노트만 읽는다.
# --------------------------------------------------------------------------- #
# COM 상수.
_MSO_PLACEHOLDER = 14  # MsoShapeType.msoPlaceholder
_PP_PLACEHOLDER_BODY = 2  # PpPlaceholderType.ppPlaceholderBody (노트 본문도 이 타입)

# PowerPoint PlaceholderFormat.Type → 라벨(베스트에포트; 실기기에서 조정 가능).
_PLACEHOLDER_LABELS = {
    1: "title",
    13: "title",
    12: "title",
    2: "body",
    3: "body",
    4: "subtitle",
}


def extract_ppt_raw(path: str) -> RawPptDocument:
    """pptx 경로 → :class:`RawPptDocument` (win32com 으로 슬라이드/도형/노트 취득)."""
    try:
        import pythoncom  # noqa: F401
        import win32com.client as win32
    except ImportError as exc:  # pragma: no cover - 환경 의존
        raise RuntimeError(
            "pywin32 가 필요합니다(Windows + PowerPoint). pip install pywin32"
        ) from exc

    file_name = os.path.basename(path)
    abspath = os.path.abspath(path)

    logger.info("[RawPpt] 열기: %s", abspath)
    pythoncom.CoInitialize()
    app = None
    pres = None
    try:
        app = win32.DispatchEx("PowerPoint.Application")
        com_util.track("ppt", app)
        # PowerPoint 는 Visible=False 로 열면 예외가 나는 버전이 있어 WithWindow=False 로 연다.
        pres = app.Presentations.Open(
            abspath, ReadOnly=True, Untitled=False, WithWindow=False
        )
        slides = [_probe_slide(s, i) for i, s in enumerate(pres.Slides, start=1)]
        doc = build_ppt_doc(file_name, slides)
        logger.info("[RawPpt] 완료: 슬라이드 %d장", len(doc.slides))
        return doc
    except Exception:
        logger.exception("[RawPpt] 처리 실패: %s", abspath)
        raise
    finally:
        if pres is not None:
            try:
                pres.Close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[RawPpt] pres.Close 실패(무시): %s", exc)
        com_util.close_app("ppt", app)
        try:
            pythoncom.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass


def _probe_slide(slide: Any, slide_no: int) -> SlideProbe:  # pragma: no cover - COM
    """COM 슬라이드 → :class:`SlideProbe`."""
    layout_name = None
    try:
        layout_name = slide.CustomLayout.Name
    except Exception:  # noqa: BLE001
        pass

    shapes: list[ShapeProbe] = []
    for shape in slide.Shapes:
        probe = _probe_shape(shape)
        if probe is not None:
            shapes.append(probe)
    return SlideProbe(
        slide_no=slide_no,
        layout_name=layout_name,
        shapes=shapes,
        notes=_notes_text(slide),
    )


def _probe_shape(shape: Any) -> Optional[ShapeProbe]:  # pragma: no cover - COM
    """COM 도형 → :class:`ShapeProbe`. 텍스트박스/표만, 그 외는 None."""
    name = None
    try:
        name = shape.Name
    except Exception:  # noqa: BLE001
        pass

    pos = {}
    for key, attr in (("left", "Left"), ("top", "Top"), ("width", "Width"), ("height", "Height")):
        try:
            pos[key] = float(getattr(shape, attr))
        except Exception:  # noqa: BLE001
            pass

    # 표 우선 판정.
    try:
        has_table = bool(shape.HasTable)
    except Exception:  # noqa: BLE001
        has_table = False
    if has_table:
        rows = _table_rows(shape)
        return ShapeProbe(kind="table", name=name, rows=rows, **pos)

    # 차트/이미지 등은 제외.
    try:
        if shape.HasChart:
            return None
    except Exception:  # noqa: BLE001
        pass

    # 텍스트 프레임.
    try:
        if shape.HasTextFrame and shape.TextFrame.HasText:
            text = shape.TextFrame.TextRange.Text
            return ShapeProbe(
                kind="text",
                name=name,
                text=text,
                placeholder=_placeholder_label(shape),
                **pos,
            )
    except Exception:  # noqa: BLE001
        pass
    return None


def _table_rows(shape: Any) -> list[list[str]]:  # pragma: no cover - COM
    """COM 표 도형 → 셀 텍스트 2D."""
    table = shape.Table
    n_rows = table.Rows.Count
    n_cols = table.Columns.Count
    out: list[list[str]] = []
    for r in range(1, n_rows + 1):
        row: list[str] = []
        for c in range(1, n_cols + 1):
            try:
                row.append(table.Cell(r, c).Shape.TextFrame.TextRange.Text)
            except Exception:  # noqa: BLE001
                row.append("")
        out.append(row)
    return out


def _placeholder_label(shape: Any) -> Optional[str]:  # pragma: no cover - COM
    """placeholder 도형이면 종류 라벨(title/body/...), 아니면 None."""
    try:
        if shape.Type != _MSO_PLACEHOLDER:
            return None
        ptype = shape.PlaceholderFormat.Type
    except Exception:  # noqa: BLE001
        return None
    return _PLACEHOLDER_LABELS.get(int(ptype), f"type_{int(ptype)}")


def _notes_text(slide: Any) -> Optional[str]:  # pragma: no cover - COM
    """스피커 노트 텍스트. NotesPage 의 **본문 placeholder만** 읽는다.

    노트 페이지에는 본문 외에 슬라이드 썸네일(이미지)·슬라이드 번호 placeholder 가
    섞여 있다. 본문(ppPlaceholderBody=2)만 채택해 번호/날짜 등이 섞이지 않게 한다.
    """
    try:
        notes_page = slide.NotesPage
    except Exception:  # noqa: BLE001
        return None
    parts: list[str] = []
    try:
        for shape in notes_page.Shapes:
            try:
                if shape.Type != _MSO_PLACEHOLDER:
                    continue
                if shape.PlaceholderFormat.Type != _PP_PLACEHOLDER_BODY:
                    continue
                if shape.HasTextFrame and shape.TextFrame.HasText:
                    t = _clean(shape.TextFrame.TextRange.Text)
                    if t:
                        parts.append(t)
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        return None
    return " ".join(parts) or None
