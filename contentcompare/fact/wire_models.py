"""LLM 에게 **요구하는** JSON 의 모양(pydantic) — 저장 모델과 일부러 다르다.

``record_models``/``fact_models``/``schema_models``/``concept_models`` 는 **저장 포맷**이고
이 파일은 **요청 포맷**이다. 셋이 달라야 한다:

1. **코드가 덮어쓰는 필드는 여기 없다.** ``Fact.fact_id``/``source``/``search_text``
   (``fact_extractor.py`` 의 F3 추출 루프)와 ``Record.source.sheet``/``cell_range``
   (``record_normalizer.py``)는 코드가 최종 결정한다. strict 모드는 **모든 속성을
   required 로 만들기 때문에**, 여기 남겨 두면 "선택적으로 무시됨"이 아니라 "LLM 이 반드시
   지어내야 함"이 된다. 좌표 할루시네이션을 막으려고 만든 규칙을 스키마가 되돌리는 셈이다.

2. **관대함의 방향이 반대다.** ``from_llm`` 계열은 키 누락·타입오류·미허용 값에 관대해야
   하고(저장본과 폴백 경로를 둘 다 받아야 하므로), 이쪽은 "전부 있고 전부 어휘 안"을
   요구한다. 한 클래스가 둘을 동시에 할 수 없다.

3. **``attributes``/``metadata`` 는 모양이 다르다.** 저장은 map, 와이어는 배열
   (:func:`~contentcompare.fact.record_models.parse_attributes` 가 양쪽을 받는다).

⚠️ **저장 모델과 합치지 말 것.** "중복이니 하나로"는 이 파일에 대한 가장 자연스러운
리팩터링 제안이고 동시에 가장 조용한 사고다 — ``fact_id`` 가 와이어로 돌아오면 LLM 이
만든 id 를 코드가 덮어쓰는 상태가 상시화되는데, 어떤 테스트도 그걸 못 본다. 그래서
``test_fact_wire_models.py`` 가 "코드 소유 필드가 와이어에 없다"를 기계적으로 고정한다.

⚠️ **이 모델들로 응답을 검증하지 않는다.** 역할은 오직 JSON Schema 를 **서술**하는 것이다.
파싱은 계속 ``parse_json_object`` + ``from_llm`` 이 한다 — strict 가 걸린 경로는 서버가
이미 모양을 보증하므로 두 번 검사할 이유가 없고, strict 가 꺼진 경로(ollama·json_object·
강등 후)에서 pydantic 검증을 추가하면 **오늘 통과하던 응답을 새로 거절**하게 된다.
정확도는 그대로인데 가용성만 떨어지는 거래다.

**어휘를 ``Literal`` 로 좁히는 기준**: 코드가 **이미 닫힌 집합으로 강등하는** 필드만.
``fact_type``(``normalize_fact_type``) · ``semantic_role``(``normalize_role``) ·
``relation``(``RELATIONS``) · F5 ``result``(``_RESULTS``) 넷이다. ``kind``·``data_type``
처럼 프롬프트에만 예시가 있고 코드에 강등기가 없는 필드는 **자유 문자열로 둔다** — enum 으로
좁히면 모델이 "차트"를 만났을 때 정직하게 새 낱말을 쓰는 대신 ``table`` 이라고 **거짓말을
하도록 강제**된다. 잘못된 라벨은 빈 라벨보다 나쁘다.

⚠️ 이 파일은 pydantic 을 최상위에서 import 하므로 **직접 import 하지 말 것**. 단계 모듈은
:func:`~contentcompare.fact.schemas.schema_for` 만 부른다(pydantic 없는 환경 대응).
"""

from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel

# --------------------------------------------------------------------------- #
# 공통
# --------------------------------------------------------------------------- #
#: 셀 값이 가질 수 있는 타입. ``Any`` 는 strict 로 표현할 수 없어(타입 없는 노드는 거절된다)
#: 실제로 나오는 것을 나열한다. ``anyOf`` 로 표현되며 strict 가 허용한다.
#:
#: **문자열로 통일하지 않는 이유**: 그러면 ``123`` 이 ``"123"`` 으로 바뀌어 artifacts 와
#: golden 파일에 diff 가 생긴다. 이 작업의 목표는 모양을 고정하는 것이지 값을 바꾸는 것이
#: 아니다. ``int`` 를 따로 넣지 않는 것은 JSON Schema 에 ``integer``/``number`` 둘을 다
#: 넣어 봐야 ``number`` 가 정수를 포함하기 때문이다(anyOf 가 길어지기만 한다).
CellValue = Union[str, float, bool, None]


class WireAttribute(BaseModel):
    """``attributes`` 의 한 항목. 저장 시에는 ``{name: {value, unit}}`` map 이 된다.

    map 이 아니라 배열인 이유는 strict 로 **키 이름을 미리 모르는 object 를 표현할 수
    없기** 때문이다(``additionalProperties`` 가 금지된다). 부수 효과로 ``FACT_SYSTEM`` 이
    요구하는 조건별 번호 접미사(``charge_rate_1``/``_2``)가 배열에서 더 자연스러워진다.
    """

    name: str
    value: CellValue
    unit: str


class WireNamedText(BaseModel):
    """``metadata`` 처럼 자유 키 map 이던 것의 배열형. :class:`WireAttribute` 와 같은 이유."""

    name: str
    value: str


# --------------------------------------------------------------------------- #
# F1a Document Profiler
# --------------------------------------------------------------------------- #
class WireMainStructure(BaseModel):
    # ``kind`` 는 프롬프트에 "table|text" 라고 쓰여 있지만 코드에 강등기가 없어 자유
    # 문자열로 둔다(모듈 독스트링 §어휘 참고).
    kind: str
    location: str
    purpose: str
    row_grain_hint: str


class ProfilerResponse(BaseModel):
    doc_type: Literal["excel", "word", "ppt"]
    main_purpose: str
    main_structures: list[WireMainStructure]
    confidence: float


# --------------------------------------------------------------------------- #
# F1b Schema Inducer
# --------------------------------------------------------------------------- #
class WireHeaderStructure(BaseModel):
    # ``Optional`` 은 strict 에서 ``anyOf: [..., {"type": "null"}]`` 이 된다 — 모델이
    # "모른다"를 **명시적으로** 말할 수 있어야 한다. 없애면 아무 행 번호나 지어내게 된다.
    header_start_row: Optional[int]
    header_rows: int
    data_start_row: Optional[int]
    header_depth: int


class WireRowGrain(BaseModel):
    description: str
    primary_entity_columns: list[str]


class WireTableProfile(BaseModel):
    # ``location`` 없음 — ``schema_inducer`` 가 시트 이름으로 채운다.
    header_structure: WireHeaderStructure
    row_grain: WireRowGrain


class WireColumnSpec(BaseModel):
    column: str
    field_name: str
    # ⚠️ 아래 11개는 ``semantic_roles.SEMANTIC_ROLES`` 와 **같아야** 하며
    # ``test_fact_wire_models.py`` 가 그 일치를 고정한다. ``Literal[*tuple]`` 언패킹은
    # 파이썬 3.11+ 이라 손으로 적고, 어긋남은 테스트로 막는다(동적 Enum 은 타입 검사기가
    # 못 읽어 ``pyright-fixer`` 가 쓸모없어진다).
    semantic_role: Literal[
        "entity_name",
        "entity_category",
        "entity_subcategory",
        "quantitative_lower_bound",
        "quantitative_target",
        "quantitative_upper_bound",
        "quantitative_value",
        "unit",
        "qualitative_spec",
        "metadata",
        "unknown",
    ]
    data_type: str
    raw_header: list[str]


class WireColumnSchema(BaseModel):
    # ``location`` 없음 — 코드가 채운다(위와 같은 이유).
    columns: list[WireColumnSpec]


class SchemaResponse(BaseModel):
    table_profile: WireTableProfile
    column_schema: WireColumnSchema


# --------------------------------------------------------------------------- #
# F2 Record Normalizer
# --------------------------------------------------------------------------- #
class WireEntity(BaseModel):
    category: str
    subcategory: str
    display_name: str
    # ``path`` 없음 — ``Entity.from_llm`` 이 위 셋으로 합성한다. 중복 요구는 불일치만 만든다.


class WireRecordSource(BaseModel):
    """``row`` 만. ``sheet``/``cell_range`` 는 ``record_normalizer`` 가 채운다.

    ``RECORD_SYSTEM`` 이 이미 "source 에는 row 만 넣습니다"라고 쓰고 있는데, 그 지시가
    프롬프트에만 있고 스키마에는 없으면 strict 가 **프롬프트를 이기고** 코드가 신뢰하지
    않기로 한 좌표를 다시 요구하게 된다.
    """

    row: Optional[int]


class WireRecord(BaseModel):
    record_id: str
    entity: WireEntity
    attributes: list[WireAttribute]
    metadata: list[WireNamedText]
    source: WireRecordSource
    evidence_text: str
    confidence: float


class RecordsResponse(BaseModel):
    records: list[WireRecord]


# --------------------------------------------------------------------------- #
# F3 Fact Extractor
# --------------------------------------------------------------------------- #
class WireFact(BaseModel):
    """``fact_id``/``source``/``search_text`` 가 **없다** — 셋 다 코드가 결정한다.

    ``source_ids`` 는 남는다. 그건 LLM 이 고른 근거이고, 코드는 그것을 배치의 실제 id 와
    교집합으로 **검증**할 뿐(``fact_extractor``) 만들어 내지 않는다.
    """

    fact_type: Literal["quantitative_spec", "qualitative_statement", "descriptive"]
    entity_name: str
    entity_path: list[str]
    attributes: list[WireAttribute]
    evidence_text: str
    source_ids: list[str]
    inherited_from: list[str]
    confidence: float


class FactsResponse(BaseModel):
    facts: list[WireFact]


# --------------------------------------------------------------------------- #
# F7 Concept Builder
# --------------------------------------------------------------------------- #
class WirePairVerdict(BaseModel):
    left_fact_id: str
    right_fact_id: str
    relation: Literal["same_as", "differs_by", "unknown"]
    axis: str
    left_text: str
    right_text: str
    reason: str


class ConceptResponse(BaseModel):
    pairs: list[WirePairVerdict]


# --------------------------------------------------------------------------- #
# F5 Fact Comparator
# --------------------------------------------------------------------------- #
class WireFinding(BaseModel):
    fact_id: str
    # 후보 **한 건**에 대한 판정이라 ``missing`` 이 없다(그건 종합 판정에서만 뜻이 있다).
    result: Literal["match", "mismatch", "unknown"]
    mismatch_attributes: list[str]
    quote: str
    reason: str


class CompareResponse(BaseModel):
    result: Literal["match", "mismatch", "missing", "unknown"]
    mismatch_attributes: list[str]
    findings: list[WireFinding]
    reason: str


# --------------------------------------------------------------------------- #
#: 단계 이름 → 와이어 모델. :func:`~contentcompare.fact.schemas.schema_for` 가 읽는다.
#:
#: 레지스트리를 모델 **옆에** 두는 이유는 모델을 추가하면서 등록을 잊는 것을 막기 위해서다
#: (``schemas.py`` 에 두면 두 파일을 같이 고쳐야 하고, 그 동기화 실패가 이 저장소에서 이미
#: 두 번 사고를 냈다 — ``_needs_rate_limit_wrapper`` 참고).
MODEL_FOR: dict[str, type[BaseModel]] = {
    "profiler": ProfilerResponse,
    "schema": SchemaResponse,
    "record": RecordsResponse,
    "fact": FactsResponse,
    "concept": ConceptResponse,
    "compare": CompareResponse,
}
