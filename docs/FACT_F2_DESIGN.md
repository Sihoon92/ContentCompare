# Phase F2 상세 설계 — Record Normalizer (LLM)

> 작성일: 2026-06-30
> 상태: **설계→구현** — F2 의 파일·JSON 스키마·프롬프트·테스트를 고정한다.
> 상위 계획: [`FACT_PIPELINE_PLAN.md`](FACT_PIPELINE_PLAN.md) (§4 Step 3, §9 Phase F2)
> 선행: [`FACT_F1_DESIGN.md`](FACT_F1_DESIGN.md) (compact_raw → document_profile/table_profile/column_schema 완료)

---

## 0. F2 범위와 목표

F2 는 **두 번째 LLM 단계**다. F1 이 추론한 `table_profile`(헤더/데이터 시작행)과
`column_schema`(열 → `semantic_role`)를 입력으로, **데이터 행 전체**를 LLM 이 의미
정규화해 `records.json` 을 만든다. 행 = 검색·비교의 후보 단위이며, F3(Fact Extractor)
의 입력이 된다.

### In-scope
1. **Record Normalizer** (LLM) — `compact_raw` + `table_profile` + `column_schema`
   → `records.json`. 각 데이터 행을 `entity` + `quantitative_spec` + `qualitative_spec`
   + `metadata` + `source` 로 정규화.
2. **행 배치 호출** — 데이터 행을 `record_batch_rows`(기본 30)씩 끊어 배치 단위로 LLM
   호출. 시트당 호출 수 = `ceil(데이터행수 / batch)`. 작은 표는 1회.
3. **배치 경계 분류 채워내림(carry-over)** — 병합셀로 표현된 상위 분류(대/중분류)가
   배치 경계에서 끊기지 않도록, 직전까지 확정된 category/subcategory 를 다음 배치
   프롬프트에 컨텍스트로 전달.
4. **record 데이터 모델** — `Record`/`Entity`/`QuantSpec`/`RecordSource`/`RecordSet`
   (`to_dict`/`from_dict`/`from_llm`, F1 모델 패턴 재사용).

### LLM 의 실제 역할 (단순 전사가 아님)
- 병합셀 대분류/중분류 **채워내림**(merged_cells 힌트 기반).
- 여러 entity 열 중 **가장 구체적인 이름**을 `display_name` 으로 선정.
- 소계/합계/빈 행 등 **레코드가 아닌 행 제외**(빈 배열 허용).
- 행마다 `source.row` 와 `evidence_text`(raw 에 실제 존재하는 문구) 부착.

### Out-of-scope (F3+)
- Fact Extractor(F3), Validator/Repair(F4), Comparator(F5).
- **Word/PPT 는 F2 비대상.** column_schema 가 없으므로(자유 텍스트) record 정규화를
  하지 않고, F3 에서 블록/도형 → fact 로 직행한다(결정 #4: 엑셀 기준, F1 선례).
- **단위 등가(도씨 ≡ ℃)·표현식 해석(`25±30` → lower/upper)·정규화는 F2 가 하지
  않는다.** F2 는 column_schema 역할대로 값을 **가공 없이** 매핑만 하고, 등가/검증은
  F4(Validator), 교차 비교는 F5(Comparator)로 미룬다.

### 제약
- 🔒 RAG 무수정. 신규 코드는 `fact/` 에만 추가. `readers/`·`similarity/`·`comparison/`
  ·`pipeline.py` 변경 없음.
- LLM 없이 테스트: 모든 단계는 **chat 클라이언트 주입**(FakeLLM)으로 단위테스트
  (CLAUDE.md 규칙). COM/네트워크 불필요.
- **다중 시트**: F1 과 동일하게 primary 시트 1개만 처리(한계 기록). F1 의 `column_schema`
  ·`table_profile` 이 그 시트 기준이므로 F2 도 같은 시트에 종속.

---

## 1. 신규/변경 파일

| 파일 | 구분 | 내용 |
|---|---|---|
| `fact/record_models.py` | ✅ 신규 | `Entity`/`QuantSpec`/`RecordSource`/`Record`/`RecordSet` dataclass (`to_dict`/`from_dict`/`from_llm`) |
| `fact/record_normalizer.py` | ✅ 신규 | `normalize_records(compact, table_profile, column_schema, runner, *, batch_rows, store=None)` — 행 배치·LLM 호출·병합·캐싱 |
| `fact/prompts.py` | 🔧 수정 | `RECORD_SYSTEM` + `build_record_user(batch_rows, column_schema, table_profile, carry)` + `RECORD_VERSION` |
| `fact/pipeline.py` | 🔧 수정 | Excel 에서 `induce_schema` 뒤 `normalize_records` 연결, 미구현 경계 → **F3** |
| `fact/__init__.py` | 🔧 수정 | 신규 공개 심볼 export |
| `config.py` `FactConfig` | 🔧 수정 | `record_batch_rows: int = 30` 추가 |
| `tests/test_fact_record_models.py` | ✅ 신규 | `from_llm` 관대 처리/직렬화/`path` 생성 |
| `tests/test_fact_record_normalizer.py` | ✅ 신규 | FakeLLM 배치/병합/캐시/source |
| `tests/test_fact_pipeline_smoke.py` | 🔧 수정 | excel → `records` artifact 생성, word/ppt 미생성, 경계 F3 |

> 신규 의존성 없음. chat 클라이언트·`LlmRunner`·`ArtifactStore` 는 F1 그대로 재사용.

---

## 2. records.json 스키마

좌표/행번호는 compact_raw·F1 과 동일하게 **1-based 절대값**(엑셀 행 `r`, 열 문자).

```json
{
  "location": "sheet=StandardList",
  "records": [
    {
      "record_id": "row-4",
      "entity": {
        "category": "기본사양",
        "subcategory": "충전",
        "display_name": "충전환경온도",
        "path": ["기본사양", "충전", "충전환경온도"]
      },
      "quantitative_spec": {"lower": -5, "target": 25, "upper": 55, "unit": "℃"},
      "qualitative_spec": "",
      "metadata": {"순번": "12"},
      "source": {"sheet": "StandardList", "row": 4, "cell_range": "D4:I4"},
      "evidence_text": "충전환경온도 -5 25 55 ℃",
      "confidence": 0.95
    }
  ]
}
```

- `entity` 는 F1 어휘(`entity_category`/`entity_subcategory`/`entity_name`)와 1:1 대응.
  계획 §4 의 `entity{category/mid/sub/display_name}` 를 F1 어휘에 맞춰 `category` /
  `subcategory` / `display_name` 로 **단순화**한다(중·소분류는 `subcategory` 로 통합 —
  `semantic_roles.py` 가 `entity_subcategory` 하나로 매핑하므로 일관).
- `path` = `[category, subcategory, display_name]` 중 **비어있지 않은 부분만** 순서대로
  (F3 fact `entity_path`·F5 매칭 편의용).
- `quantitative_spec` = column_schema 의 `quantitative_lower_bound`/`_target`/`_upper`
  /`unit` 역할 열을 **가공 없이** 매핑. 값이 없는 키는 `null`. 정량 열이 없는 행이면
  `quantitative_spec` 전체가 빈 객체/`null` 가능.
- `qualitative_spec` = `qualitative_spec` 역할 열의 텍스트(조건/비고/설명).
- `metadata` = `metadata` 역할 열들의 `{field_name: 값}`(비교 비대상, 추적용).
- `source.row` = 절대 행번호 `r`. `source.sheet`/`source.cell_range` 는 **코드가 채운다**
  (§4.4 — 좌표 할루시네이션 방지). LLM 은 `row` 와 의미 필드만 책임진다.
- `evidence_text` = 해당 행 raw 에 실제 존재하는 문구만(지어내기 금지). F4 source
  검증과 연동.
- `confidence` = 0~1 실수(행 정규화 확신도).

> dataclass 는 F1 과 동일하게 `to_dict`/`from_dict`/`from_llm`(LLM 원본 dict 의 키
> 누락·오타·미허용 값에 관대) 제공.

---

## 3. record_models (`fact/record_models.py`)

```python
@dataclass
class Entity:
    category: str = ""
    subcategory: str = ""
    display_name: str = ""
    path: list[str] = field(default_factory=list)
    # from_llm: path 미지정 시 [category, subcategory, display_name] 비어있지 않은 부분으로 생성

@dataclass
class QuantSpec:
    lower: Any = None      # number | str | None (가공 안 함)
    target: Any = None
    upper: Any = None
    unit: str = ""
    # is_empty(): 네 필드 모두 비면 True → records 에서 생략 가능

@dataclass
class RecordSource:
    sheet: str = ""
    row: Optional[int] = None
    cell_range: str = ""

@dataclass
class Record:
    record_id: str
    entity: Entity
    quantitative_spec: Optional[QuantSpec]
    qualitative_spec: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    source: RecordSource = field(default_factory=RecordSource)
    evidence_text: str = ""
    confidence: float = 0.0

@dataclass
class RecordSet:
    location: str = ""
    records: list[Record] = field(default_factory=list)
```

- `from_llm` 은 LLM 이 준 dict 에서 위 필드를 관대하게 추출(누락→기본값, 타입오류→보정).
- `record_id` 미지정 시 `f"row-{source.row}"` 로 생성. row 도 없으면 인덱스 기반 폴백.

---

## 4. record_normalizer (`fact/record_normalizer.py`)

```python
def normalize_records(
    compact: dict,
    table_profile: TableProfile,
    column_schema: ColumnSchema,
    runner: LlmRunner,
    *,
    batch_rows: int = 30,
    store: Optional[ArtifactStore] = None,
) -> RecordSet:
    ...
```

### 4.1 데이터 행 선택
- `_primary_sheet(compact)`(F1 의 함수 로직 재사용) 로 시트 확보.
- `table_profile.header_structure.data_start_row` **이상**인 행만 데이터 행.
  `data_start_row` 가 없으면 `header_start_row + header_rows` 로 폴백.
- 데이터 행 0개 → `ValueError("정규화할 데이터 행이 없습니다")`.

### 4.2 배치 + carry-over
- 데이터 행을 `batch_rows` 씩 청크. 각 배치마다:
  `runner.complete_json(RECORD_SYSTEM, build_record_user(batch, column_schema, table_profile, carry))`.
- `carry` = `{category, subcategory}` — 직전 배치까지 **마지막으로 비어있지 않게 확정된**
  분류값. 첫 배치는 빈 carry. 배치 종료 후, 그 배치 record 들의 마지막 non-empty
  category/subcategory 로 carry 갱신 → 다음 배치 프롬프트에 "직전 분류" 로 주입.
- 배치 결과(`obj["records"]`)를 `Record.from_llm` 으로 변환해 순서대로 누적.

### 4.3 산출·저장
- 누적 record 로 `RecordSet(location=f"sheet={sheet_name}", records=[...])` 구성 →
  `store.save("records", record_set.to_dict())`.

### 4.4 source 좌표는 코드가 채움 (할루시네이션 방지)
- LLM 은 각 record 에 `source.row`(입력 "행 r:" 에서 본 값)만 책임진다.
- 코드가 `source.sheet`(시트명, 기지) 와 `source.cell_range` 를 채운다:
  `cell_range` = 그 행에서 **매핑된 열들의 최소~최대 열문자 + row**(예 `D4:I4`).
  매핑 열이 없으면 `row` 단독 표기. → 좌표는 코드가 결정해 추적 신뢰성 확보.

### 4.5 캐싱 (재실행 0비용 — 결정 #2)
- stage `records` 의 fingerprint =
  `fingerprint_for(json(데이터행 전체), json(column_schema), json(table_profile), RECORD_VERSION)`.
- `store.cached_or_compute("records", compute, fingerprint=fp)`. 같은 입력이면 LLM
  미호출(배치는 내부 구현 — 캐시는 시트 단위 산출물 1개).
- 예산은 기존 `LlmRunner.max_calls`(문서당) 로 통제. 배치가 많아 예산 초과 시
  `LlmBudgetExceeded`.

---

## 5. 프롬프트 요지 (`fact/prompts.py`)

- `RECORD_VERSION = "record-v1"` (입력 지문에 포함 — 프롬프트 변경 시 재계산).
- **RECORD_SYSTEM**: "당신은 표 데이터 정규화기다. 주어진 column_schema(열→역할)에
  따라 각 데이터 행을 record(JSON)로 변환한다. 규칙:
  - `display_name` 은 가장 구체적인 entity 이름(소분류 우선).
  - 병합/상위 분류가 빈 칸이면 '직전 분류' 컨텍스트로 채운다.
  - 소계·합계·빈 행은 record 로 만들지 말고 제외한다.
  - 값은 **셀에 있는 그대로** 옮긴다(단위 변환·식 해석 금지).
  - `evidence_text` 는 그 행에 실제 있는 문구만. 지어내지 않는다.
  - JSON 만 출력." + 출력 스키마(§2 의 records 배열, source 는 row 만) 명시.
- **build_record_user(batch, column_schema, table_profile, carry)**:
  - column_schema 를 "열 → 역할/필드명" 요약으로 제시.
  - 배치 행들을 `행 r: {열:값}` 형태로 나열(F1 `build_schema_user` 패턴).
  - `carry` 가 있으면 "직전까지 확정된 분류: category=…, subcategory=…" 한 줄 주입.

---

## 6. FactPipeline 연결 (`fact/pipeline.py` 수정)

```python
def _process_one(self, path):
    store = ArtifactStore(...)
    raw = self._extract(path); store.save("physical_raw", raw.to_dict())
    compact = self._compact(raw); store.save("compact_raw", compact)
    runner = LlmRunner(self._chat_client(), max_calls=self.fact.max_llm_calls_per_doc)
    profile = profile_document(compact, runner, store)                 # F1
    stages = ["physical_raw", "compact_raw", "document_profile"]
    if compact.get("doc_type") == "excel":
        tp, cs = induce_schema(compact, profile, runner, store)        # F1
        stages += ["table_profile", "column_schema"]
        normalize_records(                                            # F2
            compact, tp, cs, runner,
            batch_rows=self.fact.record_batch_rows, store=store,
        )
        stages += ["records"]
    return {... "stages": stages, "llm_calls": runner.calls}

def run(...):
    try:
        for path in docs: self._process_one(path)
        self._not_yet_implemented()   # F3(Fact Extractor)~ 미구현
    finally: close_all_office()
```

- 미구현 경계가 **F3** 로 이동(`_not_yet_implemented` 메시지도 갱신:
  "Fact Extractor~Comparator 는 Phase F3~F6 에서 구현됩니다").
- `--engine fact` CLI 안내 문구는 그대로 유효(artifacts 저장까지 동작).

---

## 7. config 변경 (`config.py` `FactConfig`)

```python
@dataclass
class FactConfig:
    artifacts_dir: str = "artifacts"
    save_artifacts: bool = True
    cache: bool = True
    max_llm_calls_per_doc: int = 50
    record_batch_rows: int = 30   # ✅ 신규: F2 행 배치 크기
```

- `config.example.yaml` 의 fact 섹션 주석에 `record_batch_rows` 설명 1줄 추가.

---

## 8. 테스트 계획 (전부 COM/네트워크 불필요)

- `test_fact_record_models.py`: `Record.from_llm` 키 누락/타입오류 관대, `to_dict↔from_dict`
  왕복 동일, `Entity.path` 자동 생성(빈 부분 제외), `QuantSpec.is_empty`.
- `test_fact_record_normalizer.py`(FakeLLM 배치별 응답 주입):
  - ① 35행 + batch=30 → **2배치 호출·병합**(record 합산, runner.calls=2).
  - ② `source.row` 정확, `source.cell_range` 를 **코드가 매핑 열 범위**로 채움.
  - ③ 캐시 히트 시 `runner.calls == 0`(재실행 0비용).
  - ④ 빈 배치(LLM 이 `{"records": []}`) 정상 처리.
  - ⑤ carry-over: 2번째 배치 프롬프트에 "직전 분류" 가 포함됨(주입된 user 캡처).
  - ⑥ 데이터 행 0개 → `ValueError`.
- `test_fact_pipeline_smoke.py`(갱신): fake chat → excel 입력이 `records` artifact 생성,
  word/ppt 는 `records` 미생성, 경계가 **F3** `NotImplementedError`.

**DoD**:
- [x] 신규 단위테스트 통과(record_models/record_normalizer/pipeline).
- [x] RAG 무회귀(엔진 기본 rag, 기존 스모크 유지).
- [ ] ollama 라이브 1회(`gemma4:12b`): `기준.xlsx` → `records.json` 생성, entity/quant_spec
      /source 가 schema 와 정합. 배치 분할(행 많을 때) 동작 확인.
- [x] `FACT_PIPELINE_PLAN.md` §9 F2 완료 표기 + 미구현 경계 F3 갱신.

---

## 9. 리스크

| 항목 | 대응 |
|---|---|
| 배치 경계에서 상위 분류 끊김 | carry-over 컨텍스트 주입(§4.2). 한계 시 F4 검증에서 보정. |
| 소형 LLM 이 값을 가공(단위 변환 등) | 프롬프트에서 "그대로 옮기기" 강제 + F4 source 검증(evidence_text 대조)으로 적발. |
| 좌표 할루시네이션 | source.cell_range 는 코드가 계산(§4.4). LLM 은 row 만. |
| 큰 표 토큰/예산 초과 | 행 배치(기본 30) + `max_calls` 예산. 초과 시 `LlmBudgetExceeded` fail-fast. |
| 다중 시트/다중 표 | F2 는 primary 1개(F1 종속). 주의 로그. 후속 확장. |
| record 가 아닌 행(소계/빈행) | LLM 이 제외(빈 배열 허용). 과다 제외 시 confidence·evidence 로 추적. |

---

## 10. 결정 사항 (2026-06-30 확정)

| # | 질문 | 결정 | 근거/메모 |
|---|------|------|-----------|
| F2-1 | 행→record 조립 방식 | **LLM 정규화** | 계획 §4 Step3 방향 유지. 병합 분류·display_name 선정·비레코드 행 제외 등 의미 판단을 LLM 이 흡수. |
| F2-2 | LLM 호출 전략 | **행 배치(`record_batch_rows`=30)** | 토큰 한도 안전 + 예산 예측. 작은 표는 1회. 배치 경계는 carry-over 로 보완. |
| F2-3 | F2 대상 범위 | **Excel primary 시트 1개** | column_schema 종속. Word/PPT 는 F3 에서 블록→fact 직행(결정 #4·F1 선례). |
| F2-4 | entity 구조 | **`category`/`subcategory`/`display_name`(+path)** | `semantic_roles.py` 어휘(category/subcategory)와 1:1. 계획의 mid/sub 를 subcategory 로 통합. |
| F2-5 | 단위/표현식 정규화 | **F2 비대상(F4/F5 로 미룸)** | F2 는 schema 역할대로 값 매핑만. 등가/검증은 Validator, 교차비교는 Comparator. |
| F2-6 | source 좌표 | **코드가 sheet/cell_range 채움, LLM 은 row 만** | 추적 신뢰성·할루시네이션 방지. |

> 의존 순서: F0 → F1 → **F2** → F3 → F4 → F5 → F6. F2 종료 시 미구현 경계는 F3 로 이동한다.
