"""Document Profiler 테스트 — FakeLLM 주입(네트워크 불필요)."""

from __future__ import annotations

import json

from contentcompare.fact.artifacts import ArtifactStore
from contentcompare.fact.llm_stage import LlmRunner
from contentcompare.fact.profiler import profile_document

_COMPACT = {"doc_type": "excel", "file_name": "기준.xlsx",
            "sheets": [{"sheet_name": "S", "rows": [{"r": 1, "cells": {"A": "항목"}}]}]}


class _ProfileChat:
    def __init__(self):
        self.calls = 0

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        return json.dumps({
            "doc_type": "excel",
            "main_purpose": "규격 항목 리스트",
            "main_structures": [{"kind": "table", "location": "sheet=S",
                                 "purpose": "규격 목록", "row_grain_hint": "행=항목"}],
            "confidence": 0.9,
        })


def test_profile_parsed():
    runner = LlmRunner(_ProfileChat())
    prof = profile_document(_COMPACT, runner)
    assert prof.doc_type == "excel"
    assert prof.main_purpose == "규격 항목 리스트"
    assert prof.main_structures[0].kind == "table"
    assert prof.confidence == 0.9
    assert runner.calls == 1


def test_profile_cached_skips_llm(tmp_path):
    store = ArtifactStore(str(tmp_path), "기준.xlsx")
    chat = _ProfileChat()
    runner = LlmRunner(chat)
    profile_document(_COMPACT, runner, store)
    assert (tmp_path / "기준_xlsx" / "document_profile.json").exists()
    assert chat.calls == 1
    # 같은 입력 → 새 runner 로도 캐시 히트(LLM 미호출).
    runner2 = LlmRunner(_ProfileChat())
    prof2 = profile_document(_COMPACT, runner2, store)
    assert runner2.calls == 0
    assert prof2.main_purpose == "규격 항목 리스트"


def test_profile_fallback_doc_type():
    class _NoType:
        def complete(self, system, user, *, temperature=0.0):
            return json.dumps({"main_purpose": "x"})

    prof = profile_document({"doc_type": "word"}, LlmRunner(_NoType()))
    assert prof.doc_type == "word"  # LLM 이 doc_type 누락 → compact 값으로 폴백
