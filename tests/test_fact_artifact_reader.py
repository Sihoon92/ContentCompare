"""실행 산출물 리더 테스트 — 파일시스템만 쓰고 LLM/Office/네트워크 불필요.

이 리더의 계약은 하나다: **어떤 파일이 없어도 예외를 던지지 않는다.** 진단 도구가
진단 대상의 불완전함 때문에 죽으면 쓸모가 없다. 그래서 아래 테스트의 절반은
"없을 때 어떻게 되는가"다.
"""

from __future__ import annotations

import json

from contentcompare.fact.artifact_reader import (
    DocArtifacts,
    GraphIndex,
    RunRef,
    attributes_text,
    list_runs,
    load_run,
    load_snapshot,
    low_confidence_ids,
    split_pair_id,
    target_of,
)
from contentcompare.fact.concept_models import ConceptGraph


def _write(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _comparison(entity="공칭전압", target_doc="규격서.docx", result="missing",
                decided_by="code", **kw) -> dict:
    out = {
        "entity_name": entity, "target_doc": target_doc, "result": result,
        "mismatch_attributes": [], "match_score": 0.0, "match_method": "none",
        "decided_by": decided_by, "reason": "사유",
        "reference": {"fact_id": "fact-row-7", "entity_name": entity,
                      "attributes": {"target_value": {"value": 3.89, "unit": ""}},
                      "evidence_text": "3.89",
                      "source": {"doc_type": "excel", "sheet": "S", "row": 7,
                                 "cell_range": "B7:P7"}},
        "target": None,
    }
    out.update(kw)
    return out


def _run(tmp_path, *, comparisons=None, graph=None, pairs=None,
         ref_name="기준.xlsx", ref_slug="기준_xlsx"):
    d = tmp_path / ref_slug
    _write(d / "comparison_result.json",
           {"reference": ref_name, "stats": {"comparisons": 1},
            "comparisons": comparisons if comparisons is not None else [_comparison()]})
    if graph is not None:
        _write(d / "concept_graph.json", graph)
    if pairs is not None:
        _write(d / "candidate_pairs.json", pairs)
    return d


# --------------------------------------------------------------------------- #
# 실행 찾기
# --------------------------------------------------------------------------- #
def test_empty_root_yields_no_runs(tmp_path):
    assert list_runs(tmp_path) == []
    assert list_runs(tmp_path / "없는폴더") == []


def test_run_is_found_by_comparison_result(tmp_path):
    _run(tmp_path)
    (tmp_path / "규격서_docx").mkdir()  # 대상 문서 폴더는 실행이 아니다
    assert [r.label for r in list_runs(tmp_path)] == ["기준_xlsx"]


def test_snapshots_are_listed_and_flagged(tmp_path):
    _run(tmp_path)
    _write(tmp_path / "_runs" / "예전실행" / "comparison_result.json",
           {"reference": "기준.xlsx", "comparisons": []})
    labels = {r.label: r.is_snapshot for r in list_runs(tmp_path)}
    assert labels == {"기준_xlsx": False, "_runs/예전실행": True}


def test_traces_dir_is_not_mistaken_for_a_run(tmp_path):
    _run(tmp_path)
    (tmp_path / "_traces" / "실행1").mkdir(parents=True)
    assert [r.label for r in list_runs(tmp_path)] == ["기준_xlsx"]


# --------------------------------------------------------------------------- #
# 로딩 — 없어도 죽지 않는다
# --------------------------------------------------------------------------- #
def test_missing_comparison_result_is_reported_not_raised(tmp_path):
    snap = load_snapshot(RunRef(label="x", dir=tmp_path / "없음"))
    assert snap.comparisons == []
    assert any("comparison_result" in p for p in snap.problems)


def test_corrupt_json_is_reported_not_raised(tmp_path):
    d = tmp_path / "기준_xlsx"
    d.mkdir()
    (d / "comparison_result.json").write_text("{깨진", encoding="utf-8")
    snap = load_snapshot(RunRef(label="기준_xlsx", dir=d))
    assert snap.comparisons == []
    assert snap.problems


def test_missing_graph_and_pairs_are_reported(tmp_path):
    d = _run(tmp_path)
    snap = load_snapshot(RunRef(label="기준_xlsx", dir=d))
    assert snap.graph is None
    assert snap.candidate_pairs is None
    assert any("concept_graph" in p for p in snap.problems)
    assert any("candidate_pairs" in p for p in snap.problems)
    assert "cause_recall" not in snap.capabilities


def test_reference_and_targets_are_resolved(tmp_path):
    d = _run(tmp_path)
    _write(tmp_path / "규격서_docx" / "compact_raw.json",
           {"doc_type": "word", "file_name": "규격서.docx"})
    snap = load_snapshot(RunRef(label="기준_xlsx", dir=d))
    assert snap.reference_doc == "기준.xlsx"
    assert snap.target_docs == ["규격서.docx"]
    assert snap.doc("규격서.docx").doc_type == "word"


def test_snapshot_does_not_borrow_current_target_folders(tmp_path):
    """fact_id 는 실행마다 다시 매겨진다 — 현재 폴더와 섞으면 엉뚱한 fact 를 가리킨다."""
    _write(tmp_path / "규격서_docx" / "facts.json", {"facts": []})
    d = tmp_path / "_runs" / "예전"
    _write(d / "comparison_result.json",
           {"reference": "기준.xlsx", "comparisons": [_comparison()]})

    snap = load_snapshot(RunRef(label="_runs/예전", dir=d, is_snapshot=True))
    assert snap.doc("규격서.docx") is None
    assert any("fact_id" in p for p in snap.problems)
    assert "learn" not in snap.capabilities


def test_capabilities_reflect_what_is_present(tmp_path):
    d = _run(tmp_path, graph={"nodes": [], "edges": [], "stats": {}},
             pairs={"by_ref": []})
    _write(d / "facts.json", {"facts": []})
    _write(tmp_path / "규격서_docx" / "facts_by_block.json", {"blocks": []})
    snap = load_snapshot(RunRef(label="기준_xlsx", dir=d))
    assert {"debug", "learn", "graph", "cause_recall", "cause_extract"} <= snap.capabilities


def test_load_run_picks_most_recent_by_default(tmp_path):
    _run(tmp_path, ref_slug="첫번째")
    second = _run(tmp_path, ref_slug="두번째")
    import os
    os.utime(second / "comparison_result.json", (2 << 30, 2 << 30))
    assert load_run(tmp_path).label == "두번째"
    assert load_run(tmp_path, "첫번째").label == "첫번째"


def test_load_run_on_empty_root_returns_none(tmp_path):
    assert load_run(tmp_path) is None


# --------------------------------------------------------------------------- #
# 조회 헬퍼
# --------------------------------------------------------------------------- #
def test_available_ignores_fingerprint_sidecars(tmp_path):
    d = tmp_path / "doc"
    d.mkdir()
    (d / "facts.json").write_text("{}", encoding="utf-8")
    (d / "facts.fingerprint").write_text("abc", encoding="utf-8")
    assert DocArtifacts(doc_name="d", dir=d).available == {"facts"}


def test_ranked_for_finds_the_right_slot(tmp_path):
    pairs = {"by_ref": [
        {"ref_fact_id": "fact-row-7", "entity_name": "공칭전압", "targets": [
            {"doc": "규격서.docx", "ranked": [{"fact_id": "f1"}], "from_ontology": []},
            {"doc": "발표.pptx", "ranked": [], "from_ontology": ["f9"]},
        ]},
    ]}
    d = _run(tmp_path, pairs=pairs)
    snap = load_snapshot(RunRef(label="기준_xlsx", dir=d))
    assert snap.ranked_for("fact-row-7", "발표.pptx")["from_ontology"] == ["f9"]
    assert snap.ranked_for("fact-row-7", "없는문서") is None
    assert snap.ranked_for("없는fact", "규격서.docx") is None


def test_comparisons_for_filters_by_target(tmp_path):
    d = _run(tmp_path, comparisons=[
        _comparison(target_doc="A.docx"), _comparison(target_doc="B.pptx")])
    snap = load_snapshot(RunRef(label="기준_xlsx", dir=d))
    assert len(snap.comparisons_for("A.docx")) == 1
    assert len(snap.comparisons_for()) == 2


# --------------------------------------------------------------------------- #
# 스키마 함정
# --------------------------------------------------------------------------- #
def test_target_is_none_when_missing():
    assert target_of(_comparison(result="missing")) is None
    assert target_of(_comparison(result="match", target={"fact_id": "f1"})) == {"fact_id": "f1"}


def test_low_confidence_ids_are_derived_from_checks():
    """이 목록은 JSON 에 없다 — checks 의 severity 로 되살려야 한다."""
    report = {
        "overall": {"facts": 3, "error": 2, "warn": 1, "low_confidence": 2},
        "checks": [
            {"check": "unit_missing", "severity": "warn", "fact_id": "f1"},
            {"check": "source_invalid", "severity": "error", "fact_id": "f2"},
            {"check": "evidence_absent", "severity": "error", "fact_id": "f3"},
        ],
    }
    assert low_confidence_ids(report) == {"f2", "f3"}
    assert low_confidence_ids(None) == set()
    assert low_confidence_ids({}) == set()


def test_split_pair_id_handles_the_synthetic_key():
    got = split_pair_id("spec_en.docx#fact-word-6 ↔ 기준.xlsx#fact-row-4")
    assert got == (("spec_en.docx", "fact-word-6"), ("기준.xlsx", "fact-row-4"))
    assert split_pair_id("fact-row-4") is None
    assert split_pair_id("a ↔ b") is None       # '#' 이 없다
    assert split_pair_id("") is None


def test_attributes_text_keeps_free_form_keys():
    assert attributes_text({"참조규격": {"value": "SEC 4.7", "unit": ""}}) == "참조규격=SEC 4.7"
    assert attributes_text({"target_value": {"value": 3.85, "unit": "V"}}) == "target_value=3.85V"
    assert attributes_text({}) == "-"
    assert attributes_text(None) == "-"


# --------------------------------------------------------------------------- #
# GraphIndex
# --------------------------------------------------------------------------- #
GRAPH = {
    "nodes": [{"concept_id": "c-0001", "label": "공칭전압", "members": [
        {"doc": "기준.xlsx", "fact_id": "fact-row-7", "entity_name": "공칭전압"},
        {"doc": "규격서.docx", "fact_id": "fact-word-4", "entity_name": "Nominal voltage"},
    ]}],
    "edges": [
        {"relation": "same_as",
         "left": {"doc": "기준.xlsx", "fact_id": "fact-row-7"},
         "right": {"doc": "규격서.docx", "fact_id": "fact-word-4"},
         "decided_by": "llm", "recall_score": 0.75},
        {"relation": "differs_by", "axis": "물리량",
         "left": {"doc": "기준.xlsx", "fact_id": "fact-row-7"},
         "right": {"doc": "발표.pptx", "fact_id": "fact-ppt-1"},
         "decided_by": "ontology", "promoted": True},
    ],
    "stats": {},
}


def test_graph_index_answers_node_and_edge_queries():
    idx = GraphIndex(ConceptGraph.from_dict(GRAPH))
    assert idx.node_of("기준.xlsx", "fact-row-7").concept_id == "c-0001"
    assert idx.node_of("기준.xlsx", "없음") is None
    assert idx.partners("기준.xlsx", "fact-row-7", "규격서.docx") == ["fact-word-4"]
    assert len(idx.edges_touching("기준.xlsx", "fact-row-7")) == 2
    assert [e.relation for e in idx.edges_between("기준.xlsx", "fact-row-7", "발표.pptx")] \
        == ["differs_by"]


def test_graph_index_reports_edge_position_for_humans():
    graph = ConceptGraph.from_dict(GRAPH)
    idx = GraphIndex(graph)
    assert idx.edge_index(graph.edges[1]) == 1


def test_graph_index_tolerates_no_graph():
    idx = GraphIndex(None)
    assert idx.node_of("a", "b") is None
    assert idx.edges_touching("a", "b") == []
    assert idx.partners("a", "b", "c") == []
