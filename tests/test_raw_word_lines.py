"""F0 원문 무손실 — 문단 내부 줄바꿈을 line 으로 보존한다(Phase 3).

기존 경로는 ``<w:br>``/``<w:cr>`` 을 공백으로 바꾸고 전체 공백을 병합했다. 그러면
"충전 온도 4구간"처럼 한 문단 안에 조건이 여러 개인 원문이 한 줄로 뭉개져,
어느 구간이 대상에 있고 없는지를 사후에 확인할 방법이 사라진다.

**핵심 안전 속성**: ``compact_raw`` 출력은 바이트 동일하게 유지된다. compact 가
F3 LLM 의 입력이므로, 여기가 바뀌면 fact 추출 결과가 통째로 바뀌고 캐시가 전면
무효화된다. line 보존은 ``physical_raw`` 계층에만 쌓인다.
"""

from __future__ import annotations

import json

from contentcompare.raw.compact import compact_word
from contentcompare.raw.word_raw import ParaProbe, TableProbe, build_word_doc, parse_word_xml

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _xml(body: str) -> str:
    return f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'


def _para(*runs: str, br_between: bool = False) -> str:
    """run 들을 담은 <w:p>. br_between 이면 run 사이에 <w:br/> 을 넣는다."""
    parts = []
    for i, r in enumerate(runs):
        if i and br_between:
            parts.append("<w:r><w:br/></w:r>")
        parts.append(f"<w:r><w:t>{r}</w:t></w:r>")
    return f"<w:p>{''.join(parts)}</w:p>"


# --------------------------------------------------------------------------- #
# 완료 조건 — 네 온도 조건이 서로 구분된 ID 와 원문으로 저장된다
# --------------------------------------------------------------------------- #
CHARGE_LINES = [
    "Charge temperature ranges:",
    "-5~5℃, 0.1C(4.55V)",
    "5~12℃, 0.3C(4.55V)",
    "12~15℃, 0.7C(4.55V)",
    "15~45℃, 1.2C(4.20V)",
]


def _charge_doc():
    return build_word_doc("spec.docx", [ParaProbe(text="\n".join(CHARGE_LINES))])


def test_four_conditions_get_distinct_line_ids():
    """상위 설계 §2 의 대표 사례. 이게 안 되면 2차 검사가 볼 원문이 없다."""
    doc = _charge_doc()
    (block,) = doc.blocks
    assert [l.line_id for l in block.lines] == [
        "w_b001:l01", "w_b001:l02", "w_b001:l03", "w_b001:l04", "w_b001:l05",
    ]
    assert [l.raw_text for l in block.lines] == CHARGE_LINES


def test_line_order_is_one_based_and_sequential():
    (block,) = _charge_doc().blocks
    assert [l.order for l in block.lines] == [1, 2, 3, 4, 5]


def test_raw_text_and_normalized_text_are_separate():
    """raw_text 는 인용용, normalized_text 는 검색용 — 정규화가 인용을 훼손하면 안 된다."""
    doc = build_word_doc("d.docx", [ParaProbe(text="  5~12℃,\t0.3C  ")])
    (line,) = doc.blocks[0].lines
    assert line.raw_text == "5~12℃,\t0.3C"      # 양끝만 정리, 내부 탭 보존
    assert line.normalized_text == "5~12℃, 0.3C"  # 검색용은 공백 병합


def test_blank_lines_are_dropped_but_do_not_shift_ids():
    """빈 줄은 담지 않되, 남은 줄의 번호는 연속이어야 한다(사람이 세기 쉽게)."""
    doc = build_word_doc("d.docx", [ParaProbe(text="첫줄\n\n\n둘째줄")])
    (block,) = doc.blocks
    assert [(l.line_id, l.raw_text) for l in block.lines] == [
        ("w_b001:l01", "첫줄"), ("w_b001:l02", "둘째줄"),
    ]


def test_single_line_paragraph_still_gets_a_line():
    """줄바꿈이 없어도 line 은 1건 생긴다 — 소비자가 분기하지 않게."""
    doc = build_word_doc("d.docx", [ParaProbe(text="공칭전압 3.85V")])
    (line,) = doc.blocks[0].lines
    assert line.line_id == "w_b001:l01" and line.raw_text == "공칭전압 3.85V"


# --------------------------------------------------------------------------- #
# 하위호환 — 기존 필드와 compact 출력이 변하면 안 된다
# --------------------------------------------------------------------------- #
def test_block_text_is_unchanged():
    """block.text 는 예전 그대로 한 줄로 병합된 값이다."""
    (block,) = _charge_doc().blocks
    assert block.text == " ".join(CHARGE_LINES)


def test_compact_output_is_byte_identical():
    """compact 가 F3 LLM 의 입력이다 — 여기가 바뀌면 캐시가 전면 무효화된다."""
    doc = _charge_doc()
    out = compact_word(doc)
    assert out == {
        "doc_type": "word",
        "file_name": "spec.docx",
        "blocks": [{
            "id": "w_b001",
            "type": "paragraph",
            "text": " ".join(CHARGE_LINES),
        }],
    }
    assert "lines" not in json.dumps(out)


def test_physical_raw_carries_lines():
    """physical_raw 에는 실린다 — Phase 4-5 가 여기서 원문을 읽는다."""
    d = _charge_doc().to_dict()
    lines = d["blocks"][0]["lines"]
    assert len(lines) == 5
    assert lines[2] == {
        "line_id": "w_b001:l03",
        "order": 3,
        "raw_text": "5~12℃, 0.3C(4.55V)",
    }


def test_table_block_has_no_lines_key():
    """표는 행/셀 2D 로 이미 구조가 보존돼 있다 — line 을 덧붙이지 않는다."""
    doc = build_word_doc("d.docx", [TableProbe(rows=[["a", "b"], ["c", "d"]])])
    (block,) = doc.blocks
    assert block.lines == []
    assert "lines" not in block.to_dict()


# --------------------------------------------------------------------------- #
# XML 파서 — <w:br>/<w:cr> 이 줄 경계로 남는다
# --------------------------------------------------------------------------- #
def test_br_becomes_a_line_boundary():
    probes = parse_word_xml(_xml(_para("-5~5℃, 0.1C", "5~12℃, 0.3C", br_between=True)))
    doc = build_word_doc("d.docx", probes)
    assert [l.raw_text for l in doc.blocks[0].lines] == ["-5~5℃, 0.1C", "5~12℃, 0.3C"]


def test_tab_stays_inside_a_line():
    """탭은 줄 경계가 아니다 — 표 흉내를 낸 문단이 잘못 쪼개지면 안 된다."""
    probes = parse_word_xml(_xml('<w:p><w:r><w:t>항목</w:t><w:tab/><w:t>3.85V</w:t></w:r></w:p>'))
    doc = build_word_doc("d.docx", probes)
    assert len(doc.blocks[0].lines) == 1


def test_separate_paragraphs_stay_separate_blocks():
    """실제 문단이 나뉘어 있으면 블록 ID 를 유지한다(line sub-ID 로 합치지 않는다)."""
    probes = parse_word_xml(_xml(_para("첫 문단") + _para("둘째 문단")))
    doc = build_word_doc("d.docx", probes)
    assert [b.block_id for b in doc.blocks] == ["w_b001", "w_b002"]
    assert [b.lines[0].line_id for b in doc.blocks] == ["w_b001:l01", "w_b002:l01"]


# --------------------------------------------------------------------------- #
# heading / list 정보
# --------------------------------------------------------------------------- #
def test_heading_level_is_derived_from_style_name():
    """제목 계층은 2차 검사가 '가장 가까운 상위 heading' 을 찾는 데 쓴다."""
    probes = parse_word_xml(_xml(
        '<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>'
        '<w:r><w:t>Charging</w:t></w:r></w:p>'
    ))
    doc = build_word_doc("d.docx", probes)
    assert doc.blocks[0].structure["heading_level"] == 2


def test_non_heading_style_has_no_structure():
    doc = build_word_doc("d.docx", [ParaProbe(text="본문", style_name="Normal")])
    assert doc.blocks[0].structure is None


def test_list_paragraph_is_flagged():
    """같은 목록의 앞뒤 항목을 함께 회수하려면 목록 여부를 알아야 한다."""
    probes = parse_word_xml(_xml(
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="3"/></w:numPr></w:pPr>'
        '<w:r><w:t>항목 하나</w:t></w:r></w:p>'
    ))
    doc = build_word_doc("d.docx", probes)
    assert doc.blocks[0].structure["list"] is True


def test_structure_is_kept_out_of_the_llm_input():
    """구조 정보는 style 과 분리돼 compact 에 새어나가지 않는다.

    compact_word 는 block.style 을 **통째로** 통과시킨다. 구조 키를 style 에 담으면
    F3 프롬프트가 바뀌어 fact 추출 결과와 캐시가 통째로 무효화된다. 이 테스트가
    그 실수를 막는 잠금장치다.
    """
    probes = parse_word_xml(_xml(
        '<w:p><w:pPr><w:pStyle w:val="Heading2"/>'
        '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="3"/></w:numPr></w:pPr>'
        '<w:r><w:t>Charging</w:t></w:r></w:p>'
    ))
    block = build_word_doc("d.docx", probes).blocks[0]
    assert block.structure == {"heading_level": 2, "list": True}
    assert block.style == {"style_name": "Heading2"}

    emitted = compact_word(build_word_doc("d.docx", probes))["blocks"][0]
    assert emitted["style"] == {"style_name": "Heading2"}
    assert "structure" not in emitted and "lines" not in emitted


# --------------------------------------------------------------------------- #
# 줄 들여쓰기 (Task 1)
# --------------------------------------------------------------------------- #
def test_split_lines_keeps_indent_width():
    """선행 공백·탭이 indent 로 남고, raw_text 는 여전히 strip 된다."""
    from contentcompare.raw.word_raw import _split_lines

    text = "15~45도씨, 1.2C(4.20V)\n           1.1C(4.28V)\n\t\t0.8C(4.55V)"
    lines = _split_lines("w_b010", text)

    assert [l.indent for l in lines] == [0, 11, 8]      # 탭 하나 = 4칸
    assert [l.raw_text for l in lines] == [
        "15~45도씨, 1.2C(4.20V)",
        "1.1C(4.28V)",
        "0.8C(4.55V)",
    ]


def test_raw_line_to_dict_omits_zero_indent():
    """0 을 싣지 않는다 — physical_raw 가 줄마다 쓸모없는 키로 커진다."""
    from contentcompare.raw.models import RawLine

    plain = RawLine(line_id="w_b001:l01", order=1, raw_text="a", indent=0)
    inset = RawLine(line_id="w_b001:l02", order=2, raw_text="b", indent=7)

    assert "indent" not in plain.to_dict()
    assert inset.to_dict()["indent"] == 7
