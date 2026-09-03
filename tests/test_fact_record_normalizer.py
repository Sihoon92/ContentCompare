"""Record Normalizer 테스트 — FakeLLM 주입(네트워크 불필요)."""

from __future__ import annotations

import json

import pytest

from contentcompare.fact.artifacts import ArtifactStore
from contentcompare.fact.llm_stage import LlmRunner
from contentcompare.fact.prompts import build_record_user
from contentcompare.fact.record_normalizer import normalize_records
from contentcompare.fact.schema_models import (
    ColumnSchema,
    ColumnSpec,
    HeaderStructure,
    RowGrain,
    TableProfile,
)

# 헤더(행1) + 데이터(행2,3,4). D=대분류, E=항목, F=하한치.
_COMPACT = {
    "doc_type": "excel",
    "file_name": "기준.xlsx",
    "sheets": [{
        "sheet_name": "S",
        "rows": [
            {"r": 1, "cells": {"D": "대분류", "E": "항목", "F": "하한치"}},
            {"r": 2, "cells": {"D": "기본사양", "E": "충전환경온도", "F": -5}},
            {"r": 3, "cells": {"E": "방전환경온도", "F": -10}},
            {"r": 4, "cells": {"E": "저장온도", "F": -20}},
        ],
    }],
}
_TP = TableProfile(
    location="sheet=S",
    header_structure=HeaderStructure(header_start_row=1, header_rows=1, data_start_row=2),
    row_grain=RowGrain(description="행=규격 항목"),
)
_CS = ColumnSchema(location="sheet=S", columns=[
    ColumnSpec(column="D", field_name="대분류", semantic_role="entity_category"),
    ColumnSpec(column="E", field_name="항목", semantic_role="entity_name"),
    ColumnSpec(column="F", field_name="하한치", semantic_role="quantitative_lower_bound"),
])


def _rec(row, name, cat="", attrs=None):
    return {
        "record_id": f"row-{row}", "source": {"row": row},
        "entity": {"category": cat, "display_name": name},
        "attributes": attrs or {},
        "evidence_text": name, "confidence": 0.9,
    }


class _RecChat:
    """배치별로 큐의 JSON 을 차례로 반환하고 user 프롬프트를 캡처한다."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.user_prompts = []

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        self.user_prompts.append(user)
        return self._responses.pop(0)


def test_batches_and_merges_with_source_filled():
    # batch_rows=2 → 배치1=[행2,행3], 배치2=[행4] → 2호출.
    chat = _RecChat([
        json.dumps({"records": [_rec(2, "충전환경온도", "기본사양"), _rec(3, "방전환경온도")]}),
        json.dumps({"records": [_rec(4, "저장온도")]}),
    ])
    rs = normalize_records(_COMPACT, _TP, _CS, LlmRunner(chat), batch_rows=2)
    assert chat.calls == 2
    assert [r.record_id for r in rs.records] == ["row-2", "row-3", "row-4"]
    # source.cell_range 는 코드가 매핑 열(D,E,F) 범위로 채움.
    assert rs.records[0].source.cell_range == "D2:F2"  # 행2: D,E,F 존재
    assert rs.records[1].source.cell_range == "E3:F3"  # 행3: E,F 만 존재
    assert rs.records[0].source.sheet == "S"


def test_carry_over_passes_prior_category_to_next_batch():
    chat = _RecChat([
        json.dumps({"records": [_rec(2, "충전환경온도", "기본사양"), _rec(3, "방전환경온도")]}),
        json.dumps({"records": [_rec(4, "저장온도")]}),
    ])
    normalize_records(_COMPACT, _TP, _CS, LlmRunner(chat), batch_rows=2)
    # 두 번째 배치 프롬프트에 직전 분류(기본사양)가 주입됨.
    assert "기본사양" in chat.user_prompts[1]
    assert "직전" in chat.user_prompts[1]


def test_cache_hit_skips_llm(tmp_path):
    store = ArtifactStore(str(tmp_path), "기준.xlsx")
    chat = _RecChat([json.dumps({"records": [_rec(2, "충전환경온도", "기본사양"),
                                             _rec(3, "방전환경온도"), _rec(4, "저장온도")]})])
    normalize_records(_COMPACT, _TP, _CS, LlmRunner(chat), batch_rows=30, store=store)
    assert (tmp_path / "기준_xlsx" / "records.json").exists()
    assert chat.calls == 1
    runner2 = LlmRunner(_RecChat([]))  # 호출되면 IndexError → 캐시 히트 보장
    rs2 = normalize_records(_COMPACT, _TP, _CS, runner2, batch_rows=30, store=store)
    assert runner2.calls == 0
    assert [r.record_id for r in rs2.records] == ["row-2", "row-3", "row-4"]


def test_attributes_parsed_through_normalizer():
    """규격 경계(canonical) + 일반 field_name 속성이 모두 무손실로 파싱된다."""
    chat = _RecChat([json.dumps({"records": [
        _rec(2, "충전환경온도", "기본사양", attrs={
            "lower_limit": {"value": -5, "unit": "℃"},
            "정격전압": {"value": 3.7, "unit": "V"},
        })
    ]})])
    rs = normalize_records(_COMPACT, _TP, _CS, LlmRunner(chat), batch_rows=30)
    a = rs.records[0].attributes
    assert a["lower_limit"].value == -5 and a["lower_limit"].unit == "℃"
    assert a["정격전압"].value == 3.7  # 일반 속성도 유실 없이 보존


def test_empty_records_batch_ok():
    chat = _RecChat([json.dumps({"records": []})])
    rs = normalize_records(_COMPACT, _TP, _CS, LlmRunner(chat), batch_rows=30)
    assert rs.records == []


def test_no_data_rows_raises():
    compact = {"doc_type": "excel", "sheets": [{"sheet_name": "S",
               "rows": [{"r": 1, "cells": {"E": "항목"}}]}]}  # 헤더만(데이터 시작행=2 미만)
    with pytest.raises(ValueError):
        normalize_records(compact, _TP, _CS, LlmRunner(_RecChat([])), batch_rows=30)


def test_hallucinated_cell_range_is_cleared_for_row_not_in_batch():
    """행이 배치에 없으면 LLM 이 준 cell_range 를 코드가 빈 문자열로 덮어쓴다."""
    # 배치: 행2,3,4 만 있음. LLM 은 행99(배치 밖)를 반환하고 "Z99" 를 주장.
    chat = _RecChat([
        json.dumps({"records": [
            {"record_id": "row-99", "entity": {"display_name": "ghost"},
             "source": {"row": 99, "cell_range": "Z99"}}
        ]})
    ])
    rs = normalize_records(_COMPACT, _TP, _CS, LlmRunner(chat), batch_rows=30)
    assert len(rs.records) == 1
    assert rs.records[0].source.cell_range == ""  # LLM 좌표 신뢰하지 않음


def test_build_record_user_includes_columns_rows_and_carry():
    user = build_record_user(
        [{"r": 2, "cells": {"E": "충전환경온도", "F": -5}}],
        _CS, _TP, {"category": "기본사양", "subcategory": ""},
    )
    assert "entity_name" in user        # 열 스키마 요약 포함
    assert "행 2" in user               # 데이터 행 포함
    assert "기본사양" in user           # carry 분류 포함


# --------------------------------------------------------------------------- #
# 출력 절단 → 배치 자동 축소 (run_batch)
#
# 실측 배경: F2 배치가 completion_tokens=32368 에서 죽었는데, 재시도가 원리적으로
# 무력했다 — 넛지가 "짧게"가 아니라 "JSON 만"이고 temperature=0 이라 같은 30행을
# 다시 보내면 같은 지점에서 잘린다. 유일한 조치는 **입력을 줄이는 것**이다.
# --------------------------------------------------------------------------- #
class _TruncatingChat(_RecChat):
    """프롬프트에 실린 행이 ``limit`` 개 이상이면 출력이 잘린 것처럼 군다.

    행 수는 ``build_record_user`` 가 실제로 찍는 ``행 <번호>:`` 를 세어 얻는다 —
    가짜가 진짜 프롬프트 계약에 기대게 해서, 그 계약이 깨지면 테스트도 깨지게 한다.
    (``"행 "`` 부분일치로 세면 머리말의 ``[행 의미]`` 까지 걸려 하나가 더 세어진다.)
    """

    def __init__(self, responses, *, limit: int) -> None:
        super().__init__(responses)
        self.limit = limit
        self.row_counts: list[int] = []

    def complete(self, system, user, *, temperature=0.0):
        import re

        from contentcompare.llm.truncation import LengthLimitError

        rows = len(re.findall(r"^행 \d+:", user, re.M))
        self.row_counts.append(rows)
        if rows >= self.limit:
            self.calls += 1
            self.user_prompts.append(user)
            raise LengthLimitError(
                "잘렸습니다", output='{"records": [', output_chars=32000,
                backend="fake",
            )
        return super().complete(system, user, temperature=temperature)


def test_length_limit_splits_the_batch_and_recovers():
    """3행 배치가 잘리면 1행 + 2행으로 갈라 **순서대로** 다시 부른다."""
    chat = _TruncatingChat([
        json.dumps({"records": [_rec(2, "충전환경온도", "기본사양")]}),
        json.dumps({"records": [_rec(3, "방전환경온도"), _rec(4, "저장온도")]}),
    ], limit=3)

    rs = normalize_records(_COMPACT, _TP, _CS, LlmRunner(chat), batch_rows=3)

    assert chat.calls == 3                      # 실패 1 + 조각 2
    assert chat.row_counts == [3, 1, 2]         # 3 → [1, 2] 로 갈렸다
    # 행 순서가 보존된다 — 조각을 뒤에서부터 부르거나 병합 순서가 틀리면 여기서 깨진다.
    assert [r.record_id for r in rs.records] == ["row-2", "row-3", "row-4"]


def test_carry_survives_a_split():
    """**이 파일에서 가장 중요한 테스트.**

    carry(상위 분류)는 그 조각을 파싱한 뒤 갱신되어 **다음 조각의 프롬프트**로 들어간다.
    "두 조각을 다 부르고 병합"하는 설계였다면 뒤 조각이 앞 조각의 분류를 못 받아
    **조용히 틀린 분류**가 나온다 — 실패보다 나쁘다.
    """
    chat = _TruncatingChat([
        json.dumps({"records": [_rec(2, "충전환경온도", "기본사양")]}),
        json.dumps({"records": [_rec(3, "방전환경온도"), _rec(4, "저장온도")]}),
    ], limit=3)

    normalize_records(_COMPACT, _TP, _CS, LlmRunner(chat), batch_rows=3)

    # user_prompts = [잘린 3행, 조각1(1행), 조각2(2행)]
    #
    # ⚠️ ``"기본사양" in prompt`` 로 보면 안 된다 — 그 낱말은 행 D열 **데이터**에도 있어서
    # carry 가 통째로 안 붙어도 통과한다. carry **줄** 자체를 확인해야 한다.
    assert "[직전까지 확정된 분류] category=기본사양" in chat.user_prompts[2]
    # 첫 조각은 아직 아무 분류도 확정하지 못했다.
    assert "[직전까지 확정된 분류]" not in chat.user_prompts[1]


def test_split_stops_at_one_row_with_a_different_message():
    """1행까지 줄여도 잘리면 원인이 배치 크기가 아니다 — 조치가 달라 문구를 바꾼다."""
    from contentcompare.llm.truncation import LengthLimitError

    chat = _TruncatingChat([], limit=1)  # 무엇을 주든 잘린다
    with pytest.raises(LengthLimitError, match="배치 크기 문제가 아닙니다"):
        normalize_records(_COMPACT, _TP, _CS, LlmRunner(chat, max_calls=99), batch_rows=3)
    # 3 → [1,2] → 2 는 다시 [1,1]. 무한 분할이 아니라 유한하게 끝난다.
    assert chat.calls <= 6


def test_split_preserves_the_truncated_evidence():
    """더 못 쪼갤 때도 증거(잘린 원문·토큰)를 잃지 않는다."""
    from contentcompare.llm.truncation import LengthLimitError

    chat = _TruncatingChat([], limit=1)
    with pytest.raises(LengthLimitError) as caught:
        normalize_records(_COMPACT, _TP, _CS, LlmRunner(chat, max_calls=99), batch_rows=3)
    assert caught.value.output == '{"records": ['
    assert caught.value.output_chars == 32000


def test_budget_exceeded_is_not_split():
    """예산이 떨어지면 그대로 올라간다 — 더 쪼개면 같은 예외를 더 빨리 만날 뿐이다.

    분할이 만드는 호출은 새 예산을 두지 않고 ``max_llm_calls_per_doc`` 에 그대로 잡힌다.
    따로 세면 그 설정값이 거짓말을 하게 된다.

    호출 순서: 3행(실패, 1회) → 조각 1행(성공, 2회) → 조각 2행에서 예산 소진.
    """
    from contentcompare.fact.llm_stage import LlmBudgetExceeded

    chat = _TruncatingChat([
        json.dumps({"records": [_rec(2, "충전환경온도", "기본사양")]}),
    ], limit=3)
    with pytest.raises(LlmBudgetExceeded):
        normalize_records(_COMPACT, _TP, _CS, LlmRunner(chat, max_calls=2), batch_rows=3)


def test_split_is_measured_because_a_cache_hit_would_hide_it():
    """자동 복구가 조용히 성공하면 사람이 설정을 안 고친다. 계측이 유일한 증거다."""
    chat = _TruncatingChat([
        json.dumps({"records": [_rec(2, "충전환경온도", "기본사양")]}),
        json.dumps({"records": [_rec(3, "방전환경온도"), _rec(4, "저장온도")]}),
    ], limit=3)
    stats: dict = {}

    normalize_records(_COMPACT, _TP, _CS, LlmRunner(chat), batch_rows=3, stats=stats)

    assert stats["batches_split"] == 1
    assert stats["max_split_depth"] == 1
    assert stats["min_items_used"] == 1
    # 시나리오 판별용 — 출력 크기는 컬럼 수에 선형이다.
    assert stats["columns"] == 3 and stats["batch_rows"] == 3


def test_no_split_leaves_the_measurement_keys_out():
    """분할이 없었으면 0 을 남기지 않고 키 자체를 뺀다(usage.py 의 미상 규약과 같다)."""
    chat = _RecChat([json.dumps({"records": [_rec(2, "충전환경온도", "기본사양"),
                                             _rec(3, "방전환경온도"), _rec(4, "저장온도")]})])
    stats: dict = {}
    normalize_records(_COMPACT, _TP, _CS, LlmRunner(chat), batch_rows=30, stats=stats)
    assert "batches_split" not in stats and "max_split_depth" not in stats
