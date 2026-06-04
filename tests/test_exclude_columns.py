"""knowledge 메모 → 비교 제외 컬럼 추출(LLM + 규칙 기반 폴백) 테스트."""

from __future__ import annotations

from contentcompare.config import ExcelConfig
from contentcompare.exclude_columns import (
    detect_excluded_columns,
    has_exclusion_hint,
    resolve_exclusions,
)
from contentcompare.readers.excel_reader import ExcelReader, SheetGrid


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
# resolve_exclusions — 후보 이름 → 실제 헤더 인덱스 (유연 매칭)
# --------------------------------------------------------------------------- #
def test_resolve_exact_normalized_no_llm():
    # 공백/대소문자 차이는 LLM 없이 정규화 정확 일치.
    headers = ["항목", "중분류 CODE", "상한치"]
    assert resolve_exclusions(["중분류code"], headers, llm=None) == [1]


def test_resolve_multiheader_leaf_no_llm():
    # 멀티헤더 '항목>중분류' 를 leaf '중분류' 로 매칭(LLM 불필요).
    headers = ["항목>중분류", "항목>상한치", "단위"]
    assert resolve_exclusions(["중분류"], headers, llm=None) == [0]


def test_resolve_typo_needs_llm():
    headers = ["항목", "중분류 CODE", "상한치"]
    # 오타 '중분류 CODD' 는 결정적으로 못 맞춤 → LLM 없으면 미해결(안전).
    assert resolve_exclusions(["중분류 CODD"], headers, llm=None) == []
    # LLM 이 있으면 가장 가까운 헤더로 매칭.
    llm = FixedLLM('{"matches": [{"request": "중분류 CODD", "header_index": 1}]}')
    assert resolve_exclusions(["중분류 CODD"], headers, llm=llm) == [1]


def test_resolve_llm_no_match_is_safe():
    headers = ["항목", "상한치"]
    # LLM 이 -1(매칭 없음) 주면 아무것도 제외하지 않음.
    llm = FixedLLM('{"matches": [{"request": "존재안함", "header_index": -1}]}')
    assert resolve_exclusions(["존재안함"], headers, llm=llm) == []


def test_resolve_llm_out_of_range_ignored():
    headers = ["항목", "상한치"]
    llm = FixedLLM('{"matches": [{"request": "x", "header_index": 99}]}')
    assert resolve_exclusions(["x"], headers, llm=llm) == []


def test_resolve_only_unresolved_sent_to_llm():
    # leaf 로 즉시 풀리는 건 LLM 에 안 보냄(잔여만 호출).
    headers = ["항목>중분류", "단위 CODE"]
    llm = FixedLLM('{"matches": [{"request": "단위 코드", "header_index": 1}]}')
    out = resolve_exclusions(["중분류", "단위 코드"], headers, llm=llm)
    assert out == [0, 1]
    # 요청 목록에는 잔여('단위 코드')만 올라간다('중분류'는 leaf 로 이미 해결).
    assert "- 단위 코드" in llm.seen_user
    assert "- 중분류" not in llm.seen_user


# --------------------------------------------------------------------------- #
# 리더 통합: exclude_hints → 실제 제외
# --------------------------------------------------------------------------- #
def test_reader_excludes_via_hints_leaf():
    cfg = ExcelConfig(auto_header=False, key_columns=["제품"])
    cfg.exclude_hints = ["중분류"]  # 실제 헤더는 '항목>중분류'
    grid = SheetGrid(name="S", values=[
        ["제품", "항목>중분류", "항목>상한치"],
        ["A", "M01", "100"],
    ])
    items = ExcelReader(cfg)._parse_sheet(grid, "ref.xlsx")
    headers = [f.header for f in items[0].fields]
    assert "항목>중분류" not in headers   # leaf 매칭으로 제외됨
    assert "항목>상한치" in headers


def test_reader_excludes_via_hints_typo_with_llm():
    cfg = ExcelConfig(auto_header=False, key_columns=["항목"])
    cfg.exclude_hints = ["중분류 CODD"]  # 오타
    grid = SheetGrid(name="S", values=[
        ["항목", "중분류 CODE", "상한치"],
        ["A", "M01", "100"],
    ])
    llm = FixedLLM('{"matches": [{"request": "중분류 CODD", "header_index": 1}]}')
    items = ExcelReader(cfg, llm=llm)._parse_sheet(grid, "ref.xlsx")
    headers = [f.header for f in items[0].fields]
    assert "중분류 CODE" not in headers
    assert "상한치" in headers
