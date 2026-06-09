"""WordReader 순수 조립 로직(_build_items) — 제목 섹션 + 표 row."""

from __future__ import annotations

from contentcompare.readers.word_reader import WordReader, _Para, _Table


def _para(text, level=10, page=None):
    return _Para(text=text, outline_level=level, page=page)


# --------------------------------------------------------------------------- #
# 산문: 제목(Heading) 섹션 단위
# --------------------------------------------------------------------------- #
def test_paragraphs_group_under_headings():
    paras = [
        _para("개요", level=1, page=1),
        _para("이 제품은 배터리다.", page=1),
        _para("충전 환경 온도는 45도 이하.", page=1),
        _para("규격", level=1, page=2),
        _para("두께는 5mm 이다.", page=2),
    ]
    items = WordReader._build_items("doc.docx", paras, [])
    assert len(items) == 2                       # 제목 2개 → 섹션 2개
    assert items[0].text == "개요\n이 제품은 배터리다.\n충전 환경 온도는 45도 이하."
    assert items[1].text == "규격\n두께는 5mm 이다."
    assert items[0].locator["heading"] == "개요"
    assert items[0].source_label == "doc.docx > 1페이지 > 개요"


def test_no_headings_single_section():
    # 제목이 없으면 문서 전체가 한 섹션(이후 chunker 가 길면 분할).
    paras = [_para("문장 하나."), _para("문장 둘."), _para("문장 셋.")]
    items = WordReader._build_items("doc.docx", paras, [])
    assert len(items) == 1
    assert items[0].text == "문장 하나.\n문장 둘.\n문장 셋."
    assert "1번째 구역" in items[0].source_label


def test_empty_paragraphs_ignored_in_text():
    # 빈 문단은 COM 추출 단계에서 걸러지지만, 조립에서도 빈 섹션은 항목이 안 된다.
    items = WordReader._build_items("doc.docx", [_para("제목만", level=1)], [])
    assert len(items) == 1 and items[0].text == "제목만"


# --------------------------------------------------------------------------- #
# 표: 한 row = 한 항목
# --------------------------------------------------------------------------- #
def test_table_row_becomes_one_item():
    table = _Table(rows=[["제품", "온도", "두께"], ["A", "45", "5mm"], ["B", "50", "6mm"]], page=3)
    items = WordReader._build_items("doc.docx", [], [table])
    assert len(items) == 3
    assert items[0].text == "제품 | 온도 | 두께"
    assert items[1].text == "A | 45 | 5mm"
    assert items[1].locator == {"table": 1, "row": 2, "page": 3}
    assert items[1].source_label == "doc.docx > 3페이지 > 표1 2행"


def test_table_empty_cells_and_rows_skipped():
    table = _Table(rows=[["", "  ", ""], ["값", "", "x"]])
    items = WordReader._build_items("doc.docx", [], [table])
    assert len(items) == 1            # 빈 행은 제외
    assert items[0].text == "값 | x"  # 빈 셀은 제외


def test_sections_and_tables_combined():
    paras = [_para("개요", level=1), _para("본문.")]
    tables = [_Table(rows=[["a", "b"]])]
    items = WordReader._build_items("doc.docx", paras, tables)
    kinds = [it.item_id.split("#")[1][:3] for it in items]
    assert "sec" in kinds and any(k.startswith("t1") for k in kinds)
