"""Word Raw Extractor — WordOpenXML(WordprocessingML)을 직접 파싱.

회사 환경 제약으로 python-docx 를 못 쓰지만, win32com 으로 **Word 가 만들어 주는
OpenXML 문자열**(``Document.Content.WordOpenXML``)을 받아 우리가 직접 파싱하면
병합 정보를 추정 없이 정확히 읽을 수 있다. 이것이 Office 가 실제로 표/병합을
저장하는 형식이기 때문이다.

설계: COM 은 **XML 문자열을 받아오는 일만** 한다(단 1회 호출). 나머지 파싱은 전부
순수 함수(:func:`parse_word_xml`)라 Word 없이 단위테스트가 가능하다.

병합 인코딩(WordprocessingML)
------------------------------
- 가로 병합: 셀 ``<w:tc>`` 의 ``<w:tcPr><w:gridSpan w:val="N"/>`` → N개 그리드 컬럼.
- 세로 병합: ``<w:vMerge w:val="restart"/>`` 가 시작 셀(값 보유),
  ``<w:vMerge/>``(또는 val="continue")가 연속 셀 → 시작 셀의 값을 상속.
- 컬럼 수: ``<w:tblGrid>`` 의 ``<w:gridCol>`` 개수.

raw 단계 정책: 병합된 셀은 병합된 **모든 칸에 동일한 값**을 채운다(가로=gridSpan,
세로=vMerge). 의미 해석은 후속 LLM 단계의 몫.
"""

from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from ..readers import com_util
from .models import RawLine, RawWordBlock, RawWordDocument

logger = logging.getLogger(__name__)

# WordprocessingML / 패키지 네임스페이스.
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_PKG_NS = "http://schemas.microsoft.com/office/2006/xmlPackage"


def _w(tag: str) -> str:
    return f"{{{_W_NS}}}{tag}"


def _pkg(tag: str) -> str:
    return f"{{{_PKG_NS}}}{tag}"


# --------------------------------------------------------------------------- #
# 블록 probe (파싱 결과 → 빌더 입력)
# --------------------------------------------------------------------------- #
@dataclass
class ParaProbe:
    """문단 1개."""

    text: str
    """문단 원문. **줄바꿈(``\\n``)을 그대로 담는다** — ``build_word_doc`` 이 이것을
    line 으로 쪼개고, 동시에 병합된 ``block.text`` 도 만든다."""

    style_name: Optional[str] = None
    bold: Optional[bool] = None
    font_size: Optional[float] = None
    list_item: bool = False
    """``<w:numPr>`` 가 있는 목록 문단인가."""


@dataclass
class TableProbe:
    """표 1개(셀 텍스트 2D, 병합은 이미 채워진 상태)."""

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
            block_id = f"w_b{order:03d}"
            doc.blocks.append(
                RawWordBlock(
                    block_id=block_id,
                    order=order,
                    type="paragraph",
                    text=text,
                    style=_style_dict(p),
                    structure=_structure_dict(p),
                    lines=_split_lines(block_id, p.text or ""),
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


def _split_lines(block_id: str, text: str) -> list[RawLine]:
    """문단 원문을 줄 단위로 쪼갠다. 빈 줄은 담지 않는다.

    번호는 **남은 줄에 연속으로** 매긴다(빈 줄 자리를 비워 두지 않는다) — 사람이
    리포트의 ``:l03`` 을 원문에서 셀 때 빈 줄까지 세도록 만들 이유가 없다.

    ``raw_text`` 는 양끝 공백만 정리한다. 내부 탭을 지우면 인용이 원문과 달라져
    사람이 대조할 수 없고, 근거 실재 검증도 어긋난다.
    """
    out: list[RawLine] = []
    for piece in text.splitlines():
        raw = piece.strip()
        if not raw:
            continue
        out.append(RawLine(
            line_id=f"{block_id}:l{len(out) + 1:02d}",
            order=len(out) + 1,
            raw_text=raw,
            normalized_text=" ".join(raw.split()),
        ))
    return out


_HEADING_STYLE = re.compile(r"^heading\s*(\d+)$", re.IGNORECASE)


def _style_dict(p: ParaProbe) -> Optional[dict[str, Any]]:
    """문단 서식 정보. 알 수 있는 값만 담고, 전부 없으면 None.

    ⚠️ 이 값은 ``compact_raw`` 를 타고 **F3 LLM 프롬프트에 그대로 실린다.** 키를
    더하면 fact 추출 결과와 캐시가 통째로 바뀌므로 늘리지 말 것 — 코드만 쓰는
    구조 정보는 :func:`_structure_dict` 로 간다.
    """
    info: dict[str, Any] = {}
    if p.style_name:
        info["style_name"] = p.style_name
    if p.bold is not None:
        info["bold"] = p.bold
    if p.font_size is not None:
        info["font_size"] = p.font_size
    return info or None


def _structure_dict(p: ParaProbe) -> Optional[dict[str, Any]]:
    """문단 구조 정보(heading 계층 · 목록 여부). 전부 없으면 None.

    2차 검사가 "가장 가까운 상위 heading" 과 "같은 목록의 앞뒤 항목"을 회수하려면
    이 둘이 필요하다(설계 §8.6). ``style`` 과 달리 LLM 에는 노출되지 않는다.
    """
    info: dict[str, Any] = {}
    if p.style_name:
        m = _HEADING_STYLE.match(p.style_name.strip())
        if m:
            info["heading_level"] = int(m.group(1))
    if p.list_item:
        info["list"] = True
    return info or None


# --------------------------------------------------------------------------- #
# 순수 XML 파서 (Word 불필요 — 테스트 진입점)
# --------------------------------------------------------------------------- #
def parse_word_xml(xml_str: str) -> list[BlockProbe]:
    """WordOpenXML 문자열 → 블록 probe 리스트(문서 등장 순서 보존).

    ``<w:body>`` 의 자식을 순서대로 훑어 ``<w:p>``→문단, ``<w:tbl>``→표 로 만든다.
    """
    body = _find_body(xml_str)
    if body is None:
        return []

    probes: list[BlockProbe] = []
    for el in list(body):
        if el.tag == _w("p"):
            probes.append(_parse_paragraph(el))
        elif el.tag == _w("tbl"):
            probes.append(TableProbe(rows=_parse_table(el)))
    return probes


def _find_body(xml_str: str) -> Optional[ET.Element]:
    """패키지/문서 XML 문자열에서 ``<w:body>`` 엘리먼트를 찾는다."""
    s = xml_str.lstrip("﻿").lstrip()
    if s.startswith("<?xml"):
        end = s.find("?>")
        if end != -1:
            s = s[end + 2 :]
    try:
        root = ET.fromstring(s)
    except ET.ParseError:
        return None

    # 1) pkg:package → /word/document.xml 파트의 w:document/w:body
    if root.tag == _pkg("package"):
        for part in root.findall(_pkg("part")):
            if part.get(_pkg("name")) == "/word/document.xml":
                data = part.find(_pkg("xmlData"))
                document = data.find(_w("document")) if data is not None else None
                return document.find(_w("body")) if document is not None else None
        return None
    # 2) 이미 w:document 인 경우
    if root.tag == _w("document"):
        return root.find(_w("body"))
    # 3) 이미 w:body 인 경우
    if root.tag == _w("body"):
        return root
    # 4) 어디든 body 가 있으면 사용.
    return root.find(f".//{_w('body')}")


def _parse_paragraph(p: ET.Element) -> ParaProbe:
    """``<w:p>`` → :class:`ParaProbe` (텍스트 + 스타일/굵게/크기)."""
    text = _runs_text(p)

    style_name = None
    list_item = False
    pPr = p.find(_w("pPr"))
    if pPr is not None:
        pStyle = pPr.find(_w("pStyle"))
        if pStyle is not None:
            style_name = pStyle.get(_w("val"))
        list_item = pPr.find(_w("numPr")) is not None

    bold, size = _first_run_format(p)
    return ParaProbe(
        text=text, style_name=style_name, bold=bold, font_size=size, list_item=list_item
    )


def _runs_text(container: ET.Element) -> str:
    """엘리먼트 하위 모든 ``<w:t>`` 텍스트를 이어 붙인다.

    ``<w:br>``/``<w:cr>`` 은 **줄바꿈으로 남긴다.** 예전에는 공백으로 바꿨는데,
    그러면 한 문단에 조건이 여러 개 적힌 원문(충전 온도 4구간)이 한 줄로 뭉개져
    어느 조건이 대상에 있고 없는지를 사후에 확인할 수 없었다.

    ``<w:tab>`` 은 그대로 탭이다 — 줄 경계가 아니므로 표 흉내를 낸 문단이 잘못
    쪼개지지 않는다. 병합된 ``block.text`` 는 :func:`build_word_doc` 이 공백으로
    정리하므로 이 변경이 **compact 출력에 새어나가지 않는다**.
    """
    parts: list[str] = []
    for node in container.iter():
        if node.tag == _w("t"):
            parts.append(node.text or "")
        elif node.tag == _w("tab"):
            parts.append("\t")
        elif node.tag in (_w("br"), _w("cr")):
            parts.append("\n")
    return "".join(parts)


def _first_run_format(p: ET.Element):
    """문단의 첫 비어있지 않은 run 의 (bold, size_pt) 추정. 없으면 (None, None)."""
    for r in p.findall(_w("r")):
        run_text = "".join(t.text or "" for t in r.findall(_w("t")))
        if not run_text.strip():
            continue
        rPr = r.find(_w("rPr"))
        if rPr is None:
            return None, None
        return _bold_of(rPr), _size_of(rPr)
    return None, None


def _bold_of(rPr: ET.Element) -> Optional[bool]:
    """``<w:b/>`` → True, ``<w:b w:val="false"/>`` → False, 없으면 None."""
    b = rPr.find(_w("b"))
    if b is None:
        return None
    val = b.get(_w("val"))
    if val in ("0", "false", "off", "none"):
        return False
    return True


def _size_of(rPr: ET.Element) -> Optional[float]:
    """``<w:sz w:val="HH"/>`` (half-point) → 포인트(float). 없으면 None."""
    sz = rPr.find(_w("sz"))
    if sz is None:
        return None
    val = sz.get(_w("val"))
    try:
        return float(val) / 2.0
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# 표 파싱 (병합 처리의 핵심)
# --------------------------------------------------------------------------- #
def _parse_table(tbl: ET.Element) -> list[list[str]]:
    """``<w:tbl>`` → 셀 텍스트 2D. 가로(gridSpan)/세로(vMerge) 병합을 모두 채운다.

    각 행의 ``<w:tc>`` 를 왼쪽부터 순회하며 현재 그리드 컬럼을 ``gridSpan`` 만큼
    전진시킨다. ``vMerge`` 연속 셀은 같은 컬럼의 시작 셀 값을 상속한다. 모든 그리드
    위치가 tc 로 표현되므로(연속 셀도 빈 tc 로 존재) 컬럼 추적이 정확하다.
    """
    rows_el = tbl.findall(_w("tr"))
    if not rows_el:
        return []

    # 컬럼 수: tblGrid 우선, 없으면 행별 gridSpan 합의 최댓값.
    grid = tbl.find(_w("tblGrid"))
    n_cols = len(grid.findall(_w("gridCol"))) if grid is not None else 0
    if n_cols == 0:
        n_cols = max(
            (sum(_grid_span(tc) for tc in tr.findall(_w("tc"))) for tr in rows_el),
            default=0,
        )
    if n_cols == 0:
        return []

    vmerge_text: list[Optional[str]] = [None] * n_cols  # 컬럼별 세로 병합 시작 값
    out: list[list[str]] = []
    for tr in rows_el:
        row_vals = [""] * n_cols
        col = 0
        for tc in tr.findall(_w("tc")):
            span = _grid_span(tc)
            vmerge = _vmerge_state(tc)

            if vmerge == "continue":
                text = vmerge_text[col] if col < n_cols else ""
                text = text or ""
            else:
                text = _cell_text(tc)
                for k in range(col, min(col + span, n_cols)):
                    vmerge_text[k] = text if vmerge == "restart" else None

            for k in range(col, min(col + span, n_cols)):
                row_vals[k] = text
            col += span
        out.append(row_vals)
    return out


def _grid_span(tc: ET.Element) -> int:
    """셀의 가로 병합 칸 수(``<w:gridSpan>``). 기본 1."""
    tcPr = tc.find(_w("tcPr"))
    if tcPr is None:
        return 1
    gs = tcPr.find(_w("gridSpan"))
    if gs is None:
        return 1
    try:
        return max(1, int(gs.get(_w("val"))))
    except (TypeError, ValueError):
        return 1


def _vmerge_state(tc: ET.Element) -> Optional[str]:
    """세로 병합 상태: ``"restart"`` | ``"continue"`` | ``None``(병합 아님)."""
    tcPr = tc.find(_w("tcPr"))
    if tcPr is None:
        return None
    vm = tcPr.find(_w("vMerge"))
    if vm is None:
        return None
    val = vm.get(_w("val"))
    return "restart" if val == "restart" else "continue"


def _cell_text(tc: ET.Element) -> str:
    """셀 안 문단들의 텍스트(여러 문단은 공백으로 결합, 공백 정돈)."""
    parts: list[str] = []
    for p in tc.findall(_w("p")):
        s = _runs_text(p).strip()
        if s:
            parts.append(s)
    return " ".join(" ".join(parts).split())


# --------------------------------------------------------------------------- #
# COM 진입점 (WordOpenXML 문자열만 받아온다)
# --------------------------------------------------------------------------- #
def extract_word_raw(path: str) -> RawWordDocument:
    """docx 파일 경로 → :class:`RawWordDocument` (win32com 으로 OpenXML 취득 후 파싱)."""
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
        xml_str = com_doc.Content.WordOpenXML  # 문서 전체를 OpenXML 문자열로
        doc = build_word_doc(file_name, parse_word_xml(xml_str))
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
