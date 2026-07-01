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
