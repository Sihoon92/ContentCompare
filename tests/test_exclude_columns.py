"""knowledge 메모 → 비교 제외 컬럼 추출(LLM + 규칙 기반 폴백) 테스트."""

from __future__ import annotations

from contentcompare.exclude_columns import (
    detect_excluded_columns,
    has_exclusion_hint,
    merge_skip_columns,
)


class FixedLLM:
    """지정 문자열을 그대로 반환하는 가짜 LLM(호출 여부도 기록)."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0
        self.seen_user = None

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        self.seen_user = user
        return self.reply


class BoomLLM:
    """호출되면 예외를 던지는 가짜 LLM(폴백 경로 검증용)."""

    def __init__(self):
        self.calls = 0

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        raise RuntimeError("LLM down")


# --------------------------------------------------------------------------- #
# 키워드 게이트
# --------------------------------------------------------------------------- #
def test_no_hint_skips_llm_call():
    llm = FixedLLM('{"excluded_columns": ["순번"]}')
    knowledge = "# 용어 정의\n- formation: 화성 공정"
    assert detect_excluded_columns(knowledge, llm) == []
    assert llm.calls == 0  # 제외 키워드가 없으면 LLM 미호출


def test_has_exclusion_hint():
    assert has_exclusion_hint("순번은 비교에서 제외")
    assert has_exclusion_hint("ignore the index column")
    assert not has_exclusion_hint("매출과 Revenue 는 같다")


# --------------------------------------------------------------------------- #
# LLM 추출
# --------------------------------------------------------------------------- #
def test_llm_extract_parses_json():
    llm = FixedLLM('{"excluded_columns": ["순번", "중분류 CODE", "소분류 CODE"]}')
    knowledge = "# 비교 제외 항목\n- 순번, 중분류 CODE, 소분류 CODE 는 제외"
    assert detect_excluded_columns(knowledge, llm) == ["순번", "중분류 CODE", "소분류 CODE"]
    assert llm.calls == 1
    assert "도메인 지식" in llm.seen_user


def test_llm_extract_from_surrounding_text():
    llm = FixedLLM('결과: {"excluded_columns": ["순번"]} 입니다')
    assert detect_excluded_columns("순번 제외", llm) == ["순번"]


def test_llm_empty_list():
    llm = FixedLLM('{"excluded_columns": []}')
    # 키워드는 있으나 LLM 이 제외 컬럼 없음으로 판단.
    assert detect_excluded_columns("이 항목은 제외하지 않는다", llm) == []


def test_llm_failure_falls_back_to_rules():
    llm = BoomLLM()
    knowledge = "# 비교 제외 항목\n- 순번\n- 중분류 CODE"
    assert detect_excluded_columns(knowledge, llm) == ["순번", "중분류 CODE"]
    assert llm.calls == 1  # 호출은 했으나 실패 → 규칙 기반


# --------------------------------------------------------------------------- #
# 규칙 기반 폴백(LLM 없음)
# --------------------------------------------------------------------------- #
def test_rule_based_section():
    knowledge = (
        "# 용어 정의\n- formation: 화성\n"
        "# 비교 제외 항목(컬럼)\n- 순번\n- 중분류 CODE, 소분류 CODE\n"
        "# 기타\n- 자유서술"
    )
    assert detect_excluded_columns(knowledge, None) == ["순번", "중분류 CODE", "소분류 CODE"]


def test_rule_based_section_bullet_with_inline_keyword():
    # 섹션 헤더 + 불릿 자체에 '…는 비교에서 제외' 문구가 섞인 경우(연결어가 토큰에 안 섞여야).
    knowledge = "# 비교 제외 항목(컬럼)\n- 순번, 중분류 CODE, 소분류 CODE 는 비교에서 제외한다"
    assert detect_excluded_columns(knowledge, None) == ["순번", "중분류 CODE", "소분류 CODE"]


def test_rule_based_inline():
    knowledge = "- 순번, 중분류 CODE, 소분류 CODE 는 비교에서 제외한다"
    assert detect_excluded_columns(knowledge, None) == ["순번", "중분류 CODE", "소분류 CODE"]


def test_rule_based_inline_english():
    assert detect_excluded_columns("순번/CODE exclude from comparison", None) == ["순번", "CODE"]


# --------------------------------------------------------------------------- #
# merge_skip_columns
# --------------------------------------------------------------------------- #
def test_merge_appends_without_duplicates():
    assert merge_skip_columns(["순번"], ["순번", "중분류 CODE"]) == ["순번", "중분류 CODE"]


def test_merge_keeps_existing_indices():
    assert merge_skip_columns([1, "순번"], ["소분류 CODE"]) == [1, "순번", "소분류 CODE"]


def test_merge_case_insensitive_dedup():
    assert merge_skip_columns(["CODE"], ["code"]) == ["CODE"]
