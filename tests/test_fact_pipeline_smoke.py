"""FactPipeline 스모크 테스트 — raw→…→facts→검증→비교→리포트 전체 경로.

COM/네트워크를 피하려고 가짜 추출기 + 가짜 chat + 가짜 embedder 를 주입한다.
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
from contentcompare.raw.ppt_raw import ShapeProbe, SlideProbe, build_ppt_doc
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


def _fake_ppt(path):
    return build_ppt_doc(os.path.basename(path), [SlideProbe(
        slide_no=1,
        shapes=[ShapeProbe(kind="text", name="본문", text="충전환경온도 -5~55℃")],
    )])


class _FactChat:
    """각 단계 프롬프트에 맞는 JSON 을 돌려주는 가짜 chat."""

    def __init__(self, ppt_facts=None):
        self.calls = 0
        self.systems: list[str] = []
        self._ppt_facts = ppt_facts if ppt_facts is not None else []

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        self.systems.append(system)
        if "판정하는 검토자" in system:  # COMPARE_SYSTEM (F5)
            return json.dumps({"result": "unknown", "reason": "테스트 보류"})
        if "비교 가능한 fact" in system:  # FACT_SYSTEM (F3, Word/PPT)
            return json.dumps({"facts": self._ppt_facts})
        if "정규화기" in system:  # RECORD_SYSTEM (F2)
            return json.dumps({"records": [{
                "record_id": "row-2",
                "entity": {"category": "", "subcategory": "", "display_name": "충전환경온도"},
                "attributes": {"lower_limit": {"value": -5, "unit": ""},
                               "upper_limit": {"value": 55, "unit": ""}},
                "metadata": {},
                "source": {"row": 2}, "evidence_text": "충전환경온도 -5 55", "confidence": 0.9,
            }]})
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


class _FakeEmbedder:
    """모든 텍스트를 같은 벡터로 → 코사인 1.0(항상 후보가 잡힌다)."""

    def embed(self, texts, *, kind="passage"):
        return [[1.0, 0.0] for _ in texts]


def _config(tmp_path, *, save=True):
    cfg = AppConfig()
    cfg.fact.artifacts_dir = str(tmp_path)
    cfg.fact.save_artifacts = save
    return cfg


def _pipe(tmp_path, *, extractor=_fake_excel, save=True, chat=None, embedder=None):
    return FactPipeline(
        _config(tmp_path, save=save),
        extractor=extractor,
        chat=chat or _FactChat(),
        embedder=embedder or _FakeEmbedder(),
    )


def _artifacts(tmp_path, name):
    return tmp_path / ArtifactStore.slug(name)


# --------------------------------------------------------------------------- #
# 문서 처리(F0~F4a)
# --------------------------------------------------------------------------- #
def test_excel_produces_all_stage_artifacts(tmp_path):
    result = _pipe(tmp_path).run("기준.xlsx", [])
    d = _artifacts(tmp_path, "기준.xlsx")
    for stage in ("physical_raw", "compact_raw", "document_profile", "table_profile",
                  "column_schema", "records", "facts", "validation_report", "run_stats"):
        assert (d / f"{stage}.json").exists(), stage

    facts = json.loads((d / "facts.json").read_text(encoding="utf-8"))
    f = facts["facts"][0]
    assert f["entity_name"] == "충전환경온도"
    assert f["attributes"]["lower_limit"]["value"] == -5
    assert f["source"]["doc_type"] == "excel"
    assert result.summaries[0]["status"] == "ok" and result.summaries[0]["facts"] == 1


def test_word_produces_facts_but_no_schema(tmp_path):
    _pipe(tmp_path, extractor=_fake_word).run("설명.docx", [])
    d = _artifacts(tmp_path, "설명.docx")
    assert (d / "facts.json").exists() and (d / "validation_report.json").exists()
    assert not (d / "table_profile.json").exists()   # Word 는 schema induction 비대상
    assert not (d / "records.json").exists()


def test_save_artifacts_false_writes_nothing(tmp_path):
    _pipe(tmp_path, save=False).run("기준.xlsx", [])
    assert list(tmp_path.iterdir()) == []


def test_close_office_called_in_finally(tmp_path, monkeypatch):
    flag = {"closed": False}
    monkeypatch.setattr(pipeline_mod, "close_all_office", lambda: flag.__setitem__("closed", True))
    _pipe(tmp_path).run("기준.xlsx", [])
    assert flag["closed"] is True


def test_progress_callback_invoked(tmp_path):
    seen = []
    _pipe(tmp_path).run("기준.xlsx", ["대상.xlsx"], progress=lambda i, n, p: seen.append((i, n, p)))
    assert seen == [(1, 2, "기준.xlsx"), (2, 2, "대상.xlsx")]


# --------------------------------------------------------------------------- #
# 비교 + 리포트(F5~F6)
# --------------------------------------------------------------------------- #
def _ppt_chat():
    return _FactChat(ppt_facts=[{
        "entity_name": "충전환경온도",
        "attributes": {"lower_limit": {"value": -5, "unit": "℃"},
                       "upper_limit": {"value": 50, "unit": "℃"}},
        "evidence_text": "충전환경온도 -5~55℃",
        "source_ids": ["s1-p001_s001"],  # 슬라이드 단위 id (fact_extractor._units_by_group)
        "confidence": 0.9,
    }])


def _excel_or_ppt(path):
    return _fake_ppt(path) if path.endswith(".pptx") else _fake_excel(path)


def test_end_to_end_compare_produces_report_and_artifact(tmp_path):
    result = _pipe(tmp_path, extractor=_excel_or_ppt, chat=_ppt_chat()).run(
        "기준.xlsx", ["발표.pptx"]
    )

    # 기준 fact 1건 × 대상 문서 1개 = 1건 판정.
    assert len(result.comparisons) == 1
    c = result.comparisons[0]
    assert c.reference_fact.entity_name == "충전환경온도"
    assert c.target_doc == "발표.pptx"
    # 상한치가 55 vs 50 → 코드가 LLM 없이 불일치로 확정.
    assert c.result == "mismatch" and c.mismatch_attributes == ["upper_limit"]
    assert c.decided_by == "code"

    saved = json.loads(
        (_artifacts(tmp_path, "기준.xlsx") / "comparison_result.json").read_text(encoding="utf-8")
    )
    assert saved["comparisons"][0]["result"] == "mismatch"
    # 양측 근거가 남아야 사람이 원문 대조로 검수할 수 있다.
    assert saved["comparisons"][0]["reference"]["evidence_text"]
    assert saved["comparisons"][0]["target"]["evidence_text"]

    assert "# 문서 비교 리포트 (fact 엔진)" in result.markdown
    assert "충전환경온도" in result.markdown and "❌ 불일치" in result.markdown


def test_compare_skipped_when_no_targets(tmp_path):
    result = _pipe(tmp_path).run("기준.xlsx", [])
    assert result.comparisons == [] and result.markdown == ""


def test_missing_when_target_has_no_facts(tmp_path):
    """대상 문서에서 fact 가 하나도 안 나오면 기준 항목은 '대상에 없음'이다."""
    result = _pipe(tmp_path, extractor=_excel_or_ppt, chat=_FactChat()).run(
        "기준.xlsx", ["발표.pptx"]
    )
    assert [c.result for c in result.comparisons] == ["missing"]


def test_compare_use_llm_false_avoids_llm(tmp_path):
    """코드 판정만 쓰는 모드 — 비교 프롬프트가 아예 나가지 않아야 한다."""
    chat = _ppt_chat()
    cfg = _config(tmp_path)
    cfg.fact.compare_use_llm = False
    pipe = FactPipeline(cfg, extractor=_excel_or_ppt, chat=chat, embedder=_FakeEmbedder())
    result = pipe.run("기준.xlsx", ["발표.pptx"])
    assert all("판정하는 검토자" not in s for s in chat.systems)
    assert result.comparisons[0].decided_by == "code"


# --------------------------------------------------------------------------- #
# 에러 격리 (F3.5 자산 유지)
# --------------------------------------------------------------------------- #
def test_one_doc_failure_does_not_stop_others(tmp_path):
    def flaky(path):
        if path == "깨진.xlsx":
            raise OSError("파일을 열 수 없습니다")  # COM 추출 실패를 흉내
        return _fake_excel(path)

    result = _pipe(tmp_path, extractor=flaky).run("기준.xlsx", ["깨진.xlsx", "대상.xlsx"])
    assert [s["status"] for s in result.summaries] == ["ok", "error", "ok"]
    bad = result.summaries[1]
    assert bad["error"].startswith("OSError:") and bad["stages"] == []
    assert (_artifacts(tmp_path, "대상.xlsx") / "facts.json").exists()
    # 실패 문서는 비교에서 빠지고 나머지 문서와는 계속 비교한다.
    assert {c.target_doc for c in result.comparisons} == {"대상.xlsx"}


def test_failure_reports_completed_stages(tmp_path):
    class _DyingChat(_FactChat):
        def complete(self, system, user, *, temperature=0.0):
            if "semantic_role" in system:  # SCHEMA 단계에서 파싱 불가 응답만 반복
                return "JSON 아님"
            return super().complete(system, user, temperature=temperature)

    result = _pipe(tmp_path, chat=_DyingChat()).run("기준.xlsx", [])
    s = result.summaries[0]
    assert s["status"] == "error" and "ValueError" in s["error"]
    assert s["stages"] == ["physical_raw", "compact_raw", "document_profile"]
    assert s["llm_calls"] > 0  # 죽기 전까지의 호출 수도 보존


def test_run_stats_artifact_has_llm_and_stage_counters(tmp_path):
    result = _pipe(tmp_path).run("기준.xlsx", [])
    stats = json.loads(
        (_artifacts(tmp_path, "기준.xlsx") / "run_stats.json").read_text(encoding="utf-8")
    )
    assert stats["llm"]["calls"] == 3  # profile + schema + record
    assert stats["llm"]["parse_failures"] == 0
    assert stats["records"] == {
        "cached": False, "rows_in": 1, "records_out": 1, "records_without_row": 0,
    }
    assert stats["facts"]["records_in"] == 1 and stats["facts"]["facts_out"] == 1
    assert stats["validation"]["facts"] == 1
    assert result.summaries[0]["stats"]["llm"]["calls"] == 3


# --------------------------------------------------------------------------- #
# 진단 계측 — 캐시가 적중해도 남아야 한다
# --------------------------------------------------------------------------- #
def test_diagnostic_artifacts_are_written(tmp_path):
    _pipe(tmp_path).run("기준.xlsx", ["설명.docx"])
    assert (_artifacts(tmp_path, "기준.xlsx") / "candidate_pairs.json").exists()
    assert (_artifacts(tmp_path, "기준.xlsx") / "facts_by_block.json").exists()


def test_diagnostics_survive_a_cache_hit(tmp_path):
    """2회차는 LLM 을 건너뛰지만 계측은 **이번 실행의 값**으로 다시 써야 한다.

    계측을 ``cached_or_compute`` 로 감싸면 캐시 히트에서 조용히 사라진다 —
    운영에서 캐시는 켜져 있는 것이 기본이므로 그게 곧 상시 부재가 된다.
    """
    _pipe(tmp_path).run("기준.xlsx", [])
    by_block = _artifacts(tmp_path, "기준.xlsx") / "facts_by_block.json"
    by_block.unlink()  # 지운 뒤 재실행 → 캐시 히트 경로에서도 다시 만들어지는가

    _pipe(tmp_path).run("기준.xlsx", [])
    assert by_block.exists()
    data = json.loads(by_block.read_text(encoding="utf-8"))
    assert data["summary"]["cached"] is True      # 추출은 건너뛰었지만
    assert data["summary"]["blocks_cited"] >= 1   # 매핑은 역산으로 살아 있다


def test_diagnostics_can_be_turned_off(tmp_path):
    cfg = _config(tmp_path)
    cfg.fact.save_candidate_pairs = False
    cfg.fact.save_facts_by_block = False
    FactPipeline(cfg, extractor=_fake_excel, chat=_FactChat(),
                 embedder=_FakeEmbedder()).run("기준.xlsx", [])
    d = _artifacts(tmp_path, "기준.xlsx")
    assert not (d / "candidate_pairs.json").exists()
    assert not (d / "facts_by_block.json").exists()


def test_candidate_pairs_artifact_records_reference_rows(tmp_path):
    _pipe(tmp_path).run("기준.xlsx", ["설명.docx"])
    data = json.loads(
        (_artifacts(tmp_path, "기준.xlsx") / "candidate_pairs.json").read_text(encoding="utf-8")
    )
    assert data["reference"] == "기준.xlsx"
    assert data["by_ref"] and data["by_ref"][0]["entity_name"] == "충전환경온도"
    assert data["by_ref"][0]["targets"][0]["doc"] == "설명.docx"


# --------------------------------------------------------------------------- #
# 구조화 출력 — 스키마가 실제로 단계별 호출에 실려 나가는가 (커밋 5)
# --------------------------------------------------------------------------- #
class _SchemaAwareFactChat(_FactChat):
    """스키마를 이해한다고 **선언한** 백엔드 대역. 받은 스키마의 title 을 모은다.

    실제 백엔드가 아니라 여기서 검사하는 이유는, 단계 함수가 ``schema_for(<이름>)`` 을
    **맞는 이름으로** 부르는지가 배선의 전부이기 때문이다 — 이름을 잘못 적으면
    ``schema_for`` 가 ``None`` 을 돌려주고 그 단계만 조용히 구조화 출력이 꺼진다.
    """

    supports_structured_output = True

    def __init__(self, ppt_facts=None):
        super().__init__(ppt_facts)
        self.schema_titles: list = []

    def complete(self, system, user, *, temperature=0.0, schema=None):
        self.schema_titles.append(schema.get("title") if schema else None)
        return super().complete(system, user, temperature=temperature)


def test_every_stage_sends_its_own_schema(tmp_path):
    """단계마다 **자기** 스키마를 달고 나가는가.

    이름을 잘못 적으면(``schema_for("프로파일러")``) ``None`` 이 돌아와 그 단계만 조용히
    구조화 출력이 꺼지는데, 런타임에는 아무 증상이 없다 — 그것을 여기서 잡는다.
    """
    pytest.importorskip("pydantic")
    chat = _SchemaAwareFactChat()
    _pipe(tmp_path, chat=chat).run("기준.xlsx", ["대상.xlsx"])

    # 최소 집합을 못박는다 — 이것이 없으면 단계가 안 불렸을 때 아래 루프가 조용히
    # 통과해(공허한 성공) 배선이 끊긴 것을 못 본다.
    assert {"profiler", "schema", "record"} <= set(chat.schema_titles)
    assert None not in chat.schema_titles     # 어느 단계도 빠지지 않았다

    seen = dict(zip(chat.systems, chat.schema_titles))
    for system, title in seen.items():
        if "semantic_role" in system:
            assert title == "schema"
        elif "판정하는 검토자" in system:
            assert title == "compare"
        elif "정규화기" in system:
            assert title == "record"
        elif "비교 가능한 fact" in system:
            assert title == "fact"
        else:
            assert title == "profiler"


def test_schema_is_not_sent_to_a_chat_without_the_flag(tmp_path):
    """기존 가짜(플래그 미선언)는 인자를 못 받는다 — 그것이 37개를 지키는 계약이다."""
    chat = _FactChat()
    _pipe(tmp_path, chat=chat).run("기준.xlsx", ["대상.xlsx"])
    assert chat.calls > 0     # TypeError 없이 끝났다는 것 자체가 증명
