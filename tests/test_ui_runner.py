"""Phase 4: Streamlit UI 의 비화면 헬퍼(runner) 테스트 — streamlit 불필요."""

from __future__ import annotations

import os

from contentcompare.models import (
    Candidate,
    ComparisonResult,
    DocItem,
    DocType,
    FieldClaim,
    FieldResult,
    RecordItem,
    RecordResult,
    Verdict,
)
from contentcompare.ui import runner


# --------------------------------------------------------------------------- #
# 설정 구성
# --------------------------------------------------------------------------- #
def test_build_config_applies_overrides():
    cfg = runner.build_config(
        backend="internal", granularity="row", recall_k=12, top_k=4,
        fusion="cosine", rerank=True, chat_model="m1", embed_model="e1",
    )
    assert cfg.llm.backend == "internal"
    assert cfg.llm.chat_model == "m1"
    assert cfg.excel.granularity == "row"
    assert cfg.similarity.recall_k == 12
    assert cfg.similarity.top_k == 4
    assert cfg.similarity.fusion == "cosine"
    assert cfg.similarity.rerank is True


def test_build_config_defaults_when_none():
    cfg = runner.build_config()
    assert cfg.llm.backend == "ollama"          # 기본값 유지
    assert cfg.excel.granularity == "hybrid"


# --------------------------------------------------------------------------- #
# 입력 파일
# --------------------------------------------------------------------------- #
def test_gather_target_paths_filters_and_sorts(tmp_path):
    (tmp_path / "a.docx").write_text("x")
    (tmp_path / "b.pptx").write_text("x")
    (tmp_path / "note.txt").write_text("x")       # 미지원 → 제외
    (tmp_path / "~$lock.docx").write_text("x")     # Office 잠금 → 제외
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.xlsx").write_text("x")               # 하위폴더 포함

    found = runner.gather_target_paths(str(tmp_path))
    names = [os.path.basename(p) for p in found]
    assert names == ["a.docx", "b.pptx", "c.xlsx"]


def test_gather_target_paths_missing_dir():
    assert runner.gather_target_paths("/nope/xyz") == []


def test_save_upload_writes_file(tmp_path):
    dest = str(tmp_path / "uploads")
    path = runner.save_upload("기준.xlsx", b"hello", dest)
    assert os.path.basename(path) == "기준.xlsx"
    with open(path, "rb") as f:
        assert f.read() == b"hello"


# --------------------------------------------------------------------------- #
# 결과 집계 / 표
# --------------------------------------------------------------------------- #
def _record_result(verdicts):
    fields = [
        FieldResult(
            field=FieldClaim(f"id{i}", f"h{i}", i, str(i), f"A{i}"),
            verdict=v,
            reasoning="r",
            matched_item_ids=["t#1"] if v != Verdict.NOT_FOUND else [],
        )
        for i, v in enumerate(verdicts)
    ]
    cand = Candidate(DocItem("t#1", "t.docx", DocType.WORD, "텍스트", "t.docx 1단락"), 0.8)
    rec = RecordItem("r#2", "기준.xlsx", DocType.EXCEL, "행텍스트", "기준 2행")
    return RecordResult(record=rec, candidates=[cand], fields=fields)


def test_verdict_counts_mixed():
    r1 = _record_result([Verdict.SAME, Verdict.SAME])            # → same
    r2 = _record_result([Verdict.SAME, Verdict.DIFFERENT])       # → partial
    counts = runner.verdict_counts([r1, r2])
    assert counts[Verdict.SAME] == 1
    assert counts[Verdict.PARTIAL] == 1


def test_summary_rows_shape():
    r = _record_result([Verdict.SAME, Verdict.SAME])
    rows = runner.summary_rows([r])
    assert rows[0]["#"] == 1
    assert rows[0]["판정"] == runner.VERDICT_LABEL[Verdict.SAME]
    assert "t.docx 1단락" in rows[0]["출처"]


def test_field_rows_maps_sources():
    r = _record_result([Verdict.SAME, Verdict.NOT_FOUND])
    rows = runner.field_rows(r)
    assert len(rows) == 2
    assert rows[0]["출처"] == "t.docx 1단락"   # 매칭 있음
    assert rows[1]["출처"] == "-"               # not_found → 매칭 없음


def test_summary_rows_supports_comparison_result():
    cr = ComparisonResult(
        reference=DocItem("x", "기준.xlsx", DocType.EXCEL, "내용", "라벨"),
        verdict=Verdict.DIFFERENT,
        reasoning="사유",
    )
    rows = runner.summary_rows([cr])
    assert rows[0]["판정"] == runner.VERDICT_LABEL[Verdict.DIFFERENT]
