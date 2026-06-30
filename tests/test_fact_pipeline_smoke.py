"""FactPipeline(F0+F1) 스모크 테스트.

COM/네트워크를 피하려고 가짜 추출기 + 가짜 chat 을 주입한다. raw→compact→profile→
schema(Excel) 까지 artifacts 가 생성되고, F2 단계는 명시적 미구현인지 검증한다.
"""

from __future__ import annotations

import json
import os

import pytest

from contentcompare.config import AppConfig
from contentcompare.fact import pipeline as pipeline_mod
from contentcompare.fact.artifacts import ArtifactStore
from contentcompare.fact.pipeline import FactPipeline
from contentcompare.raw.excel_raw import CellProbe, SheetProbe, build_raw_sheet
from contentcompare.raw.models import RawExcelDocument
from contentcompare.raw.word_raw import ParaProbe, build_word_doc


def _fake_excel(path):
    doc = RawExcelDocument(file_name=os.path.basename(path))
    probe = SheetProbe(
        name="S",
        cells=[
            CellProbe(1, 5, "항목"), CellProbe(1, 6, "하한치"), CellProbe(1, 7, "상한치"),
            CellProbe(2, 5, "충전환경온도"), CellProbe(2, 6, -5), CellProbe(2, 7, 55),
        ],
        min_row=1, max_row=2, min_col=5, max_col=7,
    )
    doc.sheets.append(build_raw_sheet(probe))
    return doc


def _fake_word(path):
    return build_word_doc(os.path.basename(path), [ParaProbe(text="충전환경온도는 -5~55℃")])


class _FactChat:
    """Profiler/Schema 프롬프트에 맞는 JSON 을 돌려주는 가짜 chat."""

    def __init__(self):
        self.calls = 0

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        if "semantic_role" in system:  # SCHEMA_SYSTEM
            return json.dumps({
                "table_profile": {
                    "header_structure": {"header_start_row": 1, "header_rows": 1,
                                         "data_start_row": 2, "header_depth": 1},
                    "row_grain": {"description": "행=항목", "primary_entity_columns": ["E"]},
                },
                "column_schema": {"columns": [
                    {"column": "E", "field_name": "항목", "semantic_role": "entity_name",
                     "data_type": "string", "raw_header": ["충전환경온도"]},
                    {"column": "F", "field_name": "하한치", "semantic_role": "quantitative_lower_bound",
                     "data_type": "number", "raw_header": ["하한치"]},
                ]},
            })
        return json.dumps({  # PROFILER_SYSTEM
            "doc_type": "excel", "main_purpose": "규격 리스트",
            "main_structures": [], "confidence": 0.7,
        })


def _config(tmp_path, *, save=True):
    cfg = AppConfig()
    cfg.fact.artifacts_dir = str(tmp_path)
    cfg.fact.save_artifacts = save
    return cfg


def _pipe(tmp_path, *, extractor=_fake_excel, save=True, chat=None):
    return FactPipeline(_config(tmp_path, save=save), extractor=extractor, chat=chat or _FactChat())


def test_excel_produces_f1_artifacts(tmp_path):
    pipe = _pipe(tmp_path)
    with pytest.raises(NotImplementedError):
        pipe.run("기준.xlsx", [])
    d = tmp_path / ArtifactStore.slug("기준.xlsx")
    for stage in ("physical_raw", "compact_raw", "document_profile", "table_profile", "column_schema"):
        assert (d / f"{stage}.json").exists(), stage
    cs = json.loads((d / "column_schema.json").read_text(encoding="utf-8"))
    assert cs["columns"][0]["semantic_role"] == "entity_name"


def test_word_produces_profile_only(tmp_path):
    pipe = _pipe(tmp_path, extractor=_fake_word)
    with pytest.raises(NotImplementedError):
        pipe.run("설명.docx", [])
    d = tmp_path / ArtifactStore.slug("설명.docx")
    assert (d / "document_profile.json").exists()
    assert not (d / "table_profile.json").exists()  # Word 는 schema induction 비대상


def test_run_raises_not_implemented_for_f2(tmp_path):
    with pytest.raises(NotImplementedError):
        _pipe(tmp_path).run("기준.xlsx", ["대상.xlsx"])


def test_save_artifacts_false_writes_nothing(tmp_path):
    pipe = _pipe(tmp_path, save=False)
    with pytest.raises(NotImplementedError):
        pipe.run("기준.xlsx", [])
    assert list(tmp_path.iterdir()) == []


def test_close_office_called_in_finally(tmp_path, monkeypatch):
    flag = {"closed": False}
    monkeypatch.setattr(pipeline_mod, "close_all_office", lambda: flag.__setitem__("closed", True))
    with pytest.raises(NotImplementedError):
        _pipe(tmp_path).run("기준.xlsx", [])
    assert flag["closed"] is True


def test_progress_callback_invoked(tmp_path):
    seen = []
    with pytest.raises(NotImplementedError):
        _pipe(tmp_path).run("기준.xlsx", ["대상.xlsx"],
                            progress=lambda i, n, p: seen.append((i, n, p)))
    assert seen == [(1, 2, "기준.xlsx"), (2, 2, "대상.xlsx")]
