"""F3 Fact Extractor 프롬프트(Word/PPT) 빌더 테스트 (네트워크 불필요)."""

from __future__ import annotations

from contentcompare.fact.prompts import FACT_VERSION, build_fact_user


def test_build_fact_user_lists_ids_and_text():
    units = [
        {"id": "p1", "type": "text", "text": "충전환경온도는 -5~55℃"},
        {"id": "p2", "type": "text", "text": "중심치 25℃"},
    ]
    user = build_fact_user(units, "word")
    assert "p1" in user and "p2" in user
    assert "충전환경온도" in user
    assert "source_ids" in user  # LLM 이 근거 id 를 넣도록 안내


def test_build_fact_user_marks_slide_and_note():
    units = [
        {"id": "s3-1", "type": "text", "text": "충전환경온도: -5~55℃", "slide_no": 3},
        {"id": "s3-note", "type": "text", "text": "0.1C 조건", "slide_no": 3, "is_note": True},
    ]
    user = build_fact_user(units, "ppt")
    assert "slide=3" in user
    assert "스피커노트" in user


def test_build_fact_user_includes_profile_purpose():
    units = [{"id": "p1", "type": "text", "text": "본문"}]
    user = build_fact_user(units, "word", profile={"main_purpose": "표준 규격 요약"})
    assert "표준 규격 요약" in user


def test_build_fact_user_renders_table_rows():
    units = [{"id": "t1", "type": "table", "rows": [["항목", "값"], ["온도", "25"]]}]
    user = build_fact_user(units, "word")
    assert "온도" in user and "t1" in user


def test_fact_version_is_stable_string():
    assert isinstance(FACT_VERSION, str) and FACT_VERSION


def test_render_unit_single_line_is_unchanged():
    """줄이 1개면 기존 형태 그대로 — 대부분의 블록은 출력이 안 바뀐다."""
    from contentcompare.fact.prompts import _render_unit

    assert _render_unit({"id": "w_b001", "type": "text", "text": "공칭전압은 3.85V"}) == (
        "[w_b001] 공칭전압은 3.85V"
    )


def test_render_unit_expands_paragraph_lines_with_indent():
    """여러 줄이면 줄마다 펼치고 들여쓰기를 살린다."""
    from contentcompare.fact.prompts import _render_unit

    out = _render_unit({
        "id": "w_b246", "type": "text", "text": "1.1C(4.28V) 0.8C(4.55V)",
        "lines": [
            {"raw_text": "1.1C(4.28V)", "indent": 11},
            {"raw_text": "0.8C(4.55V)", "indent": 11},
        ],
    })

    assert out.splitlines() == [
        "[w_b246]            1.1C(4.28V)",
        "                    0.8C(4.55V)",
    ]


def test_render_unit_caps_absurd_indent():
    """비정상적으로 큰 들여쓰기가 프롬프트를 망가뜨리지 않게 자른다."""
    from contentcompare.fact.prompts import _RENDER_INDENT_CAP, _render_unit

    out = _render_unit({
        "id": "w_b001", "type": "text", "text": "a b",
        "lines": [{"raw_text": "a", "indent": 0}, {"raw_text": "b", "indent": 9999}],
    })

    assert out.splitlines()[1].count(" ") <= len("[w_b001] ") + _RENDER_INDENT_CAP


def test_render_unit_table_is_row_wise():
    """표는 파이썬 repr 이 아니라 행 단위로 렌더한다."""
    from contentcompare.fact.prompts import _render_unit

    out = _render_unit({
        "id": "w_b289", "type": "table",
        "rows": [["항목", "-5~5도씨, 0.1C 5~12도씨, 0.3C"]],
        "cell_lines": [[[], ["-5~5도씨, 0.1C", "5~12도씨, 0.3C"]]],
    })

    assert out.splitlines() == [
        "[w_b289] 표 (1행 × 2열)",
        "  행1 | 항목",
        "      | -5~5도씨, 0.1C",
        "        5~12도씨, 0.3C",
    ]


def test_render_unit_table_without_cell_lines_still_row_wise():
    """cell_lines 가 없어도(옛 산출물) 행 단위로 낸다 — repr 은 쓰지 않는다."""
    from contentcompare.fact.prompts import _render_unit

    out = _render_unit({"id": "w_b012", "type": "table", "rows": [["a", "b"], ["c", "d"]]})

    assert out.splitlines() == [
        "[w_b012] 표 (2행 × 2열)",
        "  행1 | a",
        "      | b",
        "  행2 | c",
        "      | d",
    ]
