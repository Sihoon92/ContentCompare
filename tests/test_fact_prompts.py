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


def test_fact_system_requires_per_condition_attributes():
    """조건이 여럿이면 fact 를 나누지 말고 속성을 나누라고 지시해야 한다."""
    from contentcompare.fact.prompts import FACT_SYSTEM

    assert "의미 경계가 아닙니다" in FACT_SYSTEM
    assert "fact 를 나누지 말고" in FACT_SYSTEM
    assert "inherited_from" in FACT_SYSTEM
    # 분해 방향을 넣지 않는다(설계 §1.4-②) — 쪼개면 top_k·동명·과병합을 누른다.
    assert "독립된 fact" not in FACT_SYSTEM


def test_fact_version_bumped():
    """프롬프트가 바뀌면 버전도 올라야 캐시가 옛 결과를 안 준다."""
    from contentcompare.fact.prompts import FACT_VERSION

    assert FACT_VERSION == "fact-v4"


# --------------------------------------------------------------------------- #
# 프롬프트 ↔ 와이어 스키마 정합 (구조화 출력)
# --------------------------------------------------------------------------- #
_SYSTEM_FOR_STAGE = {
    "profiler": "PROFILER_SYSTEM",
    "schema": "SCHEMA_SYSTEM",
    "record": "RECORD_SYSTEM",
    "fact": "FACT_SYSTEM",
    "concept": "CONCEPT_SYSTEM",
    "compare": "COMPARE_SYSTEM",
}


def test_every_system_prompt_mentions_json():
    """``json_object`` 모드의 OpenAI 요구사항이다.

    프롬프트를 다듬다 이 낱말이 사라지면 그 모드가 **조용히 400** 이 된다.
    """
    from contentcompare.fact import prompts

    for name in _SYSTEM_FOR_STAGE.values():
        assert "JSON" in getattr(prompts, name), name


def test_prompt_template_root_keys_match_the_wire_schema():
    """프롬프트의 인라인 JSON 예시와 스키마 루트 키가 **갈리지 않았는가**.

    둘이 어긋나면 프롬프트는 A 를 요구하고 서버는 B 를 강제하는 상태가 된다 — strict 가
    이기므로 **프롬프트 쪽이 조용한 거짓말**이 되고, 다음 사람이 프롬프트를 근거로
    디버깅하다 시간을 잃는다.
    """
    import pytest

    pytest.importorskip("pydantic")
    from contentcompare.fact import prompts
    from contentcompare.fact.schemas import schema_for

    for stage, const in _SYSTEM_FOR_STAGE.items():
        text = getattr(prompts, const)
        for key in schema_for(stage)["properties"]:
            assert f'"{key}"' in text, f"{const} 에 루트 키 {key!r} 가 없다"


def test_f2_f3_prompts_ask_for_attribute_arrays():
    """strict 는 자유 키 map 을 표현할 수 없다 — 프롬프트도 배열을 요구해야 한다."""
    from contentcompare.fact.prompts import FACT_SYSTEM, RECORD_SYSTEM

    for text in (RECORD_SYSTEM, FACT_SYSTEM):
        assert '"attributes": [{"name"' in text
    assert '"metadata": [{"name"' in RECORD_SYSTEM
