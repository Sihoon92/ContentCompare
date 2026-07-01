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
