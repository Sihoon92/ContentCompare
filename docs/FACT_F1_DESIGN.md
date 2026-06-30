# Phase F1 상세 설계 — Schema Induction (LLM)

> 작성일: 2026-06-29
> 상태: **설계→구현** — F1 의 파일·JSON 스키마·프롬프트·테스트를 고정한다.
> 상위 계획: [`FACT_PIPELINE_PLAN.md`](FACT_PIPELINE_PLAN.md) (§4 Step 1~2, §9 Phase F1)
> 선행: [`FACT_F0_DESIGN.md`](FACT_F0_DESIGN.md) (raw→compact→artifacts 토대 완료)

---

## 0. F1 범위와 목표

F1 은 **첫 LLM 단계**다. compact_raw(F0 산출물)를 입력으로 LLM 이 문서 구조를 추론한다.

### In-scope
1. **Document Profiler** (LLM) — `compact_raw.json` → `document_profile.json`. 문서 목적·주요 구조(표 후보)를 파악.
2. **Schema Inducer** (LLM) — `compact_raw.json` + `document_profile.json` → `table_profile.json` + `column_schema.json`. 헤더 구조 + row_grain + 컬럼 **semantic_role**.
3. **semantic_role 어휘 사전** — 정량 규격(하한/중심/상한·단위) 중심, 확장 가능(결정 #3).
4. **LlmRunner** — chat 호출 래퍼: JSON 파싱·재시도, **문서당 호출 예산**(결정 #2), ArtifactStore 캐싱(재실행 0비용).

### Out-of-scope (F2+)
- Record Normalizer(F2), Fact Extractor(F3), Validator/Repair(F4), Comparator(F5).
- Schema Inducer 는 **Excel 표 중심**(결정 #4: 엑셀 기준). Word/PPT 자유텍스트는 컬럼 스키마가 없으므로 F1 에서 schema induction 대상이 아니며, Document Profiler 만 돈다(Word/PPT 는 F3 에서 블록→fact 직행).

### 제약
- 🔒 RAG 무수정. 신규 코드는 `fact/` 에만 추가. `llm/factory.build_clients`·`readers/header_detect.py` 로직은 **참고/재사용**(수정 안 함).
- LLM 없이 테스트: 모든 단계는 **chat 클라이언트 주입**(FakeLLM)으로 단위테스트(CLAUDE.md 규칙).

---

## 1. 신규/변경 파일

| 파일 | 구분 | 내용 |
|---|---|---|
| `fact/semantic_roles.py` | ✅ 신규 | 역할 어휘(`SEMANTIC_ROLES`) + 동의어 사전 + `guess_role(header)` 코드 힌트 |
| `fact/schema_models.py` | ✅ 신규 | `DocumentProfile`/`MainStructure`/`TableProfile`/`HeaderStructure`/`RowGrain`/`ColumnSchema`/`ColumnSpec` |
| `fact/llm_stage.py` | ✅ 신규 | `LlmRunner`(complete_json/예산/재시도), `parse_json_object`, `LlmBudgetExceeded`, `fingerprint_for` |
| `fact/prompts.py` | ✅ 신규 | Profiler/Schema 프롬프트(system+user 빌더) |
| `fact/profiler.py` | ✅ 신규 | `profile_document(compact, runner, store=None)` |
| `fact/schema_inducer.py` | ✅ 신규 | `induce_schema(compact, profile, runner, store=None)` (header_detect 흡수+확장) |
| `fact/pipeline.py` | 🔧 수정 | F1 단계 연결(chat 주입/지연생성), 미구현 경계를 **F2** 로 이동 |
| `fact/__init__.py` | 🔧 수정 | 신규 공개 심볼 export |
| `tests/test_fact_semantic_roles.py` | ✅ 신규 | guess_role/사전 |
| `tests/test_fact_llm_stage.py` | ✅ 신규 | 예산/파싱/재시도 |
| `tests/test_fact_profiler.py` | ✅ 신규 | FakeLLM Profiler |
| `tests/test_fact_schema_inducer.py` | ✅ 신규 | FakeLLM Schema |
| `tests/test_fact_pipeline_smoke.py` | 🔧 수정 | fake chat 주입, F1 산출물 검증 |

> 신규 의존성 없음. chat 클라이언트는 기존 `llm/factory.build_clients(config)[0]` 재사용.

---

## 2. semantic_role 어휘 (결정 #3 — 정량 규격 중심, 확장 가능)

`fact/semantic_roles.py` 의 표준 역할(canonical). 양식이 달라도 **같은 역할로 매핑**되어 비교 가능해지는 핵심 장치.

| role | 의미 | 동의어(예) |
|---|---|---|
| `entity_name` | 비교 대상 항목명 | 항목, 항목명, 명칭, name, item, parameter, 특성 |
| `entity_category` | 대분류 | 대분류, category, 구분, 분류 |
| `entity_subcategory` | 중/소분류 | 중분류, 소분류, subcategory |
| `quantitative_lower_bound` | 하한 | 하한치, 하한, 최소, min, lower, LSL |
| `quantitative_target` | 중심/기준 | 중심치, 중심, 기준값, target, nominal, typ |
| `quantitative_upper_bound` | 상한 | 상한치, 상한, 최대, max, upper, USL |
| `unit` | 단위 | 단위, unit, uom |
| `qualitative_spec` | 정성 규격/조건 | 조건, 설명, 비고, remark, note, 정성 |
| `metadata` | 비교 비대상 메타 | 작성일, 버전, version, date, 작성자, no, 순번 |
| `unknown` | 미분류 | (매칭 실패 시) |

- `guess_role(header) -> str | None`: 헤더 문자열을 정규화(소문자·공백제거)해 우선순위대로 동의어 매칭. **LLM 의 결정을 대체하지 않고**, ① 프롬프트에 힌트로 주고 ② 결과 검증(F4)·폴백에 쓴다.
- 사전은 단순 매핑 테이블이라 도메인 확장이 쉽다.

---

## 3. 출력 JSON 스키마

좌표/행번호는 compact_raw 와 동일하게 **1-based 절대값**(엑셀 행 `r`, 열 문자).

### document_profile.json
```json
{ "doc_type": "excel",
  "main_purpose": "표준 규격 항목 리스트",
  "main_structures": [
    {"kind": "table", "location": "sheet=StandardList",
     "purpose": "규격 항목 목록", "row_grain_hint": "행 하나 = 규격 항목"}
  ],
  "confidence": 0.9 }
```

### table_profile.json
```json
{ "location": "sheet=StandardList",
  "header_structure": {"header_start_row": 3, "header_rows": 1, "data_start_row": 4, "header_depth": 1},
  "row_grain": {"description": "행 하나 = 규격 항목 1개", "primary_entity_columns": ["E"]} }
```

### column_schema.json
```json
{ "location": "sheet=StandardList",
  "columns": [
    {"column": "E", "field_name": "항목", "semantic_role": "entity_name", "data_type": "string", "raw_header": ["충전환경온도"]},
    {"column": "F", "field_name": "하한치", "semantic_role": "quantitative_lower_bound", "data_type": "number", "raw_header": ["하한치"]},
    {"column": "G", "field_name": "중심치", "semantic_role": "quantitative_target", "data_type": "number", "raw_header": ["중심치"]},
    {"column": "H", "field_name": "상한치", "semantic_role": "quantitative_upper_bound", "data_type": "number", "raw_header": ["상한치"]}
  ] }
```

> dataclass 는 `to_dict`/`from_dict`/`from_llm`(LLM 원본 dict 의 키 누락·오타에 관대) 제공. `from_llm` 은 알 수 없는 semantic_role 을 `unknown` 으로 보정한다.

---

## 4. LlmRunner (`fact/llm_stage.py`)

```python
class LlmBudgetExceeded(RuntimeError): ...

def parse_json_object(raw: str) -> dict | None:
    """raw 응답 → JSON dict. json.loads 실패 시 첫 {...} 블록 재시도(header_detect 패턴)."""

def fingerprint_for(*parts: str) -> str:
    """입력 지문(sha1 12자). compact 내용+모델명+프롬프트버전 등으로 캐시 무효화 판단."""

class LlmRunner:
    def __init__(self, chat, *, max_calls: int = 50, temperature: float = 0.0): ...
    calls: int  # 누적 호출 수
    def complete_json(self, system: str, user: str, *, retries: int = 1) -> dict:
        """예산 확인 → chat.complete → parse. 파싱 실패 시 교정 지시 덧붙여 1회 재시도.
        예산 초과 시 LlmBudgetExceeded, 끝내 파싱 실패 시 ValueError."""
```

- **호출 예산**(결정 #2): `calls >= max_calls` 면 `LlmBudgetExceeded`. 문서당 새 `LlmRunner` 를 만들어 문서 단위로 예산을 건다.
- **캐싱**(결정 #2): 단계 함수가 `store.cached_or_compute(stage, compute, fingerprint=fp)` 로 감싸 같은 입력이면 LLM 미호출(=`calls` 증가 0).

---

## 5. 단계 함수

### profiler.py
```python
def profile_document(compact: dict, runner: LlmRunner, store=None) -> DocumentProfile:
    # compute(): runner.complete_json(PROFILER_SYSTEM, build_profiler_user(compact)) → DocumentProfile.from_llm(...).to_dict()
    # store 있으면 cached_or_compute("document_profile", compute, fingerprint=fp)
```
- 모든 doc_type 대상. `build_profiler_user` 는 compact 를 토큰 한도 내로 미리보기(`_preview`, 기본 6000자 캡)로 직렬화.

### schema_inducer.py (Excel 표 중심)
```python
def induce_schema(compact, profile, runner, store=None) -> tuple[TableProfile, ColumnSchema]:
    sheet = _primary_sheet(compact)   # 데이터가 있는 첫 시트(F1: 1개 표). 없으면 ValueError.
    # 1회 LLM 호출로 {"table_profile":..., "column_schema":...} 동시 산출(비용 절감).
    # header_detect 흡수: 상위 행 + 행 신호(_signal) + guess_role 힌트를 프롬프트에 제공.
    # 캐시: table_profile/column_schema 두 산출물을 한 호출 결과에서 저장.
```
- **header_detect 흡수·확장**: `readers/header_detect.py` 의 행 신호(`_signal`/`_is_banner`)·배너 보정 아이디어를 가져오되, 출력은 header_start/rows 를 넘어 **row_grain + column semantic_role** 까지 확장한다(원본 모듈은 RAG 전용이라 수정하지 않고 로직만 이관).
- **다중 시트**: F1 은 primary 시트 1개만 처리(한계 기록). 다중 표는 후속.

---

## 6. 프롬프트 요지 (`fact/prompts.py`)

- **PROFILER_SYSTEM**: "문서 구조 분석가. compact_raw 를 보고 문서 목적과 주요 구조(표 후보)를 식별. 해석은 보수적으로, 확신 없으면 confidence 낮게. JSON 만 출력." + 출력 스키마 명시.
- **SCHEMA_SYSTEM**: "표 구조 분석가. (1) 헤더 시작행/행수(배너·메타행 제외 — header_detect 기준 재사용), (2) row_grain(행 하나의 의미·primary_entity_columns), (3) 컬럼별 semantic_role 을 아래 **어휘에서만** 고른다." + §2 어휘표 + "JSON 만 출력".
- user 빌더는 시트 미리보기(행 신호 포함)와 컬럼별 `guess_role` 힌트, profile 요약을 함께 제공.

---

## 7. FactPipeline 연결 (`fact/pipeline.py` 수정)

```python
def __init__(self, config, *, extractor=None, compactor=None, chat=None):
    ... self._chat = chat   # None 이면 지연 생성

def _chat_client(self):
    if self._chat is None:
        from ..llm.factory import build_clients
        self._chat, _ = build_clients(self.config)   # chat 만 사용
    return self._chat

def _process_one(self, path):
    store = ArtifactStore(...)
    raw = self._extract(path); store.save("physical_raw", raw.to_dict())
    compact = self._compact(raw); store.save("compact_raw", compact)
    runner = LlmRunner(self._chat_client(), max_calls=self.fact.max_llm_calls_per_doc)
    profile = profile_document(compact, runner, store)          # F1
    if compact.get("doc_type") == "excel":
        induce_schema(compact, profile, runner, store)          # F1 (Excel)
    return {...}

def run(...):
    try:
        for path in docs: self._process_one(path)
        self._not_yet_implemented()   # F2(Record Normalizer)~ 미구현
    finally: close_all_office()
```
- 미구현 경계가 **F2** 로 이동(메시지도 갱신). `--engine fact` CLI 안내 문구는 그대로 유효.

---

## 8. 테스트 계획 (전부 COM/네트워크 불필요)

- `test_fact_semantic_roles.py`: `guess_role("하한치")==quantitative_lower_bound`, 영문(`Max`→upper), 미매칭→None, 역할 집합 무결성.
- `test_fact_llm_stage.py`: `parse_json_object`(순수/코드펜스/잡음 혼합), 예산 초과 `LlmBudgetExceeded`, 파싱 실패 1회 재시도 후 성공/실패, `calls` 증가, `fingerprint_for` 결정성.
- `test_fact_profiler.py`: FakeLLM 이 profile JSON 반환 → `DocumentProfile` 필드, 캐시 히트 시 LLM 미호출(runner.calls), 알 수 없는 필드 관대 처리.
- `test_fact_schema_inducer.py`: FakeLLM 이 table_profile+column_schema 반환 → 헤더/row_grain/semantic_role 파싱, 잘못된 role→unknown 보정, 두 artifacts 저장, 캐시 히트.
- `test_fact_pipeline_smoke.py`(갱신): fake chat 주입 → excel 입력 시 `document_profile/table_profile/column_schema` artifacts 생성, word/ppt 는 `document_profile` 만, F2 경계 `NotImplementedError`.

**DoD** (2026-06-29 달성):
- [x] 신규 단위테스트 통과(semantic_roles/schema_models/llm_stage/profiler/schema_inducer/pipeline). 전체 242 passed(기존 ONNX 1건은 F0 이전 버그, 무관).
- [x] RAG 무회귀(엔진 기본 rag, 기존 스모크 유지).
- [x] ollama 라이브 1회(`gemma4:12b`): `기준.xlsx` → document_profile(목적/구조), table_profile(헤더행=1,데이터행=2), column_schema(제품명→entity_name, 연도→metadata, 매출액/직원수→quantitative_target). semantic_role 이 어휘 내에서만 선택됨을 확인.
- [x] `FACT_PIPELINE_PLAN.md` §9 F1 완료 표기.

---

## 9. 리스크

| 항목 | 대응 |
|---|---|
| 소형 LLM JSON 신뢰도 | parse 재시도 + (F4)검증/repair. F1 은 fail-fast + 캐시로 재현. |
| 다중 시트/다중 표 | F1 은 primary 1개. 후속 확장(주의 로그). |
| semantic_role 누락 도메인 | 사전 확장 테이블. unknown 으로 안전 강등. |
| Word/PPT 스키마 | F1 비대상(profiler 만). F3 에서 블록→fact. |
