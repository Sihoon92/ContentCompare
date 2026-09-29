"""fact 파이프라인이 진행률 단위를 빠짐없이 열고 닫는가.

``test_fact_pipeline_smoke`` 의 가짜 추출기·chat·embedder 를 그대로 쓴다(tests/ 는
pytest 의 prepend 모드로 sys.path 에 올라와 있다).
"""

from __future__ import annotations

import os

import pytest

from contentcompare import progress as prog
from contentcompare.fact.pipeline import BLOCK_PARTS, EXCEL_PARTS, FactPipeline
from contentcompare.raw.excel_raw import CellProbe, SheetProbe, build_raw_sheet
from contentcompare.raw.models import RawExcelDocument
from test_fact_pipeline_smoke import (
    _config,
    _excel_or_ppt,
    _fake_excel,
    _FakeEmbedder,
    _pipe,
    _ppt_chat,
)


@pytest.fixture(autouse=True)
def mem():
    reporter = prog.MemoryProgress()
    prog.set_reporter(reporter)
    yield reporter
    prog.reset_reporter()


def _events(mem, ev, key=None):
    return [e for e in mem.events if e["ev"] == ev and (key is None or e.get("key") == key)]


def _fractions(events):
    return [prog.summarize(events[:k]).fraction for k in range(1, len(events) + 1)]


def _fake_excel_3rows(path):
    doc = RawExcelDocument(file_name=os.path.basename(path))
    cells = [CellProbe(1, 5, "항목"), CellProbe(1, 6, "하한치"), CellProbe(1, 7, "상한치")]
    for r, name in ((2, "충전환경온도"), (3, "보관온도"), (4, "방전온도")):
        cells += [CellProbe(r, 5, name), CellProbe(r, 6, -5), CellProbe(r, 7, 55)]
    probe = SheetProbe(name="S", cells=cells, min_row=1, max_row=4, min_col=5, max_col=7)
    doc.sheets.append(build_raw_sheet(probe))
    return doc


def test_plan_lists_every_unit_up_front(tmp_path, mem):
    _pipe(tmp_path, extractor=_excel_or_ppt, chat=_ppt_chat()).run("기준.xlsx", ["발표.pptx"])
    plans = _events(mem, "plan")
    assert len(plans) == 1 and mem.events[0]["ev"] == "plan"
    assert [u["key"] for u in plans[0]["units"]] == [
        "doc:0", "doc:1", "concept", "compare:발표.pptx"]


def test_document_parts_follow_the_document_type(tmp_path, mem):
    _pipe(tmp_path, extractor=_excel_or_ppt, chat=_ppt_chat()).run("기준.xlsx", ["발표.pptx"])
    assert [e["name"] for e in _events(mem, "part", "doc:0")] == list(EXCEL_PARTS)
    assert [e["name"] for e in _events(mem, "part", "doc:1")] == list(BLOCK_PARTS)
    starts = {e["key"]: e["parts"] for e in _events(mem, "unit_start")}
    assert starts["doc:0"] == len(EXCEL_PARTS) and starts["doc:1"] == len(BLOCK_PARTS)


def test_every_unit_finishes_and_run_ends_at_one(tmp_path, mem):
    _pipe(tmp_path, extractor=_excel_or_ppt, chat=_ppt_chat()).run("기준.xlsx", ["발표.pptx"])
    snap = prog.summarize(mem.events)
    assert snap.fraction == 1.0
    assert {u.key: u.state for u in snap.units} == {
        "doc:0": prog.DONE, "doc:1": prog.DONE,
        "concept": prog.DONE, "compare:발표.pptx": prog.DONE}
    fractions = _fractions(mem.events)
    assert fractions == sorted(fractions)


def test_f2_batches_report_inside_the_records_part(tmp_path, mem):
    cfg = _config(tmp_path)
    cfg.fact.record_batch_rows = 1
    from test_fact_pipeline_smoke import _FactChat

    FactPipeline(cfg, extractor=_fake_excel_3rows, chat=_FactChat(),
                 embedder=_FakeEmbedder()).run("기준.xlsx", [])
    seq = [(e["ev"], e.get("name"), e.get("done"), e.get("total"))
           for e in mem.events if e.get("key") == "doc:0" and e["ev"] in ("part", "step")]
    i = seq.index(("part", "F2 records", None, None))
    assert seq[i + 1:i + 5] == [("step", None, 0, 3), ("step", None, 1, 3),
                                ("step", None, 2, 3), ("step", None, 3, 3)]


def test_f3_block_batches_end_at_their_total(tmp_path, mem):
    _pipe(tmp_path, extractor=_excel_or_ppt, chat=_ppt_chat()).run("기준.xlsx", ["발표.pptx"])
    steps = _events(mem, "step", "doc:1")
    assert steps, "Word/PPT 의 F3 배치 진척이 없다"
    assert steps[-1]["done"] == steps[-1]["total"] >= 1


def test_failed_document_is_closed_as_failed(tmp_path, mem):
    def flaky(path):
        if path == "깨진.xlsx":
            raise OSError("파일을 열 수 없습니다")
        return _fake_excel(path)

    _pipe(tmp_path, extractor=flaky).run("기준.xlsx", ["깨진.xlsx", "대상.xlsx"])
    snap = prog.summarize(mem.events)
    states = {u.key: (u.state, u.error) for u in snap.units}
    assert states["doc:1"] == (prog.FAILED, "OSError")
    assert states["compare:깨진.xlsx"] == (prog.SKIPPED, "건너뜀")
    assert states["compare:대상.xlsx"] == (prog.DONE, "")
    assert snap.fraction == 1.0


def test_cache_hit_rerun_is_monotonic_and_completes(tmp_path, mem):
    cfg = _config(tmp_path)
    cfg.fact.record_batch_rows = 1
    from test_fact_pipeline_smoke import _FactChat

    FactPipeline(cfg, extractor=_fake_excel_3rows, chat=_FactChat(),
                 embedder=_FakeEmbedder()).run("기준.xlsx", [])
    second = prog.MemoryProgress()
    prog.set_reporter(second)
    FactPipeline(cfg, extractor=_fake_excel_3rows, chat=_FactChat(),
                 embedder=_FakeEmbedder()).run("기준.xlsx", [])
    # 캐시 적중이면 F2 배치 루프가 아예 돌지 않는다.
    assert not [e for e in second.events if e["ev"] == "step" and e.get("key") == "doc:0"
                and e.get("total") == 3]
    fractions = _fractions(second.events)
    assert fractions == sorted(fractions) and fractions[-1] == 1.0


def test_no_targets_skips_concept_unit(tmp_path, mem):
    _pipe(tmp_path).run("기준.xlsx", [])
    snap = prog.summarize(mem.events)
    assert {u.key: u.state for u in snap.units} == {
        "doc:0": prog.DONE, "concept": prog.SKIPPED}
    assert snap.fraction == 1.0
