# Fact Phase F2 — Record Normalizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 엑셀 데이터 행 전체를 LLM 이 의미 정규화해 `records.json`(검색·비교 후보 단위)을 만드는 F2 단계를 추가한다.

**Architecture:** F1 의 `table_profile`/`column_schema` 를 입력으로 데이터 행을 `record_batch_rows` 씩 끊어 LLM 호출(carry-over 로 배치 경계 분류 연결). 결과를 `RecordSet` 으로 모아 `records` artifact 로 저장하고 시트 단위로 캐싱한다. 신규 코드는 `contentcompare/fact/` 에만 추가하고 현행 RAG 경로는 건드리지 않는다.

**Tech Stack:** Python 3, dataclass, 표준 라이브러리만(신규 의존성 없음). 테스트는 pytest + FakeLLM(chat 주입) — COM/네트워크 불필요.

**상세 설계 문서:** [`docs/FACT_F2_DESIGN.md`](../../FACT_F2_DESIGN.md)

## Global Constraints

- 🔒 현행 RAG 무수정: `pipeline.py`·`readers/`·`similarity/`·`comparison/` 변경 금지. 신규 코드는 `contentcompare/fact/` 에만.
- 모든 LLM 단계는 chat 클라이언트 주입(FakeLLM)으로 단위테스트 가능해야 한다 — COM/네트워크/Office 의존 금지.
- 좌표/행번호는 compact_raw·F1 과 동일하게 1-based 절대값(엑셀 행 `r`, 열 문자).
- entity 어휘는 `semantic_roles.py` 와 1:1: `category`/`subcategory`/`display_name`.
- F2 범위: Excel **primary 시트 1개**만. Word/PPT 는 F2 비대상(F3 에서 처리).
- 값은 셀에 있는 그대로 매핑(단위 변환·식 해석 금지 — F4/F5 로 미룸).
- `source.sheet`/`source.cell_range` 는 **코드가** 채운다(LLM 은 `row` 만) — 좌표 할루시네이션 방지.
- 커밋 메시지는 저장소 관례(Conventional Commits, 한국어 설명) 따름: `feat(fact): ...`.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `contentcompare/fact/record_models.py` (신규) | F2 산출물 dataclass: `Entity`/`QuantSpec`/`RecordSource`/`Record`/`RecordSet` (`to_dict`/`from_dict`/`from_llm`) |
| `contentcompare/fact/prompts.py` (수정) | `RECORD_VERSION`/`RECORD_SYSTEM`/`build_record_user` 추가 |
| `contentcompare/fact/record_normalizer.py` (신규) | `normalize_records(...)` — 데이터 행 선택·배치·carry-over·source 좌표·캐싱 |
| `contentcompare/config.py` (수정) | `FactConfig.record_batch_rows` 추가 |
| `contentcompare/fact/pipeline.py` (수정) | Excel 에서 F2 연결, 미구현 경계 → F3 |
| `contentcompare/fact/__init__.py` (수정) | 신규 심볼 export |
| `config/config.example.yaml` (수정) | fact 섹션에 `record_batch_rows` 주석 1줄 |
| `tests/test_fact_record_models.py` (신규) | 모델 직렬화/관대 처리 |
| `tests/test_fact_record_normalizer.py` (신규) | FakeLLM 배치/병합/carry/source/캐시 |
| `tests/test_fact_pipeline_smoke.py` (수정) | excel→`records` 생성, 경계 F3 |

---

## Task 1: Record 데이터 모델

**Files:**
- Create: `contentcompare/fact/record_models.py`
- Test: `tests/test_fact_record_models.py`

**Interfaces:**
- Consumes: 없음(표준 라이브러리만).
- Produces:
  - `Entity(category="", subcategory="", display_name="", path=[])` — `to_dict()`, `from_dict(d)`, `from_llm(d)`
  - `QuantSpec(lower=None, target=None, upper=None, unit="")` — `is_empty() -> bool`, `to_dict()`, `from_dict(d)`
  - `RecordSource(sheet="", row=None, cell_range="")` — `to_dict()`, `from_dict(d)`
  - `Record(record_id="", entity, quantitative_spec=None, qualitative_spec="", metadata={}, source, evidence_text="", confidence=0.0)` — `to_dict()`, `from_dict(d)`, `from_llm(d, *, sheet_name="")`
  - `RecordSet(location="", records=[])` — `to_dict()`, `from_dict(d)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_fact_record_models.py`:

```python
"""Record 데이터 모델 테스트 — 직렬화/관대 처리(네트워크 불필요)."""

from __future__ import annotations

from contentcompare.fact.record_models import (
    Entity,
    QuantSpec,
    Record,
    RecordSet,
    RecordSource,
)


def test_entity_from_llm_builds_path_from_nonempty_parts():
    ent = Entity.from_llm({"category": "기본사양", "subcategory": "", "display_name": "충전환경온도"})
    assert ent.path == ["기본사양", "충전환경온도"]  # 빈 subcategory 제외


def test_entity_from_llm_keeps_explicit_path():
    ent = Entity.from_llm({"category": "A", "display_name": "B", "path": ["A", "B"]})
    assert ent.path == ["A", "B"]


def test_quantspec_is_empty():
    assert QuantSpec().is_empty() is True
    assert QuantSpec(lower=-5).is_empty() is False
    assert QuantSpec(unit="℃").is_empty() is False


def test_record_from_llm_is_tolerant_and_fills_id_and_sheet():
    rec = Record.from_llm(
        {
            "entity": {"display_name": "충전환경온도"},
            "quantitative_spec": {"lower": -5, "upper": 55},
            "source": {"row": 4},
            "confidence": "0.9",  # 문자열도 관대 처리
        },
        sheet_name="StandardList",
    )
    assert rec.record_id == "row-4"            # id 미지정 → row 기반 생성
    assert rec.source.sheet == "StandardList"  # sheet 미지정 → 주입값
    assert rec.entity.display_name == "충전환경온도"
    assert rec.quantitative_spec.lower == -5
    assert rec.confidence == 0.9


def test_record_from_llm_drops_empty_quantspec():
    rec = Record.from_llm({"entity": {"display_name": "X"}, "quantitative_spec": {}, "source": {"row": 2}})
    assert rec.quantitative_spec is None  # 빈 정량규격 → None


def test_recordset_roundtrip():
    rs = RecordSet(
        location="sheet=S",
        records=[
            Record(
                record_id="row-2",
                entity=Entity(display_name="X", path=["X"]),
                quantitative_spec=QuantSpec(lower=1, upper=3, unit="℃"),
                source=RecordSource(sheet="S", row=2, cell_range="E2:F2"),
            )
        ],
    )
    again = RecordSet.from_dict(rs.to_dict())
    assert again.location == "sheet=S"
    assert again.records[0].entity.display_name == "X"
    assert again.records[0].quantitative_spec.unit == "℃"
    assert again.records[0].source.cell_range == "E2:F2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fact_record_models.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'contentcompare.fact.record_models'`

- [ ] **Step 3: Write minimal implementation**

Create `contentcompare/fact/record_models.py`:

```python
"""F2 산출물 데이터 모델 — Record / Entity / QuantSpec / RecordSource / RecordSet.

F1 ``schema_models`` 패턴을 따른다: ``to_dict``(저장용), ``from_dict``(저장본 로드),
``from_llm``(LLM 원본 dict — 키 누락·타입오류에 관대). LLM 산출 record 를 코드가
안전하게 받아 다운스트림(F3 Fact Extractor)이 그대로 쓰게 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


def _as_str(v: Any, default: str = "") -> str:
    return v if isinstance(v, str) else (default if v is None else str(v))


def _as_int(v: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@dataclass
class Entity:
    category: str = ""
    subcategory: str = ""
    display_name: str = ""
    path: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "subcategory": self.subcategory,
            "display_name": self.display_name,
            "path": self.path,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Entity":
        d = d or {}
        path = [_as_str(p) for p in (d.get("path") or []) if p]
        return cls(
            category=_as_str(d.get("category")),
            subcategory=_as_str(d.get("subcategory")),
            display_name=_as_str(d.get("display_name")),
            path=path,
        )

    @classmethod
    def from_llm(cls, d: dict) -> "Entity":
        ent = cls.from_dict(d if isinstance(d, dict) else {})
        if not ent.path:
            ent.path = [p for p in (ent.category, ent.subcategory, ent.display_name) if p]
        return ent


@dataclass
class QuantSpec:
    lower: Any = None
    target: Any = None
    upper: Any = None
    unit: str = ""

    def is_empty(self) -> bool:
        return self.lower is None and self.target is None and self.upper is None and not self.unit

    def to_dict(self) -> dict[str, Any]:
        return {"lower": self.lower, "target": self.target, "upper": self.upper, "unit": self.unit}

    @classmethod
    def from_dict(cls, d: dict) -> "QuantSpec":
        d = d or {}
        return cls(
            lower=d.get("lower"),
            target=d.get("target"),
            upper=d.get("upper"),
            unit=_as_str(d.get("unit")),
        )


@dataclass
class RecordSource:
    sheet: str = ""
    row: Optional[int] = None
    cell_range: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"sheet": self.sheet, "row": self.row, "cell_range": self.cell_range}

    @classmethod
    def from_dict(cls, d: dict) -> "RecordSource":
        d = d or {}
        return cls(
            sheet=_as_str(d.get("sheet")),
            row=_as_int(d.get("row")),
            cell_range=_as_str(d.get("cell_range")),
        )


@dataclass
class Record:
    record_id: str = ""
    entity: Entity = field(default_factory=Entity)
    quantitative_spec: Optional[QuantSpec] = None
    qualitative_spec: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    source: RecordSource = field(default_factory=RecordSource)
    evidence_text: str = ""
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "entity": self.entity.to_dict(),
            "quantitative_spec": self.quantitative_spec.to_dict() if self.quantitative_spec else None,
            "qualitative_spec": self.qualitative_spec,
            "metadata": self.metadata,
            "source": self.source.to_dict(),
            "evidence_text": self.evidence_text,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Record":
        d = d or {}
        qs = d.get("quantitative_spec")
        meta = d.get("metadata")
        return cls(
            record_id=_as_str(d.get("record_id")),
            entity=Entity.from_dict(d.get("entity") or {}),
            quantitative_spec=QuantSpec.from_dict(qs) if isinstance(qs, dict) else None,
            qualitative_spec=_as_str(d.get("qualitative_spec")),
            metadata=meta if isinstance(meta, dict) else {},
            source=RecordSource.from_dict(d.get("source") or {}),
            evidence_text=_as_str(d.get("evidence_text")),
            confidence=_as_float(d.get("confidence")),
        )

    @classmethod
    def from_llm(cls, d: dict, *, sheet_name: str = "") -> "Record":
        d = d if isinstance(d, dict) else {}
        qs = d.get("quantitative_spec")
        quant = QuantSpec.from_dict(qs) if isinstance(qs, dict) else None
        if quant is not None and quant.is_empty():
            quant = None
        source = RecordSource.from_dict(d.get("source") or {})
        if not source.sheet:
            source.sheet = sheet_name
        meta = d.get("metadata")
        rec = cls(
            record_id=_as_str(d.get("record_id")),
            entity=Entity.from_llm(d.get("entity") or {}),
            quantitative_spec=quant,
            qualitative_spec=_as_str(d.get("qualitative_spec")),
            metadata=meta if isinstance(meta, dict) else {},
            source=source,
            evidence_text=_as_str(d.get("evidence_text")),
            confidence=_as_float(d.get("confidence")),
        )
        if not rec.record_id:
            rec.record_id = f"row-{source.row}" if source.row is not None else "row-?"
        return rec


@dataclass
class RecordSet:
    location: str = ""
    records: list[Record] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"location": self.location, "records": [r.to_dict() for r in self.records]}

    @classmethod
    def from_dict(cls, d: dict) -> "RecordSet":
        d = d or {}
        recs = [Record.from_dict(r) for r in (d.get("records") or []) if isinstance(r, dict)]
        return cls(location=_as_str(d.get("location")), records=recs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_fact_record_models.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add contentcompare/fact/record_models.py tests/test_fact_record_models.py
git commit -m "feat(fact): F2 record 데이터 모델(Record/Entity/QuantSpec) 추가"
```

---

## Task 2: Record 프롬프트 + Normalizer

**Files:**
- Modify: `contentcompare/fact/prompts.py` (append)
- Create: `contentcompare/fact/record_normalizer.py`
- Test: `tests/test_fact_record_normalizer.py`

**Interfaces:**
- Consumes:
  - `Record`, `RecordSet` (Task 1)
  - `LlmRunner.complete_json(system, user)`, `fingerprint_for(*parts)` (`fact/llm_stage.py`)
  - `ArtifactStore.cached_or_compute(stage, compute, *, fingerprint)` (`fact/artifacts.py`)
  - `TableProfile`, `ColumnSchema`, `ColumnSpec` (`fact/schema_models.py`)
- Produces:
  - `RECORD_VERSION: str`, `RECORD_SYSTEM: str`, `build_record_user(batch, column_schema, table_profile, carry=None) -> str` (in `prompts.py`)
  - `normalize_records(compact, table_profile, column_schema, runner, *, batch_rows=30, store=None) -> RecordSet`

- [ ] **Step 1: Write the failing test**

Create `tests/test_fact_record_normalizer.py`:

```python
"""Record Normalizer 테스트 — FakeLLM 주입(네트워크 불필요)."""

from __future__ import annotations

import json

import pytest

from contentcompare.fact.artifacts import ArtifactStore
from contentcompare.fact.llm_stage import LlmRunner
from contentcompare.fact.prompts import build_record_user
from contentcompare.fact.record_normalizer import normalize_records
from contentcompare.fact.schema_models import (
    ColumnSchema,
    ColumnSpec,
    HeaderStructure,
    RowGrain,
    TableProfile,
)

# 헤더(행1) + 데이터(행2,3,4). D=대분류, E=항목, F=하한치.
_COMPACT = {
    "doc_type": "excel",
    "file_name": "기준.xlsx",
    "sheets": [{
        "sheet_name": "S",
        "rows": [
            {"r": 1, "cells": {"D": "대분류", "E": "항목", "F": "하한치"}},
            {"r": 2, "cells": {"D": "기본사양", "E": "충전환경온도", "F": -5}},
            {"r": 3, "cells": {"E": "방전환경온도", "F": -10}},
            {"r": 4, "cells": {"E": "저장온도", "F": -20}},
        ],
    }],
}
_TP = TableProfile(
    location="sheet=S",
    header_structure=HeaderStructure(header_start_row=1, header_rows=1, data_start_row=2),
    row_grain=RowGrain(description="행=규격 항목"),
)
_CS = ColumnSchema(location="sheet=S", columns=[
    ColumnSpec(column="D", field_name="대분류", semantic_role="entity_category"),
    ColumnSpec(column="E", field_name="항목", semantic_role="entity_name"),
    ColumnSpec(column="F", field_name="하한치", semantic_role="quantitative_lower_bound"),
])


def _rec(row, name, cat=""):
    return {
        "record_id": f"row-{row}", "source": {"row": row},
        "entity": {"category": cat, "display_name": name},
        "quantitative_spec": {"lower": None, "target": None, "upper": None, "unit": ""},
        "evidence_text": name, "confidence": 0.9,
    }


class _RecChat:
    """배치별로 큐의 JSON 을 차례로 반환하고 user 프롬프트를 캡처한다."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.user_prompts = []

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        self.user_prompts.append(user)
        return self._responses.pop(0)


def test_batches_and_merges_with_source_filled():
    # batch_rows=2 → 배치1=[행2,행3], 배치2=[행4] → 2호출.
    chat = _RecChat([
        json.dumps({"records": [_rec(2, "충전환경온도", "기본사양"), _rec(3, "방전환경온도")]}),
        json.dumps({"records": [_rec(4, "저장온도")]}),
    ])
    rs = normalize_records(_COMPACT, _TP, _CS, LlmRunner(chat), batch_rows=2)
    assert chat.calls == 2
    assert [r.record_id for r in rs.records] == ["row-2", "row-3", "row-4"]
    # source.cell_range 는 코드가 매핑 열(D,E,F) 범위로 채움.
    assert rs.records[0].source.cell_range == "D2:F2"  # 행2: D,E,F 존재
    assert rs.records[1].source.cell_range == "E3:F3"  # 행3: E,F 만 존재
    assert rs.records[0].source.sheet == "S"


def test_carry_over_passes_prior_category_to_next_batch():
    chat = _RecChat([
        json.dumps({"records": [_rec(2, "충전환경온도", "기본사양"), _rec(3, "방전환경온도")]}),
        json.dumps({"records": [_rec(4, "저장온도")]}),
    ])
    normalize_records(_COMPACT, _TP, _CS, LlmRunner(chat), batch_rows=2)
    # 두 번째 배치 프롬프트에 직전 분류(기본사양)가 주입됨.
    assert "기본사양" in chat.user_prompts[1]
    assert "직전" in chat.user_prompts[1]


def test_cache_hit_skips_llm(tmp_path):
    store = ArtifactStore(str(tmp_path), "기준.xlsx")
    chat = _RecChat([json.dumps({"records": [_rec(2, "충전환경온도", "기본사양"),
                                             _rec(3, "방전환경온도"), _rec(4, "저장온도")]})])
    normalize_records(_COMPACT, _TP, _CS, LlmRunner(chat), batch_rows=30, store=store)
    assert (tmp_path / "기준_xlsx" / "records.json").exists()
    assert chat.calls == 1
    runner2 = LlmRunner(_RecChat([]))  # 호출되면 IndexError → 캐시 히트 보장
    rs2 = normalize_records(_COMPACT, _TP, _CS, runner2, batch_rows=30, store=store)
    assert runner2.calls == 0
    assert [r.record_id for r in rs2.records] == ["row-2", "row-3", "row-4"]


def test_empty_records_batch_ok():
    chat = _RecChat([json.dumps({"records": []})])
    rs = normalize_records(_COMPACT, _TP, _CS, LlmRunner(chat), batch_rows=30)
    assert rs.records == []


def test_no_data_rows_raises():
    compact = {"doc_type": "excel", "sheets": [{"sheet_name": "S",
               "rows": [{"r": 1, "cells": {"E": "항목"}}]}]}  # 헤더만(데이터 시작행=2 미만)
    with pytest.raises(ValueError):
        normalize_records(compact, _TP, _CS, LlmRunner(_RecChat([])), batch_rows=30)


def test_build_record_user_includes_columns_rows_and_carry():
    user = build_record_user(
        [{"r": 2, "cells": {"E": "충전환경온도", "F": -5}}],
        _CS, _TP, {"category": "기본사양", "subcategory": ""},
    )
    assert "entity_name" in user        # 열 스키마 요약 포함
    assert "행 2" in user               # 데이터 행 포함
    assert "기본사양" in user           # carry 분류 포함
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fact_record_normalizer.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_record_user'` / `No module named 'contentcompare.fact.record_normalizer'`

- [ ] **Step 3a: Append record prompts to `prompts.py`**

Append to `contentcompare/fact/prompts.py` (after the Schema Inducer section):

```python
# --------------------------------------------------------------------------- #
# Record Normalizer (F2) — 데이터 행 → record
# --------------------------------------------------------------------------- #
RECORD_VERSION = "record-v1"

RECORD_SYSTEM = """\
당신은 표 데이터 정규화기입니다. 주어진 열 스키마(열 → 역할)에 따라 각 데이터 행을
record(JSON)로 변환합니다.

규칙:
- display_name 은 가장 구체적인 항목 이름(소분류 우선)으로 정합니다.
- 상위 분류(category/subcategory)가 빈 칸이면 '직전까지 확정된 분류'로 채웁니다.
- 소계·합계·빈 행은 record 로 만들지 말고 제외합니다(records 에서 빼세요).
- 값은 셀에 있는 그대로 옮깁니다(단위 변환·수식 해석 금지).
- evidence_text 는 그 행에 실제로 있는 문구만 적습니다(지어내기 금지).
- source 에는 row(행 번호)만 넣습니다. sheet/cell_range 는 코드가 채웁니다.

반드시 아래 JSON 만 출력하세요(설명·마크다운 금지):
{
  "records": [
    {
      "record_id": "row-<행번호>",
      "entity": {"category": "...", "subcategory": "...", "display_name": "..."},
      "quantitative_spec": {"lower": <값|null>, "target": <값|null>, "upper": <값|null>, "unit": "..."},
      "qualitative_spec": "...",
      "metadata": {"<필드명>": "<값>"},
      "source": {"row": <행번호>},
      "evidence_text": "...",
      "confidence": <0~1 실수>
    }
  ]
}"""


def _columns_summary(column_schema: Any) -> str:
    lines = []
    for c in column_schema.columns:
        lines.append(f"- {c.column}열: {c.field_name or '(이름없음)'} → {c.semantic_role} ({c.data_type})")
    return "\n".join(lines) if lines else "(열 스키마 없음)"


def build_record_user(batch: list, column_schema: Any, table_profile: Any, carry: Any = None) -> str:
    rows_block = "\n".join(f"행 {r.get('r')}: {r.get('cells')}" for r in batch)
    parts: list[str] = []
    if getattr(table_profile, "row_grain", None) and table_profile.row_grain.description:
        parts.append(f"[행 의미] {table_profile.row_grain.description}")
    parts.append("[열 스키마(열 → 역할)]")
    parts.append(_columns_summary(column_schema))
    if carry and (carry.get("category") or carry.get("subcategory")):
        parts.append(
            "[직전까지 확정된 분류] "
            f"category={carry.get('category', '')}, subcategory={carry.get('subcategory', '')}"
        )
    parts.append("[데이터 행]")
    parts.append(rows_block)
    body = "\n".join(parts)
    return (
        "다음 엑셀 데이터 행들을 위 열 스키마에 따라 record(JSON)로 정규화하세요.\n\n"
        f"{body}\n\n"
        "각 행을 records 배열의 한 항목으로 만들되 소계/합계/빈 행은 제외하세요. "
        "값은 셀에 있는 그대로 옮기고(변환 금지), source.row 에 행 번호를 넣으세요."
    )
```

> 주의: `RECORD_SYSTEM` 본문에 문자열 `semantic_role` 을 넣지 않습니다(스모크 테스트의 가짜 chat 이 `system` 문자열로 단계를 구분하기 때문 — Task 3 참고).

- [ ] **Step 3b: Create `record_normalizer.py`**

Create `contentcompare/fact/record_normalizer.py`:

```python
"""Record Normalizer (F2) — compact_raw + table_profile + column_schema → records.

데이터 행 전체를 LLM 이 의미 정규화해 record 리스트로 만든다. 행을 ``batch_rows`` 씩
끊어 호출하고, 배치 경계에서 상위 분류(category/subcategory)를 carry-over 로 잇는다.
``source.sheet``/``cell_range`` 는 코드가 채워 좌표 할루시네이션을 막는다(LLM 은 row 만).
시트 단위로 캐싱한다(재실행 0비용 — 결정 #2).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator, Optional

from .artifacts import ArtifactStore
from .llm_stage import LlmRunner, fingerprint_for
from .prompts import RECORD_SYSTEM, RECORD_VERSION, build_record_user
from .record_models import Record, RecordSet
from .schema_models import ColumnSchema, TableProfile

logger = logging.getLogger(__name__)


def _primary_sheet(compact: dict) -> Optional[dict]:
    """데이터(rows)가 있는 첫 비숨김 시트(F1 schema_inducer 와 동일 규칙)."""
    for sheet in compact.get("sheets", []):
        if sheet.get("rows") and not sheet.get("hidden"):
            return sheet
    return None


def _data_start_row(tp: TableProfile) -> int:
    hs = tp.header_structure
    if hs.data_start_row is not None:
        return hs.data_start_row
    if hs.header_start_row is not None:
        return hs.header_start_row + (hs.header_rows or 1)
    return 1


def _chunks(seq: list, size: int) -> Iterator[list]:
    size = max(1, size)
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _col_index(col: str) -> int:
    """엑셀 열문자 → 1-based 인덱스 (A=1, Z=26, AA=27). 비정상 문자는 뒤로."""
    idx = 0
    for ch in str(col).upper():
        if not ("A" <= ch <= "Z"):
            return 10 ** 9
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx


def _cell_range(row: dict, columns: list[str], r: int) -> str:
    """그 행에서 매핑된(존재하는) 열들의 최소~최대 열문자로 cell_range 생성."""
    cells = row.get("cells") or {}
    present = [c for c in columns if c in cells]
    if not present:
        return str(r)
    lo = min(present, key=_col_index)
    hi = max(present, key=_col_index)
    return f"{lo}{r}" if lo == hi else f"{lo}{r}:{hi}{r}"


def normalize_records(
    compact: dict,
    table_profile: TableProfile,
    column_schema: ColumnSchema,
    runner: LlmRunner,
    *,
    batch_rows: int = 30,
    store: Optional[ArtifactStore] = None,
) -> RecordSet:
    """엑셀 compact → :class:`RecordSet`. 데이터 행이 없으면 ValueError."""
    sheet = _primary_sheet(compact)
    if sheet is None:
        raise ValueError("정규화할 표(데이터 있는 시트)가 없습니다")
    sheet_name = sheet.get("sheet_name", "")
    start = _data_start_row(table_profile)
    data_rows = [r for r in sheet.get("rows", []) if (r.get("r") or 0) >= start]
    if not data_rows:
        raise ValueError("정규화할 데이터 행이 없습니다")

    location = f"sheet={sheet_name}"
    schema_columns = [c.column for c in column_schema.columns]
    fp = fingerprint_for(
        json.dumps(data_rows, sort_keys=True, ensure_ascii=False),
        json.dumps(column_schema.to_dict(), sort_keys=True, ensure_ascii=False),
        json.dumps(table_profile.to_dict(), sort_keys=True, ensure_ascii=False),
        RECORD_VERSION,
    )

    def compute() -> dict:
        records: list[Record] = []
        carry = {"category": "", "subcategory": ""}
        for batch in _chunks(data_rows, batch_rows):
            obj = runner.complete_json(
                RECORD_SYSTEM, build_record_user(batch, column_schema, table_profile, carry)
            )
            row_by_r = {r.get("r"): r for r in batch}
            batch_records: list[Record] = []
            for raw in (obj.get("records") or []):
                rec = Record.from_llm(raw, sheet_name=sheet_name)
                rec.source.sheet = sheet_name
                if rec.source.row is not None and rec.source.row in row_by_r:
                    rec.source.cell_range = _cell_range(
                        row_by_r[rec.source.row], schema_columns, rec.source.row
                    )
                batch_records.append(rec)
            # carry-over: 이 배치의 마지막 non-empty 분류를 다음 배치로 전달.
            for rec in batch_records:
                if rec.entity.category:
                    carry["category"] = rec.entity.category
                if rec.entity.subcategory:
                    carry["subcategory"] = rec.entity.subcategory
            records.extend(batch_records)
        return RecordSet(location=location, records=records).to_dict()

    if store is not None:
        data = store.cached_or_compute("records", compute, fingerprint=fp)
    else:
        data = compute()
    logger.info("[Fact] records: %s → %d records", location, len(data.get("records", [])))
    return RecordSet.from_dict(data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_fact_record_normalizer.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add contentcompare/fact/prompts.py contentcompare/fact/record_normalizer.py tests/test_fact_record_normalizer.py
git commit -m "feat(fact): F2 Record Normalizer(행 배치+carry-over+source 좌표) 추가"
```

---

## Task 3: 파이프라인 연결 + config + 스모크 테스트

**Files:**
- Modify: `contentcompare/config.py:200-220` (FactConfig)
- Modify: `config/config.example.yaml` (fact 섹션)
- Modify: `contentcompare/fact/pipeline.py`
- Modify: `contentcompare/fact/__init__.py`
- Test: `tests/test_fact_pipeline_smoke.py`

**Interfaces:**
- Consumes: `normalize_records(...)` (Task 2), `FactConfig.record_batch_rows`.
- Produces: `FactPipeline._process_one` 가 excel 에서 `records` artifact 생성. 미구현 경계 → F3.

- [ ] **Step 1: Update smoke test (failing)**

In `tests/test_fact_pipeline_smoke.py`, add a record branch to `_FactChat.complete` — insert this block **before** the `if "semantic_role" in system:` line (so the record step returns records JSON):

```python
        if "정규화기" in system:  # RECORD_SYSTEM (F2)
            return json.dumps({"records": [{
                "record_id": "row-2",
                "entity": {"category": "", "subcategory": "", "display_name": "충전환경온도"},
                "quantitative_spec": {"lower": -5, "target": None, "upper": 55, "unit": ""},
                "qualitative_spec": "", "metadata": {},
                "source": {"row": 2}, "evidence_text": "충전환경온도 -5 55", "confidence": 0.9,
            }]})
```

Then update `test_excel_produces_f1_artifacts` to also assert the records artifact, by replacing its stage tuple and adding a records check:

```python
def test_excel_produces_f1_artifacts(tmp_path):
    pipe = _pipe(tmp_path)
    with pytest.raises(NotImplementedError):
        pipe.run("기준.xlsx", [])
    d = tmp_path / ArtifactStore.slug("기준.xlsx")
    for stage in ("physical_raw", "compact_raw", "document_profile",
                  "table_profile", "column_schema", "records"):
        assert (d / f"{stage}.json").exists(), stage
    cs = json.loads((d / "column_schema.json").read_text(encoding="utf-8"))
    assert cs["columns"][0]["semantic_role"] == "entity_name"
    recs = json.loads((d / "records.json").read_text(encoding="utf-8"))
    assert recs["records"][0]["entity"]["display_name"] == "충전환경온도"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fact_pipeline_smoke.py::test_excel_produces_f1_artifacts -q`
Expected: FAIL — `records` 단계 파일이 없어 `assert ... , 'records'` 실패.

- [ ] **Step 3a: Add config field**

In `contentcompare/config.py`, inside `FactConfig` (after `max_llm_calls_per_doc`), add:

```python
    record_batch_rows: int = 30
    """F2 Record Normalizer 의 행 배치 크기(한 LLM 호출당 처리 행 수)."""
```

- [ ] **Step 3b: Document in example yaml**

In `config/config.example.yaml`, find the `fact:` section (search for `max_llm_calls_per_doc`) and add directly below it:

```yaml
  record_batch_rows: 30        # F2: 한 LLM 호출당 정규화할 데이터 행 수
```

- [ ] **Step 3c: Wire F2 into the pipeline**

In `contentcompare/fact/pipeline.py`, update imports and `_process_one`/`_not_yet_implemented`:

Replace the import line `from .schema_inducer import induce_schema` with:

```python
from .record_normalizer import normalize_records
from .schema_inducer import induce_schema
```

Replace the Excel branch in `_process_one` (the `if compact.get("doc_type") == "excel":` block) with:

```python
        if compact.get("doc_type") == "excel":
            tp, cs = induce_schema(compact, profile, runner, store)
            stages += ["table_profile", "column_schema"]
            normalize_records(
                compact, tp, cs, runner,
                batch_rows=self.fact.record_batch_rows, store=store,
            )
            stages += ["records"]
```

Replace `_not_yet_implemented` body with the F3 boundary message:

```python
    @staticmethod
    def _not_yet_implemented() -> None:
        raise NotImplementedError(
            "FactPipeline: Fact Extractor~Comparator 는 Phase F3~F6 에서 구현됩니다. "
            "현재(F0~F2)는 raw/compact/profile/schema/records artifacts 저장까지 동작합니다."
        )
```

Also update the module docstring boundary note (the `Record Normalizer → ... → Comparator (F2~F6, 미구현)` line) to `Fact Extractor → ... → Comparator (F3~F6, 미구현)` and the `run` docstring mention of F2 → F3.

- [ ] **Step 3d: Export new symbols**

In `contentcompare/fact/__init__.py`, add imports and `__all__` entries:

```python
from .record_models import Entity, QuantSpec, Record, RecordSet, RecordSource
from .record_normalizer import normalize_records
```

Add to `__all__`: `"normalize_records"`, `"Record"`, `"RecordSet"`, `"Entity"`, `"QuantSpec"`, `"RecordSource"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_fact_pipeline_smoke.py -q`
Expected: PASS (all smoke tests, including updated excel + records assertion)

- [ ] **Step 5: Commit**

```bash
git add contentcompare/config.py config/config.example.yaml contentcompare/fact/pipeline.py contentcompare/fact/__init__.py tests/test_fact_pipeline_smoke.py
git commit -m "feat(fact): FactPipeline 에 F2 연결 + record_batch_rows 설정, 경계 F3 이동"
```

---

## Task 4: 문서 갱신 + 전체 검증

**Files:**
- Modify: `docs/FACT_PIPELINE_PLAN.md` (§9 로드맵)
- Modify: `docs/FACT_F2_DESIGN.md` (§8 DoD 체크)

**Interfaces:** 없음(문서·검증).

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest tests/test_fact_*.py -q`
Expected: PASS — F1 51개 + 신규(record_models 6 + record_normalizer 6) + 갱신된 smoke. 회귀 없음.

- [ ] **Step 2: Confirm RAG no-regression**

Run: `python -m pytest -q`
Expected: 기존 통과 유지(엔진 기본 rag). 신규 실패 없음(기존 ONNX 관련 1건은 F0 이전부터 있던 무관 이슈 — 새로 깨진 것이 없는지만 확인).

- [ ] **Step 3: Update roadmap doc**

In `docs/FACT_PIPELINE_PLAN.md` §9, change the F2 line from:

```markdown
- **Phase F2 — Record Normalizer** → `records.json`.
```

to:

```markdown
- **Phase F2 — Record Normalizer** ✅ **완료(2026-06-30)** ([상세 설계: `FACT_F2_DESIGN.md`](FACT_F2_DESIGN.md))
  - Excel primary 시트의 데이터 행을 LLM 이 의미 정규화 → `records.json`. 행 배치(`record_batch_rows`=30)+carry-over, source 좌표는 코드가 채움.
  - 미구현 경계가 F3 로 이동. Word/PPT 는 F3 에서 블록→fact 직행.
```

- [ ] **Step 4: Check DoD boxes in design doc**

In `docs/FACT_F2_DESIGN.md` §8, change the four `- [ ]` DoD items to `- [x]` for the automated ones (단위테스트 통과, RAG 무회귀, F2 완료 표기). Leave the ollama 라이브 항목as `- [ ]` until the manual run below is done.

- [ ] **Step 5: Manual live validation (사용자 PC, Office+ollama 필요)**

이 단계는 자동화 불가(COM+LLM). 사용자가 실행:

Run: `python scripts/dump_raw.py 기준.xlsx --compact -o out/compact.json` (raw/compact 정상 확인)
Run: `contentcompare --engine fact --config config/config.yaml --reference 기준.xlsx --targets 기준.xlsx --out /dev/null`
Expected: `artifacts/기준_xlsx/records.json` 생성, entity/quantitative_spec/source 가 column_schema 와 정합. 행 많은 표에서 배치 분할 동작.
확인되면 §8 DoD 의 ollama 항목을 `- [x]` 로 체크.

- [ ] **Step 6: Commit**

```bash
git add docs/FACT_PIPELINE_PLAN.md docs/FACT_F2_DESIGN.md
git commit -m "docs(fact): F2 완료 표기(로드맵 §9, F2 DoD)"
```

---

## Self-Review

**1. Spec coverage** (FACT_F2_DESIGN.md 대비):
- §1 신규/변경 파일 9개 → Task 1(record_models), Task 2(prompts+normalizer), Task 3(config/pipeline/__init__/smoke), Task 4(docs). 전부 커버. ✅
- §2 records.json 스키마 → Task 1 모델 + Task 2 normalizer 가 산출. ✅
- §3 record_models → Task 1. ✅
- §4 normalizer(배치/carry/source 좌표/캐싱) → Task 2 + 테스트 ①~⑥. ✅
- §5 프롬프트 → Task 2 Step 3a. ✅
- §6 FactPipeline 연결 → Task 3 Step 3c. ✅
- §7 config record_batch_rows → Task 3 Step 3a/3b. ✅
- §8 테스트/DoD → Task 1/2/3 테스트 + Task 4 검증. ✅

**2. Placeholder scan:** 모든 코드 스텝에 실제 코드 포함. "TBD/적절히 처리" 없음. ✅ (Step 5 라이브 검증은 사용자 환경 의존이라 수동 — 자동화 불가를 명시.)

**3. Type consistency:**
- `normalize_records(compact, table_profile, column_schema, runner, *, batch_rows=30, store=None)` — Task 2 정의 ↔ Task 3 호출(`normalize_records(compact, tp, cs, runner, batch_rows=..., store=store)`) 인자 순서/이름 일치. ✅
- `Record.from_llm(d, *, sheet_name=...)`, `RecordSet.from_dict`/`to_dict` — Task 1 정의 ↔ Task 2 사용 일치. ✅
- `build_record_user(batch, column_schema, table_profile, carry=None)` — Task 2 정의 ↔ 테스트 호출 일치. ✅
- 스모크 fake chat 분기 마커 `"정규화기"` ↔ `RECORD_SYSTEM` 첫 문장에 "정규화기" 포함, `RECORD_SYSTEM` 에 `semantic_role` 문자열 미포함(주의 노트) — 분기 충돌 없음. ✅
