"""Schema Inducer 테스트 — FakeLLM 주입(네트워크 불필요)."""

from __future__ import annotations

import json

import pytest

from contentcompare.fact.artifacts import ArtifactStore
from contentcompare.fact.llm_stage import LlmRunner
from contentcompare.fact.schema_inducer import induce_schema
from contentcompare.fact.schema_models import DocumentProfile

# 헤더(행3) + 데이터(행4)인 정량규격 시트.
_COMPACT = {
    "doc_type": "excel", "file_name": "기준.xlsx",
    "sheets": [{
        "sheet_name": "StandardList", "n_rows": 2, "n_cols": 4,
        "merged_cells": [{"range": "F2:H2", "value": "정량규격"}],
        "rows": [
            {"r": 3, "cells": {"E": "항목", "F": "하한치", "G": "중심치", "H": "상한치"}},
            {"r": 4, "cells": {"E": "충전환경온도", "F": -5, "G": 25, "H": 55}},
        ],
    }],
}
_PROFILE = DocumentProfile(doc_type="excel", main_purpose="규격 리스트")


class _SchemaChat:
    """table_profile + column_schema 를 한 번에 반환하는 가짜 chat."""

    def __init__(self, bad_role=False):
        self.calls = 0
        self.bad_role = bad_role

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        lower_role = "made_up_role" if self.bad_role else "quantitative_lower_bound"
        return json.dumps({
            "table_profile": {
                "header_structure": {"header_start_row": 3, "header_rows": 1,
                                     "data_start_row": 4, "header_depth": 1},
                "row_grain": {"description": "행=규격 항목", "primary_entity_columns": ["E"]},
            },
            "column_schema": {"columns": [
                {"column": "E", "field_name": "항목", "semantic_role": "entity_name",
                 "data_type": "string", "raw_header": ["충전환경온도"]},
                {"column": "F", "field_name": "하한치", "semantic_role": lower_role,
                 "data_type": "number", "raw_header": ["하한치"]},
                {"column": "G", "field_name": "중심치", "semantic_role": "quantitative_target",
                 "data_type": "number", "raw_header": ["중심치"]},
                {"column": "H", "field_name": "상한치", "semantic_role": "quantitative_upper_bound",
                 "data_type": "number", "raw_header": ["상한치"]},
            ]},
        })


def test_schema_parsed():
    runner = LlmRunner(_SchemaChat())
    tp, cs = induce_schema(_COMPACT, _PROFILE, runner)
    assert tp.location == "sheet=StandardList"
    assert tp.header_structure.header_start_row == 3
    assert tp.header_structure.data_start_row == 4
    assert tp.row_grain.primary_entity_columns == ["E"]
    assert cs.role_of("E") == "entity_name"
    assert cs.role_of("F") == "quantitative_lower_bound"
    assert cs.role_of("H") == "quantitative_upper_bound"
    assert runner.calls == 1


def test_schema_bad_role_demoted():
    runner = LlmRunner(_SchemaChat(bad_role=True))
    _, cs = induce_schema(_COMPACT, _PROFILE, runner)
    assert cs.role_of("F") == "unknown"  # 미허용 role → 강등


def test_schema_saves_two_artifacts_and_caches(tmp_path):
    store = ArtifactStore(str(tmp_path), "기준.xlsx")
    chat = _SchemaChat()
    induce_schema(_COMPACT, _PROFILE, LlmRunner(chat), store)
    d = tmp_path / "기준_xlsx"
    assert (d / "table_profile.json").exists()
    assert (d / "column_schema.json").exists()
    assert chat.calls == 1
    # 같은 입력 → 캐시 히트, LLM 미호출, column_schema 디스크에서 복원.
    runner2 = LlmRunner(_SchemaChat())
    tp2, cs2 = induce_schema(_COMPACT, _PROFILE, runner2, store)
    assert runner2.calls == 0
    assert cs2.role_of("F") == "quantitative_lower_bound"
    assert tp2.header_structure.header_start_row == 3


def test_no_table_raises():
    runner = LlmRunner(_SchemaChat())
    with pytest.raises(ValueError):
        induce_schema({"doc_type": "excel", "sheets": []}, _PROFILE, runner)
