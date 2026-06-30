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


def _rec(row, name, cat=""):
    return {
        "record_id": f"row-{row}", "source": {"row": row},
        "entity": {"category": cat, "display_name": name},
        "quantitative_spec": {"lower": None, "target": None, "upper": None, "unit": ""},
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


def test_empty_records_batch_ok():
    chat = _RecChat([json.dumps({"records": []})])
    rs = normalize_records(_COMPACT, _TP, _CS, LlmRunner(chat), batch_rows=30)
    assert rs.records == []


def test_no_data_rows_raises():
    compact = {"doc_type": "excel", "sheets": [{"sheet_name": "S",
               "rows": [{"r": 1, "cells": {"E": "항목"}}]}]}  # 헤더만(데이터 시작행=2 미만)
    with pytest.raises(ValueError):
        normalize_records(compact, _TP, _CS, LlmRunner(_RecChat([])), batch_rows=30)


def test_build_record_user_includes_columns_rows_and_carry():
    user = build_record_user(
        [{"r": 2, "cells": {"E": "충전환경온도", "F": -5}}],
        _CS, _TP, {"category": "기본사양", "subcategory": ""},
    )
    assert "entity_name" in user        # 열 스키마 요약 포함
    assert "행 2" in user               # 데이터 행 포함
    assert "기본사양" in user           # carry 분류 포함
