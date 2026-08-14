"""Fact Extractor 테스트 — Excel 코드 매핑(무 LLM) + Word/PPT LLM 경로.

Excel 은 LLM 을 쓰지 않고 records → facts 규칙 매핑만 검증한다.
Word/PPT 는 FakeLLM(주입 chat)으로 배치/병합/source 검증을 확인한다(네트워크 불필요).
"""

from __future__ import annotations

import json

from contentcompare.fact.artifacts import ArtifactStore
from contentcompare.fact.fact_extractor import extract_facts
from contentcompare.fact.llm_stage import LlmRunner
from contentcompare.fact.record_models import (
    Attribute,
    Entity,
    Record,
    RecordSet,
    RecordSource,
)


# --------------------------------------------------------------------------- #
# Excel 경로 (무 LLM) — records.attributes 를 그대로 통과(pass-through)
# --------------------------------------------------------------------------- #
def test_excel_passes_through_spec_attributes():
    records = RecordSet(
        location="sheet=S",
        records=[
            Record(
                record_id="row-4",
                entity=Entity(
                    category="기본사양",
                    subcategory="충전",
                    display_name="충전환경온도",
                    path=["기본사양", "충전", "충전환경온도"],
                ),
                attributes={
                    "lower_limit": Attribute(-5, "℃"),
                    "target_value": Attribute(25, "℃"),
                    "upper_limit": Attribute(55, "℃"),
                },
                source=RecordSource(sheet="S", row=4, cell_range="D4:I4"),
                evidence_text="충전환경온도 -5 25 55 ℃",
                confidence=0.95,
            )
        ],
    )
    fs = extract_facts({"doc_type": "excel"}, records=records)
    assert len(fs.facts) == 1
    f = fs.facts[0]
    assert f.fact_id == "fact-row-4"
    assert f.fact_type == "quantitative_spec"
    assert f.entity_name == "충전환경온도"
    assert f.entity_path == ["기본사양", "충전", "충전환경온도"]
    assert f.attributes["lower_limit"].value == -5
    assert f.attributes["lower_limit"].unit == "℃"
    assert f.attributes["target_value"].value == 25
    assert f.attributes["upper_limit"].value == 55
    assert f.source == {"doc_type": "excel", "sheet": "S", "row": 4, "cell_range": "D4:I4"}
    assert f.evidence_text == "충전환경온도 -5 25 55 ℃"
    assert f.confidence == 0.95


def test_excel_general_attribute_table_no_loss():
    """규격표가 아닌 일반 속성표(다중 숫자 + 비수치 컬럼)도 무손실로 통과."""
    records = RecordSet(
        records=[
            Record(
                record_id="row-2",
                entity=Entity(display_name="메인모터", path=["메인모터"]),
                attributes={
                    "정격전압": Attribute(3.7, "V"),
                    "정격전류": Attribute(2.0, "A"),
                    "재질": Attribute("알루미늄"),
                },
                source=RecordSource(sheet="S", row=2),
            )
        ]
    )
    fs = extract_facts({"doc_type": "excel"}, records=records)
    f = fs.facts[0]
    assert f.fact_type == "quantitative_spec"  # 숫자 값 존재
    assert f.attributes["정격전압"].value == 3.7
    assert f.attributes["정격전류"].value == 2.0   # 둘째 숫자 속성 유실 없음
    assert f.attributes["재질"].value == "알루미늄"  # 비수치 속성 유실 없음


def test_excel_qualitative_only_is_qualitative_statement():
    records = RecordSet(
        records=[
            Record(
                record_id="row-7",
                entity=Entity(display_name="표면처리", path=["표면처리"]),
                attributes={"표면처리": Attribute("무전해 니켈 도금")},
                source=RecordSource(sheet="S", row=7),
            )
        ]
    )
    fs = extract_facts({"doc_type": "excel"}, records=records)
    f = fs.facts[0]
    assert f.fact_type == "qualitative_statement"  # 숫자 없음
    assert f.attributes["표면처리"].value == "무전해 니켈 도금"


def test_excel_entity_name_falls_back_to_path_tail():
    records = RecordSet(
        records=[
            Record(
                record_id="row-2",
                entity=Entity(display_name="", path=["A", "B"]),
                attributes={"target_value": Attribute(10, "mm")},
                source=RecordSource(row=2),
            )
        ]
    )
    fs = extract_facts({"doc_type": "excel"}, records=records)
    assert fs.facts[0].entity_name == "B"


def test_excel_search_text_has_entity_values_unit():
    records = RecordSet(
        records=[
            Record(
                record_id="row-4",
                entity=Entity(display_name="충전환경온도", path=["충전환경온도"]),
                attributes={"lower_limit": Attribute(-5, "℃"), "upper_limit": Attribute(55, "℃")},
                source=RecordSource(row=4),
            )
        ]
    )
    fs = extract_facts({"doc_type": "excel"}, records=records)
    st = fs.facts[0].search_text
    assert "충전환경온도" in st and "-5" in st and "55" in st and "℃" in st


def test_excel_record_without_attributes_is_descriptive():
    records = RecordSet(
        records=[
            Record(
                record_id="row-9",
                entity=Entity(display_name="비고항목", path=["비고항목"]),
                source=RecordSource(row=9),
            )
        ]
    )
    fs = extract_facts({"doc_type": "excel"}, records=records)
    f = fs.facts[0]
    assert f.fact_type == "descriptive"
    assert f.attributes == {}


def test_excel_location_carried_from_recordset():
    records = RecordSet(location="sheet=StandardList", records=[])
    fs = extract_facts({"doc_type": "excel"}, records=records)
    assert fs.location == "sheet=StandardList"
    assert fs.facts == []


# --------------------------------------------------------------------------- #
# Word/PPT 경로 (LLM, FakeLLM 주입)
# --------------------------------------------------------------------------- #
class _FactChat:
    """배치별로 큐의 JSON 을 차례로 반환하고 user 프롬프트를 캡처한다."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.user_prompts = []

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        self.user_prompts.append(user)
        return self._responses.pop(0)


_WORD = {
    "doc_type": "word",
    "file_name": "요약.docx",
    "blocks": [
        {"id": "p1", "type": "paragraph", "text": "충전환경온도는 -5~55℃ 범위로 관리한다."},
        {"id": "p2", "type": "paragraph", "text": "중심치는 25℃로 한다."},
    ],
}

_PPT = {
    "doc_type": "ppt",
    "file_name": "발표.pptx",
    "slides": [
        {
            "slide_no": 3,
            "shapes": [{"id": "sh5", "type": "text", "text": "충전환경온도: -5~55℃"}],
            "notes": "0.1C, 4.55V 조건 기준",
        }
    ],
}


def test_word_extracts_fact_with_validated_source():
    chat = _FactChat(
        [
            json.dumps(
                {
                    "facts": [
                        {
                            "fact_type": "quantitative_spec",
                            "entity_name": "충전환경온도",
                            "attributes": {
                                "lower_limit": {"value": -5, "unit": "℃"},
                                "upper_limit": {"value": 55, "unit": "℃"},
                            },
                            "evidence_text": "충전환경온도는 -5~55℃",
                            "source_ids": ["p1", "p99"],  # p99 는 입력에 없음
                            "confidence": 0.8,
                        }
                    ]
                }
            )
        ]
    )
    fs = extract_facts(_WORD, runner=LlmRunner(chat))
    assert chat.calls == 1
    assert len(fs.facts) == 1
    f = fs.facts[0]
    assert f.entity_name == "충전환경온도"
    assert f.fact_id == "fact-word-1"
    assert f.source == {"doc_type": "word", "block_ids": ["p1"]}  # 할루시네이션 id 제거
    assert "충전환경온도" in f.search_text


def test_word_fact_with_no_valid_source_is_dropped():
    chat = _FactChat(
        [
            json.dumps(
                {
                    "facts": [
                        {"entity_name": "real", "source_ids": ["p1"]},
                        {"entity_name": "ghost", "source_ids": ["nope"]},  # 근거 없음 → 드롭
                    ]
                }
            )
        ]
    )
    fs = extract_facts(_WORD, runner=LlmRunner(chat))
    assert [f.entity_name for f in fs.facts] == ["real"]


# --------------------------------------------------------------------------- #
# 계측(F3.5) — 조용한 손실(드롭)을 사유별로 센다
# --------------------------------------------------------------------------- #
def test_stats_counts_drops_by_reason():
    chat = _FactChat(
        [
            json.dumps(
                {
                    "facts": [
                        {"entity_name": "real", "source_ids": ["p1"]},
                        {"entity_name": "ghost", "source_ids": ["nope"]},  # 근거 없음
                        {"entity_name": "ghost2", "source_ids": []},       # 근거 없음
                        "문자열 fact",                                       # dict 아님
                    ]
                }
            )
        ]
    )
    stats: dict = {}
    fs = extract_facts(_WORD, runner=LlmRunner(chat), stats=stats)
    assert len(fs.facts) == 1
    assert stats["llm_facts_seen"] == 4
    assert stats["facts_out"] == 1
    assert stats["dropped_no_valid_source_id"] == 2
    assert stats["dropped_not_dict"] == 1
    assert stats["dropped_samples"] == ["ghost", "ghost2"]  # 원인 진단용 예시
    assert stats["cached"] is False
    # 커버리지: p2 는 어떤 fact 의 근거도 되지 못했다(LLM 이 그냥 안 뽑은 내용).
    assert stats["blocks_in"] == 2 and stats["blocks_cited"] == 1
    assert stats["blocks_uncited_samples"] == ["p2"]


def test_stats_excel_path_reports_records_in():
    records = RecordSet(records=[Record(record_id="row-2", entity=Entity(display_name="A"))])
    stats: dict = {}
    extract_facts({"doc_type": "excel"}, records=records, stats=stats)
    assert stats["records_in"] == 1 and stats["facts_out"] == 1


def test_word_batches_by_block_count():
    blocks = [{"id": f"p{i}", "type": "paragraph", "text": f"항목{i} 값 {i}"} for i in range(1, 4)]
    word = {"doc_type": "word", "file_name": "x.docx", "blocks": blocks}
    chat = _FactChat(
        [
            json.dumps({"facts": [{"entity_name": "A", "source_ids": ["p1"]}]}),
            json.dumps({"facts": [{"entity_name": "B", "source_ids": ["p3"]}]}),
        ]
    )
    fs = extract_facts(word, runner=LlmRunner(chat), batch_blocks=2)  # 3블록/2 → 2호출
    assert chat.calls == 2
    assert [f.entity_name for f in fs.facts] == ["A", "B"]
    assert [f.fact_id for f in fs.facts] == ["fact-word-1", "fact-word-2"]


def test_ppt_source_has_slide_shapes_and_notes():
    chat = _FactChat(
        [
            json.dumps(
                {
                    "facts": [
                        {
                            "entity_name": "충전환경온도",
                            "attributes": {"lower_limit": {"value": -5, "unit": "℃"}},
                            "source_ids": ["s3-sh5", "s3-notes"],
                            "confidence": 0.8,
                        }
                    ]
                }
            )
        ]
    )
    fs = extract_facts(_PPT, runner=LlmRunner(chat))
    f = fs.facts[0]
    assert f.fact_id == "fact-ppt-1"
    assert f.source["doc_type"] == "ppt"
    assert f.source["slide_no"] == 3
    assert f.source["shape_ids"] == ["sh5"]
    assert f.source["from_notes"] is True


def test_ppt_prompt_shows_slide_and_note():
    chat = _FactChat([json.dumps({"facts": []})])
    extract_facts(_PPT, runner=LlmRunner(chat))
    user = chat.user_prompts[0]
    assert "slide=3" in user
    assert "스피커노트" in user


def test_word_cache_hit_skips_llm(tmp_path):
    store = ArtifactStore(str(tmp_path), "요약.docx")
    chat = _FactChat([json.dumps({"facts": [{"entity_name": "A", "source_ids": ["p1"]}]})])
    extract_facts(_WORD, runner=LlmRunner(chat), store=store)
    assert (tmp_path / "요약_docx" / "facts.json").exists()
    assert chat.calls == 1
    runner2 = LlmRunner(_FactChat([]))  # 호출되면 IndexError → 캐시 히트 보장
    fs2 = extract_facts(_WORD, runner=runner2, store=store)
    assert runner2.calls == 0
    assert [f.entity_name for f in fs2.facts] == ["A"]


def test_with_context_prefixes_previous_tail():
    """두 번째 배치부터 직전 배치의 꼬리 3블록이 맥락으로 붙는다."""
    from contentcompare.fact.fact_extractor import _with_context

    batches = [
        [{"id": f"w_b{i:03d}", "type": "text", "text": "x"} for i in range(1, 6)],
        [{"id": "w_b006", "type": "text", "text": "y"}],
    ]
    out = _with_context(batches)

    assert [u["id"] for u in out[0]] == ["w_b001", "w_b002", "w_b003", "w_b004", "w_b005"]
    assert [u["id"] for u in out[1]] == ["w_b003", "w_b004", "w_b005", "w_b006"]
    assert [u.get("context") for u in out[1]] == [True, True, True, None]


def test_with_context_does_not_mutate_input():
    """원본 unit dict 에 context 를 찍으면 앞 배치의 렌더까지 오염된다."""
    from contentcompare.fact.fact_extractor import _with_context

    first = [{"id": "w_b001", "type": "text", "text": "x"}]
    _with_context([first, [{"id": "w_b002", "type": "text", "text": "y"}]])

    assert "context" not in first[0]


def test_context_table_is_truncated():
    """표를 통째로 맥락에 실으면 배치 토큰을 삼킨다 — 앞 2행만."""
    from contentcompare.fact.fact_extractor import _with_context

    tbl = {"id": "w_b001", "type": "table",
           "rows": [["a"], ["b"], ["c"], ["d"]],
           "cell_lines": [[[]], [[]], [[]], [[]]]}
    out = _with_context([[tbl], [{"id": "w_b002", "type": "text", "text": "y"}]])

    assert out[1][0]["rows"] == [["a"], ["b"]]
    assert len(out[1][0]["cell_lines"]) == 2
    assert tbl["rows"] == [["a"], ["b"], ["c"], ["d"]]      # 원본 불변


def test_context_blocks_cannot_be_fact_sources():
    """맥락 블록만 근거로 든 fact 는 기존 batch_ids 검증이 드롭한다."""
    from contentcompare.fact.fact_extractor import _facts_from_blocks

    compact = {"doc_type": "word", "blocks": [
        {"id": f"w_b{i:03d}", "type": "paragraph", "text": f"내용 {i}"}
        for i in range(1, 4)
    ]}

    class _Runner:
        def __init__(self):
            self.calls = 0

        def complete_json(self, system, user):
            self.calls += 1
            # 두 번째 배치에서 앞 배치(맥락) 블록만 근거로 든 fact 를 낸다.
            if self.calls == 2:
                return {"facts": [{"entity_name": "유령", "source_ids": ["w_b001"]}]}
            return {"facts": []}

    drops = {}
    out = _facts_from_blocks(compact, None, _Runner(), 2, drops)

    assert out.facts == []
    assert drops["dropped_no_valid_source_id"] == 1


_RAW_WITH_LINES = {"blocks": [{
    "block_id": "w_b001", "type": "paragraph", "text": "a b",
    "indent": 3,
    "lines": [{"raw_text": "a", "indent": 0}, {"raw_text": "b", "indent": 11}],
}]}


def test_units_carry_line_structure():
    """physical_raw 를 주면 unit 에 줄 구조가 실린다."""
    from contentcompare.fact.fact_extractor import _units_by_group

    compact = {"doc_type": "word",
               "blocks": [{"id": "w_b001", "type": "paragraph", "text": "a b"}]}
    groups, _ = _units_by_group(compact, lines_by_block=_RAW_WITH_LINES)
    unit = groups[0][0]

    assert unit["indent"] == 3
    assert [l["raw_text"] for l in unit["lines"]] == ["a", "b"]
    assert unit["lines"][1]["indent"] == 11


def test_units_unchanged_without_lines():
    """안 주면 예전 그대로 — PPT·Excel·옛 산출물 경로가 무변경이다."""
    from contentcompare.fact.fact_extractor import _units_by_group

    compact = {"doc_type": "word",
               "blocks": [{"id": "w_b001", "type": "paragraph", "text": "a b"}]}
    groups, _ = _units_by_group(compact)

    assert "lines" not in groups[0][0]
    assert "indent" not in groups[0][0]


def test_lines_index_ignores_single_line_blocks():
    """줄이 1개인 블록은 지문에 넣지 않는다 — 무관한 변경으로 캐시가 깨진다."""
    from contentcompare.fact.fact_extractor import _lines_index

    raw = {"blocks": [{"block_id": "w_b001", "type": "paragraph",
                       "lines": [{"raw_text": "only", "indent": 0}]}]}

    assert _lines_index(raw) == {}


def test_fingerprint_changes_with_line_structure(tmp_path):
    """같은 compact + 다른 줄 정보 → 다른 지문(안 그러면 옛 결과를 준다)."""
    from contentcompare.fact.artifacts import ArtifactStore
    from contentcompare.fact.fact_extractor import extract_facts

    compact = {"doc_type": "word",
               "blocks": [{"id": "w_b001", "type": "paragraph", "text": "a b"}]}

    class _Runner:
        def __init__(self):
            self.calls = 0

        def complete_json(self, system, user):
            self.calls += 1
            return {"facts": [{"entity_name": f"호출{self.calls}",
                               "source_ids": ["w_b001"]}]}

    runner = _Runner()
    store = ArtifactStore(str(tmp_path), "t.docx")
    extract_facts(compact, runner=runner, store=store, lines_by_block=_RAW_WITH_LINES)
    assert runner.calls == 1

    # 같은 입력 → 캐시 히트
    extract_facts(compact, runner=runner, store=store, lines_by_block=_RAW_WITH_LINES)
    assert runner.calls == 1

    # 줄 정보만 바뀜 → 재계산
    changed = {"blocks": [{"block_id": "w_b001", "type": "paragraph",
                           "lines": [{"raw_text": "a", "indent": 0},
                                     {"raw_text": "b", "indent": 20}]}]}
    extract_facts(compact, runner=runner, store=store, lines_by_block=changed)
    assert runner.calls == 2
