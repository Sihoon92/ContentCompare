"""문단 하나가 Word 파일 안에서 **실제로 어떻게 저장돼 있는지** 보여주는 진단 CLI.

``physical_raw.json`` 은 이미 가공된 결과다. 거기서는 "들여쓰기로 앞 줄을 이어받은
연속행"과 "그냥 독립된 문단"이 똑같아 보인다 — ``build_word_doc`` 이 공백을 병합하고
``_split_lines`` 가 양끝 공백을 지우기 때문이다. 어느 쪽인지 알려면 원본 XML 을 봐야
한다. 이 스크립트는 .docx 를 zip 으로 직접 열어(Word·COM 불필요) 그것만 보여준다.

무엇을 보는가::

    ind        문단 들여쓰기 <w:pPr><w:ind>. 있으면 "연속행"의 가장 깨끗한 신호다.
               ⚠️ 현재 파이프라인은 이 값을 **읽지 않는다**(_style_dict/_structure_dict
               어디에도 없다). 그래서 있어도 지금은 버려진다.
    text(raw)  <w:t>/<w:tab>/<w:br> 을 이어 붙인 날것. 선행 공백·탭이 살아 있는 유일한
               자리다(build_word_doc 이 다음 단계에서 지운다).
    text(블록)  실제로 compact_raw 를 타고 LLM 에 가는 값. 여기서 무엇이 사라지는지 비교.

사용법::

    python scripts/inspect_paragraph.py 문서.docx 15~45          # 검색어가 든 문단
    python scripts/inspect_paragraph.py 문서.docx 1.1C --context 2  # 앞뒤 문단까지
    python scripts/inspect_paragraph.py 문서.docx --index 245     # 순번으로(0-based)
    python scripts/inspect_paragraph.py 문서.docx 15~45 --xml     # 원본 XML 통째로
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="문단이 .docx 안에 실제로 어떻게 저장돼 있는지 본다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("docx", help=".docx 경로")
    p.add_argument("needle", nargs="?", default="", help="문단에 포함된 문자열")
    p.add_argument("--index", type=int, default=-1, help="문단 순번으로 직접 지정")
    p.add_argument("--context", type=int, default=1, help="앞뒤로 함께 볼 문단 수")
    p.add_argument("--xml", action="store_true", help="원본 XML 도 출력")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)

    try:
        with zipfile.ZipFile(args.docx) as z:
            xml_bytes = z.read("word/document.xml")
    except (OSError, KeyError, zipfile.BadZipFile) as e:
        print(f"❌ .docx 를 zip 으로 열지 못했습니다: {type(e).__name__}: {e}")
        print("   DRM·암호가 걸린 파일일 수 있습니다. 그 경우 Word 로 '다른 이름으로 저장'")
        print("   해서 사본을 만든 뒤 다시 시도하세요.")
        return 1

    root = ET.fromstring(xml_bytes)
    body = root.find(f"{W}body")
    if body is None:
        print("❌ <w:body> 를 찾지 못했습니다.")
        return 1

    # 본문 직계 자식만 — parse_word_xml 과 같은 규칙이라 순번이 일치한다.
    children = [el for el in list(body) if el.tag in (f"{W}p", f"{W}tbl")]
    paras = [(i, el) for i, el in enumerate(children) if el.tag == f"{W}p"]
    print(f"본문 직계 블록 {len(children)}개 (문단 {len(paras)} · 표 "
          f"{len(children) - len(paras)})")
    print("⚠️ 아래 [n] 은 XML 자식 순번이라 physical_raw 의 block_id 와 다를 수 있습니다 —"
          " build_word_doc 이 빈 문단을 건너뛰고 번호를 매기기 때문입니다."
          " 내용으로 대조하세요.\n")

    hits = _select(paras, args)
    if not hits:
        print("조건에 맞는 문단이 없습니다.")
        return 1

    wanted = _with_context(children, hits, args.context)
    for i in wanted:
        el = children[i]
        if el.tag == f"{W}tbl":
            print(f"── [{i}] <w:tbl> (표) ──")
            continue
        _dump(i, el, show_xml=args.xml)
    return 0


def _select(paras, args) -> list[int]:
    if args.index >= 0:
        return [i for i, _ in paras if i == args.index]
    if not args.needle:
        print("검색어나 --index 중 하나는 주셔야 합니다.")
        return []
    return [i for i, el in paras if args.needle in _runs_text(el)]


def _with_context(children, hits: list[int], context: int) -> list[int]:
    keep: set[int] = set()
    for i in hits:
        for j in range(max(0, i - context), min(len(children), i + context + 1)):
            keep.add(j)
    return sorted(keep)


def _dump(i: int, p: ET.Element, *, show_xml: bool) -> None:
    raw = _runs_text(p)
    block_text = " ".join(raw.split())  # build_word_doc 과 같은 처리

    print(f"── [{i}] <w:p> ──")
    print(f"  ind        : {_indent(p) or '(없음)'}")
    print(f"  style      : {_style(p) or '(없음)'}")
    print(f"  <w:br> 수  : {len(p.findall(f'.//{W}br')) + len(p.findall(f'.//{W}cr'))}")
    print(f"  <w:tab> 수 : {len(p.findall(f'.//{W}tab'))}")
    print("  text(raw)  : 줄별 repr — 선행 공백·탭이 보이면 그것이 연속행 신호다")
    for n, line in enumerate(raw.split("\n"), 1):
        print(f"      l{n:02d} {line!r}")
    print(f"  text(블록) : {block_text!r}")
    print("               ↑ 이 값만 compact_raw 를 타고 LLM 에 간다")
    if show_xml:
        print("  XML:")
        print("    " + ET.tostring(p, encoding="unicode").replace("\n", "\n    "))
    print()


def _indent(p: ET.Element) -> str:
    """``<w:pPr><w:ind>`` 의 속성 전부. 연속행 신호로 가장 깨끗한 자리."""
    pPr = p.find(f"{W}pPr")
    ind = pPr.find(f"{W}ind") if pPr is not None else None
    if ind is None:
        return ""
    return " ".join(f"{re.sub(r'^\{.*\}', '', k)}={v}" for k, v in ind.attrib.items())


def _style(p: ET.Element) -> str:
    pPr = p.find(f"{W}pPr")
    st = pPr.find(f"{W}pStyle") if pPr is not None else None
    return st.get(f"{W}val", "") if st is not None else ""


def _runs_text(p: ET.Element) -> str:
    """``word_raw._runs_text`` 와 같은 규약 — 두 곳이 갈리면 진단이 거짓말을 한다."""
    parts: list[str] = []
    for node in p.iter():
        if node.tag == f"{W}t":
            parts.append(node.text or "")
        elif node.tag == f"{W}tab":
            parts.append("\t")
        elif node.tag in (f"{W}br", f"{W}cr"):
            parts.append("\n")
    return "".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
