# Fact 속성 일반화 설계 — `Record.attributes` 통합 (F1/F2/F3 개정)

> 작성일: 2026-07-01
> 상태: **설계→구현**
> 개정 대상: [`FACT_F1_DESIGN.md`](FACT_F1_DESIGN.md)(어휘) · [`FACT_F2_DESIGN.md`](FACT_F2_DESIGN.md)(record 모델) · [`FACT_F3_DESIGN.md`](FACT_F3_DESIGN.md)(Excel fact 추출)

---

## 0. 배경과 목표

기존 F2 `Record` 는 정량 규격을 `QuantSpec(lower/target/upper/unit)` **하나**로만 담았다.
그래서 **하한/중심/상한 규격표**에는 맞지만, 아래처럼 "다른 컬럼으로 데이터가 형성된"
일반 속성표에서는 정보가 유실된다:

```
| 부품명   | 정격전압(V) | 정격전류(A) | 재질     |
| 메인모터 | 3.7         | 2.0         | 알루미늄 |
```
- 한 행에 숫자 속성이 여러 개(전압+전류) → QuantSpec 슬롯 하나에 하나만 들어가고 **나머지 유실**.
- 비수치 컬럼(재질/색상)이 여러 개 → `qualitative_spec`(1슬롯)에서 충돌, `metadata`(F3 드롭)로 소실.

**목표**: `Record` 의 정량/정성 표현을 **단일 `attributes` 맵**으로 일반화해 임의 표 구조를
무손실로 담고, `Fact` 와 표현을 통일(F3 Excel 경로를 pass-through 로 단순화)한다.
규격표 케이스(canonical 키)는 그대로 보존한다.

**핵심 원칙**: 병목은 F3 가 아니라 상류 F1 어휘 + F2 record 모양이므로 거기서 고친다.
blast radius 는 `fact/` 패키지 + 테스트에 국한(F4~F6·RAG 미영향, 확인됨).

---

## 1. 모델 변경 (`fact/record_models.py`, `fact/fact_models.py`)

`QuantSpec` + `qualitative_spec` 를 제거하고 **`Record.attributes: dict[str, Attribute]`** 로 통일.

```python
@dataclass
class Attribute:          # F2/F3 공유(단일 소스)
    value: Any = None     # 가공 안 함(숫자/문자/None)
    unit: str = ""

@dataclass
class Record:
    record_id: str
    entity: Entity
    attributes: dict[str, Attribute]   # ← quantitative_spec + qualitative_spec 대체
    metadata: dict[str, Any]           # 비교 비대상(보존). unknown 역할도 여기로.
    source: RecordSource
    evidence_text: str
    confidence: float
```

- `Attribute` 는 **`record_models.py` 에 정의**하고 `fact_models.py` 가 import(계층: F3→F2).
  기존 `fact_models.Attribute` 는 이 공유 타입으로 대체. `Fact.attributes` 와 동일 타입.
- `Record` 와 `Fact` 가 거의 동형이 되어 F3 Excel 경로가 **복사에 가까워진다**.
- `Attribute.from_dict`/`parse_attributes(raw)->dict[str,Attribute]`(빈 속성 제외)는
  record/fact 양쪽이 공유.

---

## 2. F1 어휘 확장 + attribute 이름 규칙 (`fact/semantic_roles.py`, `fact/fact_types.py`)

**`quantitative_value`** 역할 1개 추가(경계가 아닌 단일 정량 값). 컬럼 role → attribute 이름:

| 컬럼 semantic_role | attribute 키 | 예 | 근거 |
|---|---|---|---|
| `quantitative_lower_bound` | **canonical** `lower_limit` | -5 | F5 정렬 이점 |
| `quantitative_target` | **canonical** `target_value` | 25 | " |
| `quantitative_upper_bound` | **canonical** `upper_limit` | 55 | " |
| **`quantitative_value`** (신규) | **field_name** | `정격전압` | 다중 속성 무손실 |
| `qualitative_spec` | **field_name** | `재질`, `색상` | 다중 정성 허용 |
| `unit` | (속성 아님) | — | 그 행 정량 속성의 단위로 부착 |
| `entity_name`/`category`/`subcategory` | entity | — | — |
| `metadata` / `unknown` | `Record.metadata` | field_name | 보존·비교 제외(무손실) |

- 규격 경계는 canonical 3키 유지 → 규격표 fact 출력 불변.
- 일반 값/정성 컬럼은 **자신의 field_name** 이 attribute 이름(정체성 = 이름). 문서 간 이름
  정렬/동의어는 F5 + `knowledge/*.md` 의 몫(설계 유지).
- `unit` 컬럼이 있으면 그 값이 그 행 **정량 속성의 단위**. 헤더 내 단위(`정격전압(V)`)는
  F2 에서 변환하지 않고 이름에 남긴다(정규화는 F4).
- **불명(unknown)은 버리지 않고 metadata 로 보존** — 무손실 원칙.
- `fact_types.ROLE_TO_ATTR` 에 `quantitative_value` 처리 규칙을 반영(bound=canonical,
  value/qualitative=field_name 은 F2 프롬프트가 명시).

---

## 3. F2 프롬프트 / 정규화 (`fact/prompts.py`, `fact/record_normalizer.py`)

- `RECORD_SYSTEM` 출력 스키마를 교체:
  `quantitative_spec{lower,target,upper,unit}` + `qualitative_spec`
  → **`attributes: {"<이름>": {"value": <값|null>, "unit": "<단위>"}}`** + `metadata`.
- 이름 규칙(§2 표)을 프롬프트에 명시: "경계 컬럼은 lower_limit/target_value/upper_limit,
  그 외 값·정성 컬럼은 그 컬럼의 field_name 을 키로. 값은 그대로, 변환 금지."
- carry-over(상위 분류 채워내림)·source 좌표 코드 채움·행 배치·캐싱·`RECORD_VERSION` 갱신은
  **구조 변경 없이 유지**. `record_normalizer` 는 record 파싱만 새 스키마로.
- LLM 이 attributes 맵을 산출(결정: (i) LLM 명명 — 현행 F2 아키텍처 유지). 코드 결정론적
  매핑(ii)은 후속 견고화로 남긴다.

---

## 4. F3 단순화 (`fact/fact_extractor.py`)

`_facts_from_records` 가 pass-through 로 축소:
- `fact.attributes = dict(record.attributes)` (복사)
- `fact_type` = 숫자 값 속성 있으면 `quantitative_spec`, (정성만) 있으면
  `qualitative_statement`, 없으면 `descriptive`. (`_is_number(value)` 로 판정)
- `entity_name`/`entity_path`/`source`/`evidence_text`/`confidence`/`fact_id` 승계(기존과 동일)
- `search_text` = entity + path + 각 attribute 의 이름·값·단위(중복 제거)
- `_attributes_from_record`/`ATTR_*` 개별 매핑 코드 제거(더 이상 QuantSpec 없음).
  Word/PPT 경로(`_facts_from_blocks`)는 **변경 없음**(이미 자유 attributes 맵).

---

## 5. 마이그레이션 · 테스트 · DoD

**갱신 파일(코드)**: `record_models.py`·`fact_models.py`·`semantic_roles.py`·`fact_types.py`
·`prompts.py`·`record_normalizer.py`·`fact_extractor.py`(+`__init__.py` export 정리).

**갱신 테스트**: `test_fact_record_models`·`test_fact_record_normalizer`·`test_fact_extractor`
(Excel)·`test_fact_pipeline_smoke`(fake RECORD 응답). **신규 테스트**: 일반 속성표(다중
숫자 컬럼 + 다중 비수치 컬럼)가 무손실로 attributes 에 담기는지.

**DoD**:
- [x] fact 스위트 전체 통과, RAG 무회귀(엔진 기본 rag) — fact+RAG 106개 통과(2026-07-01).
- [x] **규격표 케이스(충전환경온도)의 fact 출력 불변**(canonical 키 유지) — 스모크에서 `lower_limit=-5` 확인.
- [x] **일반 속성표**: 다중 숫자(정격전압·정격전류) + 비수치(재질) 무손실 —
      `test_excel_general_attribute_table_no_loss`. (unknown→metadata 는 프롬프트 지시, 라이브 검증 대기.)
- [x] `FACT_F1/F2/F3_DESIGN.md` 모델 섹션 개정 배너 + 본 문서 링크.
- [ ] (라이브) ollama 검증은 기존 F2/F3 라이브 DoD 와 함께 대기.

---

## 6. 결정 사항 (2026-07-01)

| # | 질문 | 결정 | 근거 |
|---|------|------|------|
| G-1 | record 정량/정성 표현 | **`attributes` 단일 맵으로 통합(replace)** | 충실도·응집도·대칭성 우위(객관 분석), F4 착수 전 정리가 최선 타이밍 |
| G-2 | attribute 키 명명 | **경계=canonical, 그 외=field_name** | 규격표 정렬 이점 유지 + 일반표 무손실 |
| G-3 | 명명 주체 | **(i) LLM 산출** | 현행 F2 아키텍처 유지(변경 최소). (ii) 코드 결정론은 후속 |
| G-4 | unknown 컬럼 | **metadata 로 보존(비교 제외)** | 무손실 |
| G-5 | 단위 | **정량 속성에 unit 부착, 헤더 내 단위는 이름에 잔류** | F2 는 매핑만, 정규화는 F4 |
| G-6 | Attribute 타입 위치 | **`record_models` 정의, fact 가 import** | 단일 소스, 계층 F3→F2 |

---

## 7. 구현 순서 (TDD, 각 단계 RED→GREEN)

1. `Attribute` + `parse_attributes` 를 `record_models` 로(공유), `fact_models` 가 import.
2. `semantic_roles`: `quantitative_value` 추가(+desc/synonym).
3. `record_models.Record`: `attributes` 통합(from_llm/to_dict/from_dict) — `test_fact_record_models` 갱신.
4. `prompts.RECORD_SYSTEM`/desc: attributes 출력 스키마 + 이름 규칙, `RECORD_VERSION` 갱신.
5. `record_normalizer`: 새 스키마 파싱 — `test_fact_record_normalizer` 갱신.
6. `fact_extractor._facts_from_records`: pass-through + fact_type/search_text — `test_fact_extractor`(Excel) 갱신 + **일반 속성표 신규 테스트**.
7. `pipeline` 스모크 fake RECORD 응답 갱신 + 전체 `pytest` + RAG 무회귀.
8. `FACT_F1/F2/F3_DESIGN.md` 모델 섹션 개정.
